import asyncio
import contextlib
import ipaddress
import json
import re
import socket
import time

from sqlalchemy import delete, select, update

from app.models import Account, Job, LoginSession, Node, Preference
from app.pac import PacError, parse_pac
from app.security import digest, hint
from app.upstream import Gateway, InvalidToken, UpstreamError


class Service:
    def __init__(self, settings, db, secrets):
        self.settings, self.db, self.secrets = settings, db, secrets
        self.gateway = Gateway(settings)
        self.locks: dict[str, asyncio.Lock] = {}
        self.refreshes: dict[str, asyncio.Task] = {}
        self.tasks: set[asyncio.Task] = set()
        self.account_limit = asyncio.Semaphore(3)
        self.probe_limit = asyncio.Semaphore(8)
        self.probe_job_id = None
        self.scheduler = None

    def lock(self, account_id):
        return self.locks.setdefault(account_id, asyncio.Lock())

    def preferences(self):
        with self.db() as db:
            row = db.get(Preference, "general")
            return row.value if row else {"refresh_minutes": 30}

    async def start(self):
        with self.db.begin() as db:
            db.execute(update(Job).where(Job.status.in_(["pending", "running"]))
                       .values(status="interrupted", errors=["服务重启，任务已中断"]))
        if self.settings.scheduler_enabled:
            self.scheduler = asyncio.create_task(self.tick())

    async def stop(self):
        tasks = [*self.tasks, *self.refreshes.values()]
        if self.scheduler:
            tasks.append(self.scheduler)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.gateway.close()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def tick(self):
        while True:
            try:
                now = time.time()
                with self.db.begin() as db:
                    ids = list(db.scalars(select(Account.id).where(
                        Account.enabled.is_(True), Account.next_refresh <= now,
                        Account.status != "invalid")))
                    db.execute(delete(Job).where(Job.created_at < now - 7 * 86400,
                                                 Job.status.not_in(["pending", "running"])))
                    db.execute(delete(LoginSession).where(LoginSession.expires < now))
                ids = [i for i in ids if i not in self.refreshes]
                if ids:
                    job_id = self.new_job("refresh", len(ids))
                    self.spawn(self.refresh_job(job_id, ids))
            except Exception:
                # Never log upstream URLs or exception representations containing credentials.
                import logging
                logging.getLogger("ghelper").error("调度检查失败，下次检查将重试")
            await asyncio.sleep(15)

    def ensure_refresh(self, account_id):
        existing = self.refreshes.get(account_id)
        if existing and not existing.done():
            return existing
        task = asyncio.create_task(self.refresh(account_id))
        self.refreshes[account_id] = task
        task.add_done_callback(lambda _: self.refreshes.pop(account_id, None)
                               if self.refreshes.get(account_id) is task else None)
        return task

    def invalidate(self, account_id):
        with self.db.begin() as db:
            account = db.get(Account, account_id)
            if account:
                account.status = "invalid"
                account.token_cipher = ""
                account.error = "token 已失效，请重新导入"
                account.last_success = None
                db.execute(delete(Node).where(Node.account_id == account_id))

    def controls(self, account_id, response):
        if response.get("clear_token"):
            self.invalidate(account_id)
            raise InvalidToken("token 已失效，请重新导入")
        value = response.get("set_token")
        if value:
            if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
                raise UpstreamError("上游返回了无法识别的新 token")
            with self.db.begin() as db:
                account = db.get(Account, account_id)
                fingerprint = self.secrets.fingerprint(value)
                other = db.scalar(select(Account).where(Account.fingerprint == fingerprint,
                                                        Account.id != account_id))
                if other:
                    raise UpstreamError("轮换后的 token 与其他账号重复，请检查账号")
                account.token_cipher = self.secrets.encrypt(value)
                account.fingerprint, account.token_hint = fingerprint, hint(value)

    async def call(self, account_id, path, **kwargs):
        with self.db() as db:
            account = db.get(Account, account_id)
            token = self.secrets.decrypt(account.token_cipher)
            pk = account.pk
        try:
            result = await self.gateway.request(token, path, pk=pk, **kwargs)
        except InvalidToken:
            self.invalidate(account_id)
            raise
        self.controls(account_id, result)
        if result.get("error"):
            raise UpstreamError("上游拒绝请求，请检查账号状态")
        return result

    async def refresh(self, account_id):
        async with self.account_limit, self.lock(account_id):
            now = time.time()
            with self.db.begin() as db:
                account = db.get(Account, account_id)
                if not account or not account.enabled or account.status == "invalid":
                    return "账号已停用或凭证失效"
                account.last_attempt = now
            try:
                response = await self.call(account_id, "/session", body={"tester": {}})
                proxy = response.get("session", {}).get("proxy", {})
                pac = proxy.get("pac", {}).get("value", {}).get("pacScript", {}).get("data")
                nodes, degraded = parse_pac(pac)
                pk = proxy.get("pk")
                if not isinstance(pk, str) or not pk:
                    raise UpstreamError("上游缺少会话标识")
                with self.db.begin() as db:
                    account = db.get(Account, account_id)
                    old = {n.id: n for n in db.scalars(select(Node).where(Node.account_id == account_id))}
                    ids = set()
                    for node in nodes:
                        node_id = digest(f"{account_id}:{node.server}:{node.port}")
                        ids.add(node_id)
                        if node_id not in old:
                            db.add(Node(id=node_id, account_id=account_id, server=node.server,
                                        port=node.port, region=node.region))
                        elif account.pk != pk:
                            old[node_id].latency = old[node_id].reachable = old[node_id].tested_at = None
                    db.execute(delete(Node).where(Node.account_id == account_id, Node.id.not_in(ids)))
                    if account.pk != pk:
                        account.first_seen = now
                    account.pk, account.last_success = pk, time.time()
                    account.status, account.error, account.failures = "ready", None, 0
                    account.degraded = degraded
                    account.next_refresh = time.time() + self.preferences()["refresh_minutes"] * 60
                try:
                    info = await self.call(account_id, "/v3/account")
                    traffic = await self.call(account_id, "/v3/traffic", query={"type": "chrome"})
                    raw = info.get("account") or {}
                    safe = {k: v for k, v in raw.items()
                            if k in {"email", "is_vip", "vip_date", "vip_remaining", "level"}
                            and isinstance(v, (str, bool, int, float, type(None)))}
                    total = traffic.get("total")
                    safe["browser_traffic_total"] = total if isinstance(total, (int, float)) else None
                    with self.db.begin() as db:
                        account = db.get(Account, account_id)
                        account.info, account.info_error = safe, None
                except InvalidToken:
                    raise
                except Exception:
                    with self.db.begin() as db:
                        db.get(Account, account_id).info_error = "账号资料暂不可用，节点获取不受影响"
                return None
            except Exception as exc:
                error = str(exc) if isinstance(exc, (UpstreamError, PacError)) else "刷新失败，请稍后重试"
                # Only controlled messages escape this layer, never an upstream response/URL.
                if not isinstance(exc, (UpstreamError, PacError)):
                    error = "刷新失败，请稍后重试"
                with self.db.begin() as db:
                    account = db.get(Account, account_id)
                    if account:
                        if account.status != "invalid":
                            account.status = "error"
                        account.error = error
                        account.failures += 1
                        account.next_refresh = time.time() + min(1800, 60 * 2 ** min(account.failures, 5))
                return error

    def new_job(self, kind, total):
        with self.db.begin() as db:
            job = Job(kind=kind, total=total)
            db.add(job)
            db.flush()
            return job.id

    def progress(self, job_id, error=None):
        with self.db.begin() as db:
            job = db.get(Job, job_id)
            job.completed += 1
            if error:
                job.errors = [*job.errors, error]

    def finish(self, job_id):
        with self.db.begin() as db:
            job = db.get(Job, job_id)
            job.status = "partial" if job.errors else "completed"

    async def refresh_job(self, job_id, ids):
        with self.db.begin() as db:
            db.get(Job, job_id).status = "running"
        async def one(account_id):
            error = await asyncio.shield(self.ensure_refresh(account_id))
            self.progress(job_id, f"{account_id[:8]}：{error}" if error else None)
        await asyncio.gather(*(one(i) for i in ids))
        self.finish(job_id)

    async def export_rows(self, ids, regions=None, exclude_failed=False):
        now = time.time()
        with self.db() as db:
            accounts = list(db.scalars(select(Account).where(Account.id.in_(ids),
                Account.enabled.is_(True), Account.status != "invalid")))
            due = [a.id for a in accounts if (not a.last_success or now - a.last_success >=
                   self.preferences()["refresh_minutes"] * 60) and a.next_refresh <= now]
        if due:
            waiting = asyncio.gather(*(asyncio.shield(self.ensure_refresh(i)) for i in due))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(waiting, timeout=35)
        with self.db() as db:
            rows = db.execute(select(Node, Account).join(Account).where(
                Account.id.in_(ids), Account.enabled.is_(True), Account.status != "invalid",
                Account.last_success >= time.time() - 3600)).all()
            output = []
            for node, account in rows:
                if regions and node.region not in regions:
                    continue
                if exclude_failed and node.reachable is False and node.tested_at and node.tested_at >= time.time() - 1800:
                    continue
                output.append({"server": node.server, "port": node.port, "region": node.region,
                               "account_id": account.id, "account_name": account.name})
            return output

    async def probe_node(self, server, port):
        loop = asyncio.get_running_loop()
        process = None
        try:
            addresses = await asyncio.wait_for(loop.getaddrinfo(server, port, type=socket.SOCK_STREAM), 5)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return None
            address = addresses[0][4][0]
            resolved = f"[{address}]" if ":" in address else address
            process = await asyncio.create_subprocess_exec(
                "curl", "--silent", "--output", "NUL" if __import__("os").name == "nt" else "/dev/null",
                "--proxy", f"https://{server}:{port}", "--proxy-insecure", "--noproxy", "",
                "--resolve", f"{server}:{port}:{resolved}", "--max-time", "8",
                "--write-out", "%{json}", "https://www.gstatic.com/generate_204",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(process.communicate(), 10)
            result = json.loads(stdout)
            if process.returncode == 0 and result.get("http_code") == 204:
                return round(result["time_total"] * 1000, 1)
        except (OSError, ValueError, TimeoutError):
            pass
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
        return None

    async def probe_job(self, job_id, ids):
        with self.db.begin() as db:
            db.get(Job, job_id).status = "running"
        async def one(node_id):
            async with self.probe_limit:
                with self.db() as db:
                    node = db.get(Node, node_id)
                    if not node:
                        self.progress(job_id, "节点已更新，跳过旧节点")
                        return
                    server, port = node.server, node.port
                    account = db.get(Account, node.account_id)
                    snapshot_pk = account.pk
                latency = await self.probe_node(server, port)
                with self.db.begin() as db:
                    node = db.get(Node, node_id)
                    if node and db.get(Account, node.account_id).pk == snapshot_pk:
                        node.latency, node.reachable, node.tested_at = latency, latency is not None, time.time()
                self.progress(job_id, "节点连接失败" if latency is None else None)
        try:
            await asyncio.gather(*(one(i) for i in ids))
            self.finish(job_id)
        finally:
            self.probe_job_id = None
