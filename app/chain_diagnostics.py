"""Isolated, cancellable path diagnostics and bounded server-side history."""
import asyncio
import contextlib
import copy
import ipaddress
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml
from sqlalchemy import delete, select

from app.chain_graph import compile_graph, effective_paths, graph_fingerprint
from app.mihomo import binary_path, free_port
from app.models import Chain, ChainEvent, ChainSample, Job

IP_URL = "https://api.ipify.org?format=json"


@contextlib.asynccontextmanager
async def isolated_core(service, proxies, terminals):
    from app.chains import ChainError
    binary = binary_path()
    if not binary:
        raise ChainError("缺少 Mihomo 内核，无法检测真实路径")
    controller, listener = free_port(), free_port()
    while listener == controller:
        listener = free_port()
    secret = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="chain-diagnostic-", dir=service.settings.data_dir) as directory:
        file = Path(directory) / "config.yaml"
        config = {"mixed-port": 0, "mode": "rule", "ipv6": False, "log-level": "silent",
                  "external-controller": f"127.0.0.1:{controller}", "secret": secret, "proxies": proxies,
                  "proxy-groups": [{"name": "diagnostic-selected", "type": "select", "proxies": terminals}],
                  "listeners": [{"name": "diagnostic", "type": "mixed", "listen": "127.0.0.1",
                                 "port": listener, "udp": False, "proxy": "diagnostic-selected"}],
                  "rules": ["MATCH,REJECT"]}
        descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, allow_unicode=True)
        process = await asyncio.create_subprocess_exec(binary, "-d", directory, "-f", str(file),
                                                       stdout=asyncio.subprocess.DEVNULL,
                                                       stderr=asyncio.subprocess.DEVNULL)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{controller}", trust_env=False,
                                         headers={"Authorization": f"Bearer {secret}"}) as client:
                for _ in range(50):
                    if process.returncode is not None:
                        raise ChainError("检测内核拒绝了配置，请检查节点协议及参数")
                    try:
                        response = await client.get("/group/diagnostic-selected", timeout=1)
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        await asyncio.sleep(.1)
                else:
                    raise ChainError("检测内核启动超时")
                yield client, listener
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()


