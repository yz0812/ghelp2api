import asyncio
import contextlib
import ipaddress
import json
import re
import socket
import time

from sqlalchemy import delete, select, update

from app.models import Account, Job, LoginSession, Node, Preference, ProxyCheck, Source, Subscription
from app.pac import PacError, parse_pac
from app.regions import label as region_label
from app.security import digest, hint
from app.sources import Fetcher, SourceError
from app.subscription import SubscriptionError, clean_name, parse_subscription, sanitize
from app.upstream import Gateway, InvalidToken, UpstreamError

# 上游会话约 180 分钟回收；到期前 30 分钟改为高频探测，避免拿着失效节点等到下一个刷新周期。
SESSION_LIFETIME = 180 * 60
FAST_WINDOW = SESSION_LIFETIME - 30 * 60
FAST_INTERVAL = 5 * 60
# 订阅源的按需重新拉取间隔、拉取总超时、探测结果有效期
SOURCE_TTL = 30 * 60
SOURCE_TIMEOUT = 20
SOURCE_FETCH_TIMEOUT = 45
CHECK_TTL = 30 * 60
# 生成订阅时的现场探测：总预算 12 秒，避免客户端拉取超时
PROBE_BUDGET = 12
PROBE_CONCURRENCY = 16
# 剩余预算不足这个值时不再发起新的探测：没测过的节点留到下一轮，不要记成“已超时”
PROBE_MIN_SLICE = 1.5
# 定时测速在后台跑，可以给更长的预算；单轮测不完就连续跑，直到覆盖全量或到达总时限
SCHEDULED_PROBE_BUDGET = 120
SCHEDULED_COVER_BUDGET = 600
# 所有现场/后台探测共享的并发上限（HTTP 节点的每个探测都是一个 curl 子进程）
PROBE_GLOBAL_LIMIT = 32
# 同时跑的后台订阅测速数；测速失败后的重试间隔
SUB_PROBE_LIMIT = 2
SUB_PROBE_RETRY = 5 * 60


def proxy_key(proxy: dict) -> str:
    """去重与探测缓存的指纹；名称、UDP 开关这类展示性字段不参与比较。"""
    material = {k: v for k, v in proxy.items() if k not in {"name", "udp", "dialer-proxy"}}
    return digest(json.dumps(material, sort_keys=True, default=str))


def source_due(source, now) -> bool:
    """成功的源按缓存期重拉，失败的源指数退避，避免每次生成都去撞一个连不上的地址。"""
    if source.failures:
        backoff = min(SOURCE_TTL, 60 * 2 ** min(source.failures, 5))
        return source.last_attempt is None or now - source.last_attempt >= backoff
    return source.last_fetch is None or now - source.last_fetch >= SOURCE_TTL


