"""Build two-hop graphs and supervise the authenticated relay core."""
import asyncio
import contextlib
import json
import os
import secrets
import socket
import time
import tempfile
from urllib.parse import quote, urlsplit

import httpx
import yaml
from sqlalchemy import select

from app.mihomo import TEST_URL, binary_path, free_port
from app.models import Chain, Entry, Exit, Subscription, ProxyCheck
from app.security import digest


class ChainError(ValueError):
    pass


class ChainManager:
    def __init__(self, service):
        self.service = service
        self.process = None
        self.task = None
        self.lock = asyncio.Lock()
        self.signature = None
        self.status = {}
        self.controller = None
        self.secret = secrets.token_urlsafe(32)
        self.config_file = None
        self.entry_labels = {}

    def host(self, chain):
        return chain.public_host or urlsplit(self.service.settings.public_base_url).hostname or "localhost"

    def candidates(self, entry, *, check_enabled=True):
        with self.service.db() as db:
            sub = db.get(Subscription, entry.subscription_id)
            if not sub or not sub.enabled or (check_enabled and not entry.enabled):
                return []
            rows = self.service._subscription_rows(sub, time.time())
            self.service._apply_checks(rows, time.time())
            rows = self.service._filter(rows, sub.exclude_failed, sub.max_latency, sub.max_per_country, sub.dedupe)
        from app.service import proxy_key
        seen, result = set(), []
        selected = set(entry.node_keys or ([entry.node_key] if entry.node_key else []))
        for row in rows:
            key = proxy_key(row["proxy"])
            if key in seen or (entry.mode == "fixed" and key not in selected):
                continue
            seen.add(key)
            result.append((key, row))
        return result

    def build(self, chain):
        with self.service.db() as db:
            entry, exit = db.get(Entry, chain.entry_id), db.get(Exit, chain.exit_id)
        if not chain.enabled or not entry or not exit or not entry.enabled or not exit.enabled:
            raise ChainError("链路、入口或出口已停用")
        candidates = self.candidates(entry)
        if not candidates:
            raise ChainError("所选入口节点均已失效" if entry.mode == "fixed" else "入口订阅没有符合条件的普通节点")
        outbound = json.loads(self.service.secrets.decrypt(exit.config_cipher))
        proxies, terminals = [], []
        for key, row in candidates:
            tag = f"chain-{chain.id}-{key}"
            first = dict(row["proxy"], name=f"{tag}-in")
            last = dict(outbound, name=f"{tag}-out", **{"dialer-proxy": first["name"], "udp": False})
            proxies.extend([first, last])
            self.entry_labels[last["name"]] = row["proxy"]["name"]
            terminals.append(last["name"])
        group = {"name": f"链路 · {chain.name} · {chain.id[:8]}", "type": "url-test",
                 "proxies": terminals, "url": TEST_URL, "interval": 300,
                 "tolerance": 0 if entry.mode == "fixed" else 100, "lazy": False}
        return proxies, group

    async def refresh(self, ids, force=False):
        with self.service.db() as db:
            chains = list(db.scalars(select(Chain).where(Chain.id.in_(ids))))
            entries = [db.get(Entry, c.entry_id) for c in chains]
            subs = [db.get(Subscription, e.subscription_id) for e in entries if e]
        accounts = set(i for s in subs if s and s.enabled for i in s.account_ids)
        sources = set(i for s in subs if s and s.enabled for i in s.source_ids)
        if force:
            await asyncio.gather(*(self.service.ensure_refresh(i) for i in accounts),
                                 *(self.service._refresh_source_safe(i) for i in sources))
        else:
            await self.service._refresh_due(list(accounts), list(sources), time.time())

    async def export(self, ids, native, exclude_failed=False, max_latency=None):
        await self.refresh(ids)
        proxies, groups, targets = [], [], []
        with self.service.db() as db:
            chains = list(db.scalars(select(Chain).where(Chain.id.in_(ids), Chain.enabled.is_(True))))
        if not native and chains:
            await self.sync()
        details = {r["chain_id"]: r for r in self.details(ids)}
        for chain in chains:
            try:
                detail = details.get(chain.id, {})
                if exclude_failed and detail.get("reachable") is False:
                    continue
                if max_latency and detail.get("latency") is not None and detail["latency"] > max_latency:
                    continue
                if native:
                    nodes, group = self.build(chain)
                    proxies.extend(nodes)
                    groups.append(group)
                    targets.append(group["name"])
                elif chain.relay_enabled and self.status.get(chain.id, {}).get("state") == "running":
                    name = f"链路 · {chain.name} · {chain.id[:8]}"
                    proxies.append({"name": name, "type": "http", "server": self.host(chain),
                                    "port": chain.listen_port, "username": chain.username,
                                    "password": self.service.secrets.decrypt(chain.password_cipher), "udp": False})
                    targets.append(name)
            except ChainError:
                continue
        return proxies, groups, targets

    async def _request(self, path, method="GET", **kwargs):
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.request(method, f"http://127.0.0.1:{self.controller}{path}",
                                            headers={"Authorization": f"Bearer {self.secret}"}, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}

    async def _stop_process(self):
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
            await self.process.wait()
        self.process = None
        if self.config_file:
            self.config_file.unlink(missing_ok=True)
            self.config_file = None

    async def sync(self):
        async with self.lock:
            with self.service.db() as db:
                chains = list(db.scalars(select(Chain)))
            proxies, groups, listeners, desired = [], [], [], {}
            for chain in chains:
                if not chain.enabled or not chain.relay_enabled:
                    desired[chain.id] = {"state": "disabled"}
                    continue
                try:
                    nodes, group = self.build(chain)
                    proxies.extend(nodes)
                    groups.append(group)
                    listeners.append({"name": chain.id, "type": "mixed", "listen": "0.0.0.0",
                                      "port": chain.listen_port, "udp": False, "proxy": group["name"],
                                      "users": [{"username": chain.username,
                                                 "password": self.service.secrets.decrypt(chain.password_cipher)}]})
                    desired[chain.id] = {"state": "running", "group": group["name"]}
                except ChainError as exc:
                    desired[chain.id] = {"state": "error", "error": str(exc)}
            config = {"mixed-port": 0, "mode": "rule", "log-level": "silent", "ipv6": False,
                      "proxies": proxies, "proxy-groups": groups, "listeners": listeners,
                      "rules": ["MATCH,REJECT"]}
            signature = digest(json.dumps(config, sort_keys=True))
            if signature == self.signature and self.process and self.process.returncode is None:
                self.status = desired
                return
            # Rebuild before stopping the old core; revoked routes never survive a failed apply.
            await self._stop_process()
            self.status = desired
            self.signature = None
            if not listeners:
                return
            binary = binary_path()
            if not binary:
                for listener in listeners:
                    self.status[listener["name"]] = {"state": "error", "error": "缺少 Mihomo 内核"}
                return
            self.controller = free_port()
            config.update({"external-controller": f"127.0.0.1:{self.controller}", "secret": self.secret})
            directory = self.service.settings.data_dir / "relay"
            directory.mkdir(parents=True, exist_ok=True)
            self.config_file = directory / f"runtime-{secrets.token_hex(8)}.yaml"
            descriptor = os.open(self.config_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                yaml.safe_dump(config, handle, allow_unicode=True)
            try:
                check = await asyncio.create_subprocess_exec(binary, "-t", "-d", str(directory), "-f", str(self.config_file),
                                                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    code = await asyncio.wait_for(check.wait(), 15)
                finally:
                    if check.returncode is None:
                        check.kill()
                        await check.wait()
                if code:
                    raise ChainError("Mihomo 配置校验失败")
                # Check occupied ports before launch; the controller alone does not prove listeners started.
                for listener in listeners:
                    with socket.socket() as sock:
                        sock.bind(("0.0.0.0", listener["port"]))
                self.process = await asyncio.create_subprocess_exec(binary, "-d", str(directory), "-f", str(self.config_file),
                                                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                for _ in range(50):
                    try:
                        await self._request("/version", timeout=1)
                        self.signature = signature
                        return
                    except (httpx.HTTPError, OSError):
                        if self.process.returncode is not None:
                            break
                        await asyncio.sleep(.1)
                raise ChainError("Mihomo 代理监听启动失败")
            except (OSError, TimeoutError, ChainError):
                await self._stop_process()
                for listener in listeners:
                    self.status[listener["name"]] = {"state": "error", "error": "代理启动失败，请检查内核与端口占用"}

    async def inspect(self):
        if self.process and self.process.returncode is None:
            try:
                data = (await self._request("/proxies", timeout=2)).get("proxies", {})
                for status in self.status.values():
                    group = data.get(status.get("group"), {})
                    current = group.get("now")
                    history = data.get(current, {}).get("history", [])
                    status.update(current_entry=self.entry_labels.get(current, current),
                                  latency=history[-1].get("delay") if history else None)
            except httpx.HTTPError:
                pass
        return self.status

    async def _measure(self, proxies, group):
        """Run one bounded URL test and always tear down its private core."""
        binary = binary_path()
        if not binary:
            raise ChainError("缺少 Mihomo 内核，无法执行真实代理测速")
        controller, secret = free_port(), secrets.token_urlsafe(32)
        with tempfile.TemporaryDirectory(prefix="chain-probe-", dir=self.service.settings.data_dir) as directory:
            from pathlib import Path
            config = Path(directory) / "probe.yaml"
            descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                yaml.safe_dump({"mixed-port": 0, "external-controller": f"127.0.0.1:{controller}",
                                "secret": secret, "proxies": proxies, "proxy-groups": [group],
                                "rules": ["MATCH,REJECT"], "log-level": "silent"}, handle)
            process = await asyncio.create_subprocess_exec(binary, "-d", directory, "-f", str(config),
                                                           stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                async with httpx.AsyncClient(trust_env=False, headers={"Authorization": f"Bearer {secret}"}) as client:
                    base = f"http://127.0.0.1:{controller}"
                    for _ in range(50):
                        if process.returncode is not None:
                            raise ChainError("测速内核启动失败，请检查代理配置")
                        try:
                            response = await client.get(base + "/version", timeout=1)
                            response.raise_for_status()
                            break
                        except httpx.HTTPError:
                            await asyncio.sleep(.1)
                    else:
                        raise ChainError("测速内核启动超时")
                    response = await client.get(base + f"/group/{quote(group['name'], safe='')}/delay",
                                                params={"url": TEST_URL, "timeout": 5000}, timeout=30)
                    response.raise_for_status()
                    return {name: delay for name, delay in response.json().items()
                            if name in group["proxies"] and isinstance(delay, (int, float))
                            and not isinstance(delay, bool) and delay > 0}
            finally:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                await process.wait()

    def result_key(self, resource):
        """Fingerprint configuration, independent of transient health and display names."""
        from app.service import proxy_key
        if isinstance(resource, Exit):
            config = json.loads(self.service.secrets.decrypt(resource.config_cipher))
            for key in ("tls", "skip-cert-verify", "username", "password"):
                if not config.get(key):
                    config.pop(key, None)
            return digest(f"exit-result:{resource.enabled}:{proxy_key(config)}")
        with self.service.db() as db:
            entry = db.get(Entry, resource.entry_id)
            outbound = db.get(Exit, resource.exit_id)
            sub = db.get(Subscription, entry.subscription_id) if entry else None
            rows = self.service._subscription_rows(sub, time.time()) if sub else []
        selected = sorted(entry.node_keys or ([entry.node_key] if entry.node_key else [])) if entry else []
        keys = sorted({proxy_key(r["proxy"]) for r in rows})
        if entry and entry.mode == "fixed":
            keys = [key for key in keys if key in selected]
        config = [resource.enabled, resource.entry_id, resource.exit_id,
                  self.result_key(outbound) if outbound else None,
                  [entry.enabled, entry.mode, selected, entry.subscription_id] if entry else None,
                  [sub.enabled, sub.exclude_failed, sub.max_latency, sub.max_per_country, sub.dedupe] if sub else None,
                  keys]
        return digest("chain-result:" + json.dumps(config, sort_keys=True))

    def cached_result(self, resource):
        with self.service.db() as db:
            check = db.get(ProxyCheck, self.result_key(resource))
            if check:
                return check.result
            # Preserve measurements made before complete feedback was persisted.
            if isinstance(resource, Chain):
                try:
                    proxies, _ = self.build(resource)
                except ChainError:
                    return None
                previous = db.get(ProxyCheck, self.check_key(proxies))
                if previous:
                    return {"reachable": previous.reachable, "latency": previous.latency,
                            "tested_at": previous.tested_at, "entry": None,
                            "error": None if previous.reachable else "上次链路检测失败，请重新检测"}
            return None

    def save_result(self, key, result, method):
        if key:
            with self.service.db.begin() as db:
                db.merge(ProxyCheck(key=key, reachable=result["reachable"], latency=result["latency"],
                                    method=method, tested_at=result["tested_at"], result=dict(result)))

    async def probe(self, chain_id, job_id=None):
        result = {"reachable": False, "latency": None, "entry": None, "error": None}
        proxies = None
        cache_key = None
        with self.service.db() as db:
            chain = db.get(Chain, chain_id)
        if chain:
            cache_key = self.result_key(chain)
        try:
            async with asyncio.timeout(40):
                await self.refresh([chain_id])
                with self.service.db() as db:
                    chain = db.get(Chain, chain_id)
                if not chain:
                    raise ChainError("链路不存在")
                cache_key = self.result_key(chain)
                proxies, group = self.build(chain)
                delays = await self._measure(proxies, group)
                if not delays:
                    raise ChainError("所有候选链路均连接失败或超时，请检查入口和出口")
                fastest = min(delays, key=delays.get)
                result.update(reachable=True, latency=delays[fastest], entry=self.entry_labels.get(fastest))
        except ChainError as exc:
            result["error"] = str(exc)
        except TimeoutError:
            result["error"] = "检测超时，请稍后重试"
        except (httpx.HTTPError, OSError):
            result["error"] = "链路检测失败，请检查入口、出口或网络连接"
        result["tested_at"] = time.time()
        self.save_result(cache_key, result, "chain")
        if proxies is not None:
            with self.service.db.begin() as db:
                db.merge(ProxyCheck(key=self.check_key(proxies), reachable=result["reachable"],
                                    latency=result["latency"], method="chain", tested_at=result["tested_at"]))
        if job_id:
            self.service.progress(job_id, result["error"])
            self.service.finish(job_id)
        return result

    async def probe_exit(self, exit_id):
        result = {"reachable": False, "latency": None, "error": None}
        cache_key = None
        try:
            async with asyncio.timeout(40):
                with self.service.db() as db:
                    exit = db.get(Exit, exit_id)
                if exit:
                    cache_key = self.result_key(exit)
                if not exit or not exit.enabled:
                    raise ChainError("出口不存在或已停用")
                outbound = json.loads(self.service.secrets.decrypt(exit.config_cipher))
                outbound["name"] = "exit-probe"
                group = {"name": "exit-test", "type": "url-test", "proxies": [outbound["name"]],
                         "url": TEST_URL, "interval": 300, "lazy": True}
                delays = await self._measure([outbound], group)
                if not delays:
                    raise ChainError("出口直连失败或超时；若出口必须经入口访问，请检测对应链路")
                result.update(reachable=True, latency=min(delays.values()))
        except ChainError as exc:
            result["error"] = str(exc)
        except TimeoutError:
            result["error"] = "出口测速超时，请稍后重试"
        except (httpx.HTTPError, OSError):
            result["error"] = "出口测速失败，请检查地址、认证信息或网络连接"
        result["tested_at"] = time.time()
        self.save_result(cache_key, result, "exit")
        return result

    @staticmethod
    def check_key(proxies):
        return digest("chain:" + json.dumps(proxies, sort_keys=True))

    def details(self, ids):
        result = []
        with self.service.db() as db:
            chains = list(db.scalars(select(Chain).where(Chain.id.in_(ids))))
            for chain in chains:
                try:
                    proxies, group = self.build(chain)
                    key = self.check_key(proxies)
                    check = db.get(ProxyCheck, key)
                    if check and time.time() - check.tested_at > 1800:
                        check = None
                    error = None
                except ChainError as exc:
                    key, check, error = digest(chain.id), None, str(exc)
                result.append({"chain_id": chain.id, "key": key, "name": chain.name, "type": "chain", "server": self.host(chain),
                               "port": chain.listen_port, "group": "代理链路", "origin": "链路 · " + chain.name,
                               "country": "UN", "country_label": "未知", "reachable": check.reachable if check else None,
                               "latency": check.latency if check else None, "tested_at": check.tested_at if check else None,
                               "method": "chain", "included": not error, "reason": error,
                               "last_probe": self.cached_result(chain)})
        return result

    async def run(self):
        delay = 15
        while True:
            try:
                with self.service.db() as db:
                    ids = list(db.scalars(select(Chain.id).where(Chain.enabled.is_(True))))
                await self.refresh(ids)
                await self.sync()
                delay = min(60, delay * 2) if any(s.get("state") == "error" for s in self.status.values()) else 15
            except Exception:
                delay = min(60, delay * 2)
            await asyncio.sleep(delay)

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self._stop_process()