class ChainDiagnostics:
    def __init__(self, manager):
        self.manager, self.service = manager, manager.service
        self.limit = asyncio.Semaphore(2)
        self.tasks = {}
        self.active_chains = set()
        self.next_sample = {}
        self.observed = {}
        self.last_cleanup = 0

    async def measure(self, chain, *, timeout=120, progress=None, source="manual", current_path=None):
        from app.chains import ChainError, ChainManager, TEST_URL
        result = {"reachable": None, "latency": None, "error": None, "entry": None,
                  "entry_key": None, "source": source, "revision": chain.revision,
                  "steps": [], "paths": [], "exit_ip": None, "ip_error": None}

        def step(label, state, **values):
            result["steps"].append(dict(label=label, state=state, **values))
            if progress:
                progress(result)

        try:
            async with asyncio.timeout(timeout), self.limit:
                # A private manager keeps draft labels out of the running relay's status map.
                compiler = ChainManager(self.service)
                draft = copy.copy(chain)
                draft.enabled = True
                draft.paths = copy.deepcopy(effective_paths(chain))
                proxies, group = compile_graph(compiler, draft)
                step("配置与节点引用", "passed")
                if group.get("_errors"):
                    result["path_errors"] = group["_errors"]
                terminals = list(compiler.path_details)
                # Monitoring freezes the observed route; it never chooses a different healthy route silently.
                if current_path:
                    matches = [name for name in terminals if compiler.path_details[name] == current_path]
                    if not matches:
                        raise ChainError("实际路径已变化，等待下一次采样")
                    terminals = matches
                async with isolated_core(self.service, proxies, terminals) as (client, listener):
                    step("隔离检测内核", "passed")
                    response = await client.get("/group/diagnostic-selected/delay",
                                                params={"url": TEST_URL, "timeout": 5000}, timeout=60)
                    if response.status_code != 504:
                        response.raise_for_status()
                    values = response.json() if response.status_code != 504 else {}
                    delays = {k: v for k, v in values.items()
                              if k in terminals and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
                    selected = None
                    for index, path in enumerate(draft.paths):
                        names = [n for n in terminals if compiler.path_details[n]["path_index"] == index]
                        available = [n for n in names if n in delays]
                        winner = ((available[0] if path["strategy"] in {"fixed", "fallback"}
                                   else min(available, key=delays.get)) if available else None)
                        result["paths"].append({"name": path["name"], "path_index": index,
                                                "reachable": bool(winner) if names else None,
                                                "latency": delays.get(winner), "candidate_count": len(names)})
                        if selected is None and winner:
                            selected = winner
                    result.update(reachable=bool(selected), latency=delays.get(selected))
                    step("完整路径", "passed" if selected else "failed", latency=result["latency"])
                    chosen = selected or terminals[0]
                    result.update(path=compiler.path_details[chosen], entry=compiler.entry_labels[chosen],
                                  entry_key=compiler.entry_keys[chosen])
                    by_name = {p["name"]: p for p in proxies}
                    prefix, name = [], chosen
                    while name:
                        prefix.append(name)
                        name = by_name[name].get("dialer-proxy")
                    for index, name in enumerate(reversed(prefix)):
                        try:
                            response = await client.get(f"/proxies/{quote(name, safe='')}/delay",
                                                        params={"url": TEST_URL, "timeout": 5000}, timeout=7)
                            response.raise_for_status()
                            latency = response.json().get("delay")
                            if not isinstance(latency, (float, int)) or isinstance(latency, bool) or latency <= 0:
                                raise ChainError("前缀检测无有效结果")
                            step(f"经过前 {index + 1} 跳", "passed", latency=latency)
                        except (httpx.HTTPError, ChainError):
                            step(f"经过前 {index + 1} 跳", "failed", error="累计路径检测失败，疑似在此阶段受阻")
                    if selected:
                        response = await client.put("/proxies/diagnostic-selected", json={"name": selected}, timeout=3)
                        response.raise_for_status()
                        try:
                            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{listener}", trust_env=False,
                                                         timeout=10) as transport:
                                async with transport.stream("GET", IP_URL) as response:
                                    response.raise_for_status()
                                    content = b""
                                    async for chunk in response.aiter_bytes():
                                        content += chunk
                                        if len(content) > 4096:
                                            raise ValueError("IP 响应过大")
                            result["exit_ip"] = str(ipaddress.ip_address(json.loads(content)["ip"]))
                            step("出口 IP", "passed", value=result["exit_ip"])
                        except (httpx.HTTPError, ValueError, KeyError):
                            result["ip_error"] = "出口 IP 查询失败，不影响链路可用性结论"
                            step("出口 IP", "unknown", error=result["ip_error"])
                    else:
                        result["error"] = "所有已检测路径连接失败或超时，请查看累计路径结果"
        except TimeoutError:
            result["error"] = "检测达到时间预算，尚未完成的阶段未测"
            step("剩余检测", "unknown")
        except ChainError as exc:
            result["error"] = str(exc)
            result["reachable"] = False
            step("配置或内核", "failed", error=str(exc))
        except (httpx.HTTPError, OSError, ValueError):
            result["error"] = "检测服务暂不可用，请重试"
            step("剩余检测", "unknown")
        result["tested_at"] = time.time()
        return result

    def start(self, chain, *, source="manual"):
        if chain.id != "draft" and chain.id in self.active_chains:
            raise ValueError("该链路已有检测任务，请等待完成或取消")
        job_id = self.service.new_job("chain_diagnostic", 1)
        if chain.id != "draft":
            self.active_chains.add(chain.id)
        task = self.service.spawn(self.run(job_id, chain, source))
        self.tasks[job_id] = task
        def finished(_):
            self.tasks.pop(job_id, None)
            self.active_chains.discard(chain.id)
        task.add_done_callback(finished)
        return job_id

    def update_job(self, job_id, result, status="running"):
        with self.service.db.begin() as db:
            job = db.get(Job, job_id)
            if job:
                job.status, job.result = status, copy.deepcopy(result)
                job.completed = int(status not in {"pending", "running"})
                job.errors = [result["error"]] if result.get("error") else []

    async def run(self, job_id, chain, source):
        fingerprint = graph_fingerprint(self.manager, chain)
        try:
            self.update_job(job_id, {"steps": [], "source": source})
            current = self.manager.status.get(chain.id, {}).get("current_path") if source == "monitor" else None
            result = await self.measure(chain, progress=lambda r: self.update_job(job_id, r),
                                        source=source, current_path=current)
            result["fingerprint"] = fingerprint
            if chain.id != "draft":
                self.persist(chain, result, fingerprint)
            self.update_job(job_id, result, "done")
        except asyncio.CancelledError:
            with self.service.db() as db:
                job = db.get(Job, job_id)
                result = copy.deepcopy(job.result) if job and job.result else {}
            result.update(error="检测已取消，未完成的阶段未测", tested_at=time.time())
            self.update_job(job_id, result, "cancelled")
            raise
        except Exception:
            self.update_job(job_id, {"error": "检测任务中断，请重试", "tested_at": time.time()}, "failed")
        finally:
            self.active_chains.discard(chain.id)

    def persist(self, chain, result, fingerprint):
        with self.service.db.begin() as db:
            current = db.get(Chain, chain.id)
            if not current:
                return
            previous = db.scalar(select(ChainSample).where(ChainSample.chain_id == chain.id,
                                                          ChainSample.fingerprint == fingerprint)
                                 .order_by(ChainSample.tested_at.desc()).limit(1))
            db.add(ChainSample(chain_id=chain.id, fingerprint=fingerprint, result=result,
                               tested_at=result["tested_at"]))
            old = previous.result if previous else {}
            reachable = result.get("reachable")
            if reachable is not None and (not previous or old.get("reachable") != reachable):
                db.add(ChainEvent(chain_id=chain.id, kind="recovered" if reachable else "failure",
                                  detail={"revision": chain.revision, "error": result.get("error")}))
            if result.get("exit_ip") and old.get("exit_ip") and result["exit_ip"] != old["exit_ip"]:
                db.add(ChainEvent(chain_id=chain.id, kind="ip_changed",
                                  detail={"before": old["exit_ip"], "after": result["exit_ip"]}))
        if current.revision == chain.revision and graph_fingerprint(self.manager, current) == fingerprint:
            self.manager.save_result(self.manager.result_key(current), result, "chain")
            try:
                proxies, _ = self.manager.build(current)
                self.manager.save_result(self.manager.check_key(proxies), result, "chain")
            except ValueError:
                pass

    async def cancel(self, job_id):
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with self.service.db() as db:
                job = db.get(Job, job_id)
                pending = job and job.status in {"pending", "running"}
                result = copy.deepcopy(job.result or {}) if job else {}
            if pending:
                result.update(error="检测已取消，未完成的阶段未测", tested_at=time.time())
                self.update_job(job_id, result, "cancelled")

    async def tick(self):
        now = time.time()
        with self.service.db() as db:
            chains = list(db.scalars(select(Chain)))
        for chain in chains:
            status = self.manager.status.get(chain.id, {})
            snapshot = {"state": status.get("state"), "path": status.get("current_path"),
                        "entry_key": status.get("current_entry_key")}
            old = self.observed.get(chain.id)
            if old != snapshot and snapshot["path"] and old and old.get("path"):
                with self.service.db.begin() as db:
                    if db.get(Chain, chain.id):
                        db.add(ChainEvent(chain_id=chain.id, kind="path_changed", detail={"before": old, "after": snapshot}))
            self.observed[chain.id] = snapshot
            if chain.enabled and chain.monitor_enabled and chain.id not in self.active_chains:
                if now >= self.next_sample.get(chain.id, 0):
                    self.next_sample[chain.id] = now + chain.interval
                    self.start(chain, source="monitor")
        if now - self.last_cleanup >= 3600:
            self.last_cleanup = now
            with self.service.db.begin() as db:
                db.execute(delete(ChainSample).where(ChainSample.tested_at < now - 30 * 86400))
                db.execute(delete(ChainEvent).where(ChainEvent.created_at < now - 30 * 86400))

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