class Service:
    def __init__(self, settings, db, secrets):
        self.settings, self.db, self.secrets = settings, db, secrets
        self.gateway = Gateway(settings)
        self.fetcher = Fetcher()
        self.locks: dict[str, asyncio.Lock] = {}
        self.refreshes: dict[str, asyncio.Task] = {}
        self.source_tasks: set[str] = set()
        self.sub_probes: set[str] = set()
        self.sub_probe_failed: dict[str, float] = {}
        self.sub_probe_limit = asyncio.Semaphore(SUB_PROBE_LIMIT)
        self.proxy_limit = asyncio.Semaphore(PROBE_GLOBAL_LIMIT)
        self.tasks: set[asyncio.Task] = set()
        self.account_limit = asyncio.Semaphore(3)
        self.probe_limit = asyncio.Semaphore(8)
        self.probe_job_id = None
        self.source_check_job_id = None
        self.scheduler = None

    def lock(self, account_id):
        return self.locks.setdefault(account_id, asyncio.Lock())

    def preferences(self):
        with self.db() as db:
            row = db.get(Preference, "general")
            return {"refresh_minutes": 30, "probe_minutes": 30} | (row.value if row else {})

    def refresh_interval(self, account, now, base):
        """会话接近寿命时缩短探测间隔，把发现新节点池的延迟压到 FAST_INTERVAL 内。"""
        if account.first_seen and now - account.first_seen >= FAST_WINDOW:
            return min(base, FAST_INTERVAL)
        return base

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
        await self.fetcher.close()

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
                for sub_id in self._subscriptions_due(now):
                    self.schedule_probe(sub_id)
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
                    base = self.preferences()["refresh_minutes"] * 60
                    account.next_refresh = time.time() + self.refresh_interval(account, time.time(), base)
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

    async def export_proxies(self, account_ids, source_ids=(), regions=None, exclude_failed=False,
                             dedupe=True, max_latency=None):
        """合并账号与订阅源，返回 (proxy 字典列表, 分组名 → 节点名列表)。

        开启过滤时才现场探测；探测有总预算，超时的按未知处理。
        """
        now = time.time()
        await self._refresh_due(account_ids, source_ids, now)
        rows = self._ghelper_rows(account_ids, regions, now) + self._source_rows(source_ids)
        if exclude_failed or max_latency:  # 只有需要过滤时才现场探测，普通订阅不为等待付出代价
            await self._probe_stale(rows, now)
        if exclude_failed:
            rows = [row for row in rows if row["reachable"] is not False]
        if max_latency:
            # 延迟未知的节点保留：探测没完成不等于节点慢
            rows = [row for row in rows if row["latency"] is None or row["latency"] <= max_latency]
        return self._assemble(rows, dedupe)

    async def _refresh_due(self, account_ids, source_ids, now):
        with self.db() as db:
            accounts = list(db.scalars(select(Account).where(Account.id.in_(account_ids),
                Account.enabled.is_(True), Account.status != "invalid")))
            base = self.preferences()["refresh_minutes"] * 60
            due = [a.id for a in accounts if (not a.last_success or now - a.last_success >=
                   self.refresh_interval(a, now, base)) and a.next_refresh <= now]
        if due:
            waiting = asyncio.gather(*(asyncio.shield(self.ensure_refresh(i)) for i in due))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(waiting, timeout=35)
        if source_ids:
            with self.db() as db:
                rows = list(db.scalars(select(Source).where(
                    Source.id.in_(source_ids), Source.enabled.is_(True))))
            due = [s for s in rows if source_due(s, now)]
            for source in due:
                if source.proxies and source.id not in self.source_tasks:  # 已有缓存：后台更新，不拖慢这次生成
                    self.source_tasks.add(source.id)
                    self.spawn(self._refresh_source_safe(source.id))
            blocking = [s.id for s in due if not s.proxies]  # 首次拉取才等待
            if blocking:
                waiting = asyncio.gather(*(self.refresh_source(i) for i in blocking))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(waiting, timeout=SOURCE_TIMEOUT)

    async def _refresh_source_safe(self, source_id):
        try:
            with contextlib.suppress(Exception):
                await self.refresh_source(source_id)
        finally:
            self.source_tasks.discard(source_id)

    def forget_source(self, source_id):
        """订阅源删除后释放它的锁，避免 locks 无限增长。"""
        lock = self.locks.get(f"source:{source_id}")
        if lock and not lock.locked():
            del self.locks[f"source:{source_id}"]

    def _subscriptions_due(self, now):
        """开启了过滤条件的订阅到期后重新测速，让生成时直接命中结论。"""
        minutes = self.preferences().get("probe_minutes", 30)
        with self.db() as db:
            subs = list(db.scalars(select(Subscription).where(Subscription.enabled.is_(True))))
        return [s.id for s in subs if (s.exclude_failed or s.max_latency)
                and (not s.last_probe or now - s.last_probe >= minutes * 60)
                and now - self.sub_probe_failed.get(s.id, 0) >= SUB_PROBE_RETRY]

    def schedule_probe(self, sub_id):
        """同一订阅同时只跑一个后台测速任务：一轮可能要跑好几分钟。"""
        if sub_id in self.sub_probes:
            return False
        self.sub_probes.add(sub_id)
        self.spawn(self._probe_subscription_safe(sub_id))
        return True

    async def _probe_subscription_safe(self, sub_id):
        try:
            async with self.sub_probe_limit:
                await self.probe_subscription(sub_id)
            self.sub_probe_failed.pop(sub_id, None)
        except Exception:
            # 失败时 last_probe 不会更新，记下时间避免调度器每 15 秒重新拉起一轮
            self.sub_probe_failed[sub_id] = time.time()
        finally:
            self.sub_probes.discard(sub_id)
            self.locks.pop(f"sub:{sub_id}", None)

    async def probe_subscription(self, sub_id):
        """按订阅自身的条件给节点测速；结论写入探测缓存，生成订阅时直接复用。

        后台一轮内连续推进，直到这批节点全部有结论或到达总时限：节点上万个时
        单轮测不完，只测到队头就会让队列尾部永远轮不到。
        """
        async with self.lock(f"sub:{sub_id}"):
            with self.db() as db:
                sub = db.get(Subscription, sub_id)
                if not sub or not sub.enabled:
                    return
                accounts, sources = list(sub.account_ids), list(sub.source_ids)
                regions = list(sub.regions)
            deadline = time.time() + SCHEDULED_COVER_BUDGET
            now = time.time()
            await self._refresh_due(accounts, sources, now)
            rows = self._ghelper_rows(accounts, regions, now) + self._source_rows(sources)
            while time.time() < deadline:
                budget = min(SCHEDULED_PROBE_BUDGET, deadline - time.time())
                if budget <= PROBE_MIN_SLICE:
                    break
                if not await self._probe_stale(rows, time.time(), budget=budget):
                    break  # 没有节点需要测：缓存已覆盖全量
            with self.db.begin() as db:
                row = db.get(Subscription, sub_id)
                if row:
                    row.last_probe = time.time()

    def _ghelper_rows(self, account_ids, regions, now):
        if not account_ids:
            return []
        with self.db() as db:
            pairs = db.execute(select(Node, Account).join(Account).where(
                Account.id.in_(account_ids), Account.enabled.is_(True), Account.status != "invalid",
                Account.last_success >= now - 3600)).all()
        rows = []
        for node, account in pairs:
            if regions and node.region not in regions:
                continue
            alias = re.sub(r"[\x00-\x1f\x7f]", "", account.name)[:40]
            rows.append({
                "proxy": {"name": f"{alias}-{region_label(node.region)}-{node.region}-{digest(f'{node.server}:{node.port}')[:10]}",
                          "type": "http", "server": node.server, "port": node.port,
                          "tls": True, "skip-cert-verify": True, "udp": False},
                "group": f"账号 · {alias} · {account.id[:8]}",
                "reachable": node.reachable, "latency": node.latency, "tested_at": node.tested_at})
        return rows

    def _source_rows(self, source_ids):
        if not source_ids:
            return []
        with self.db() as db:
            sources = list(db.scalars(select(Source).where(
                Source.id.in_(source_ids), Source.enabled.is_(True)).order_by(Source.created_at)))
        rows = []
        for source in sources:
            prefix = clean_name(source.name, "订阅源")
            for index, stored in enumerate(source.proxies or []):
                # 缓存可能是旧版解析器写入的：输出前按当前规则再清洗一遍
                proxy = sanitize(stored, f"节点{index + 1}")
                if not proxy:
                    continue
                proxy["name"] = clean_name(f"{prefix}-{proxy['name']}", prefix)
                rows.append({"proxy": proxy, "group": f"来源 · {prefix}",
                             "reachable": None, "latency": None, "tested_at": None})
        return rows

    async def _probe_stale(self, rows, now, budget=PROBE_BUDGET):
        """补齐缺少新鲜结论的节点；返回本轮实际排队的节点数。"""
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(proxy_key(row["proxy"]), []).append(row)
        cached = {}
        if grouped:
            with self.db() as db:
                cached = {c.key: c for c in db.scalars(
                    select(ProxyCheck).where(ProxyCheck.key.in_(grouped)))}
        todo: list[tuple[float, str, list[dict]]] = []
        for key, group in grouped.items():
            fresh = next((r for r in group if r["tested_at"] and now - r["tested_at"] < CHECK_TTL), None)
            if fresh:
                value = (fresh["reachable"], fresh["latency"], fresh["tested_at"])  # 账号测速结果更新鲜
            else:
                # 账号节点上的旧结论已过期：先清成未知，没测出新结论前不能被“去除不可用”剔掉
                for row in group:
                    row["reachable"] = row["latency"] = None
                check = cached.get(key)
                if check and now - check.tested_at < CHECK_TTL:
                    if check.method == "timeout":
                        continue  # 上次没测完：本轮按未知保留，下个周期再试
                    value = (check.reachable, check.latency, check.tested_at)
                else:
                    # 记住这个节点上次有结论的时间，从没测过的排最前面
                    last = max([r["tested_at"] or 0 for r in group] + [check.tested_at if check else 0])
                    todo.append((last, key, group))
                    continue
            for row in group:
                row["reachable"], row["latency"], row["tested_at"] = value
        if todo:
            todo.sort(key=lambda item: item[0])  # 按上次结论时间升序：每轮都往后推进，队尾不会被饿死
            for _, key, group in todo:
                group[0]["key"] = key
            await self._probe_rows([group[0] for _, _, group in todo], now, budget)
            for _, _, group in todo:  # 同一节点出现在多个账号时共享一次探测结论
                for row in group:
                    row["reachable"] = group[0]["reachable"]
                    row["latency"], row["tested_at"] = group[0]["latency"], group[0]["tested_at"]
        return len(todo)

    async def _probe_rows(self, rows, now, budget=PROBE_BUDGET):
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
        deadline = time.time() + budget

        async def one(row):
            async with semaphore, self.proxy_limit:  # 排队等全局名额的时间不计入“测过”
                remaining = deadline - time.time()
                if remaining <= PROBE_MIN_SLICE:
                    return None  # 预算耗尽：没轮到的按未知处理，但不算“测过”
                row["tried"] = True
                try:
                    return await asyncio.wait_for(self._probe_proxy(row["proxy"]), remaining)
                except (TimeoutError, OSError, ValueError):
                    return None

        tasks = [asyncio.create_task(one(row)) for row in rows]
        # 硬性截止：到点直接取消未完成的探测，保证生成时间不会超过预算太多。
        done, pending = await asyncio.wait(tasks, timeout=budget)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        checks, probed = [], 0
        for row, task in zip(rows, tasks):
            result = None
            if task in done and not task.cancelled():
                try:
                    result = task.result()
                except Exception:
                    result = None
            if not row.pop("tried", False):
                # 预算内根本没轮到它：不写结论，否则会把整批节点锁死一个缓存周期
                continue
            probed += 1
            if result is None:  # 发起过但没测出结果：记为未知，避免每轮都重新排队
                checks.append(ProxyCheck(key=row["key"], reachable=False, latency=None,
                                         method="timeout", tested_at=now))
                continue
            reachable, latency, method = result
            row["reachable"], row["latency"], row["tested_at"] = reachable, latency, now
            checks.append(ProxyCheck(key=row["key"], reachable=reachable, latency=latency,
                                     method=method, tested_at=now))
        if checks:
            with self.db.begin() as db:
                for check in checks:
                    db.merge(check)
        return probed

    async def _probe_proxy(self, proxy):
        server = str(proxy.get("server") or "")
        port = int(proxy.get("port") or 0)
        if not server or not 1 <= port <= 65535:
            raise ValueError("节点地址不合法")
        if proxy.get("type") == "http":
            # 外部源的 HTTP 节点可能是明文、可能带认证，按节点自身的配置探测
            latency = await self.probe_node(server, port, tls=bool(proxy.get("tls")),
                                            username=proxy.get("username"), password=proxy.get("password"))
            return latency is not None, latency, "http"
        return await self.probe_tcp(server, port)

    async def probe_tcp(self, server, port):
        """非 HTTP 协议只能验证端口连通，无法确认代理本身可用。"""
        loop = asyncio.get_running_loop()
        try:
            addresses = await asyncio.wait_for(loop.getaddrinfo(server, port, type=socket.SOCK_STREAM), 5)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return False, None, "tcp"
        except (OSError, TimeoutError, ValueError):
            return False, None, "tcp"
        start = time.perf_counter()
        writer = None
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(addresses[0][4][0], port), 3)
            return True, round((time.perf_counter() - start) * 1000, 1), "tcp"
        except (OSError, TimeoutError):
            return False, None, "tcp"
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), 1)

    def _assemble(self, rows, dedupe=True):
        """去重时节点只输出一次，但每个包含它的账号/来源分组都保留引用。"""
        proxies, groups, names, seen = [], {}, set(), {}
        for row in rows:
            key = proxy_key(row["proxy"]) if dedupe else None
            if key in seen:
                groups.setdefault(row["group"], []).append(seen[key])
                continue
            proxy = row["proxy"]
            base = proxy["name"]
            name, index = base, 2
            while name in names:  # 关闭去重时保留同名节点的可读性
                name, index = f"{base} · {index}", index + 1
            proxy["name"] = name
            names.add(name)
            proxies.append(proxy)
            groups.setdefault(row["group"], []).append(name)
            if key:
                seen[key] = name
        return proxies, groups

    async def refresh_source(self, source_id):
        """拉取一个订阅源；失败时保留上次缓存并记录脱敏错误。"""
        async with self.lock(f"source:{source_id}"):
            with self.db() as db:
                source = db.get(Source, source_id)
                if not source or not source.enabled:
                    return 0, "订阅源不存在或已停用"
                try:
                    url = self.secrets.decrypt(source.url_cipher)
                except Exception:
                    return 0, "订阅地址无法解密，请重新填写"
            try:
                async with asyncio.timeout(SOURCE_FETCH_TIMEOUT):
                    proxies = parse_subscription(await self.fetcher.fetch(url))
            except (SourceError, SubscriptionError) as exc:
                error = str(exc)
            except TimeoutError:
                error = "拉取订阅超时"
            except Exception:
                error = "拉取订阅失败，请检查地址"
            else:
                with self.db.begin() as db:
                    row = db.get(Source, source_id)
                    if not row:  # 拉取期间被删除：丢弃结果，不要把节点写回来
                        return 0, "订阅源已被删除"
                    row.proxies, row.last_fetch = proxies, time.time()
                    row.last_attempt, row.last_error, row.failures = time.time(), None, 0
                return len(proxies), None
            with self.db.begin() as db:
                row = db.get(Source, source_id)
                if row:
                    row.last_attempt = time.time()
                    row.last_error, row.failures = error, row.failures + 1
            return 0, error

    async def source_check_job(self, job_id, ids):
        """逐个重新拉取订阅源，拉取失败或没有节点的记为失效，供一键清理。"""
        with self.db.begin() as db:
            db.get(Job, job_id).status = "running"
        limit = asyncio.Semaphore(5)

        async def one(source_id):
            async with limit:
                count, error = await self.refresh_source(source_id)
                if not error and count:
                    self.progress(job_id)
                    return
                with self.db() as db:
                    source = db.get(Source, source_id)
                    name = source.name if source else source_id[:8]
                self.progress(job_id, f"{name}：{error or '没有解析到节点'}")

        try:
            await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)
            self.finish(job_id)
        finally:
            self.source_check_job_id = None

    async def probe_node(self, server, port, tls=True, username=None, password=None):
        loop = asyncio.get_running_loop()
        process = None
        credentials = f"{username or ''}:{password or ''}" if username or password else ""
        if re.search(r"[\x00-\x1f\x7f]", credentials):
            return None
        try:
            addresses = await asyncio.wait_for(loop.getaddrinfo(server, port, type=socket.SOCK_STREAM), 5)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return None
            address = addresses[0][4][0]
            resolved = f"[{address}]" if ":" in address else address
            # 代理凭据经 stdin 传给 curl 的配置，不出现在进程命令行里
            config = b""
            if credentials:
                escaped = credentials.replace("\\", "\\\\").replace('"', '\\"')
                config = f'proxy-user = "{escaped}"\n'.encode()
            process = await asyncio.create_subprocess_exec(
                "curl", "--silent", "--output", "NUL" if __import__("os").name == "nt" else "/dev/null",
                "--proxy", f"{'https' if tls else 'http'}://{server}:{port}", "--proxy-insecure",
                "--noproxy", "", "--resolve", f"{server}:{port}:{resolved}", "--max-time", "8",
                "--config", "-", "--write-out", "%{json}", "https://www.gstatic.com/generate_204",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(process.communicate(config), 10)
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
