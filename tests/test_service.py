import asyncio
import time

import httpx
import pytest
from sqlalchemy import select

from app.main import create_app
from app.models import Account, Job, Node, ProxyCheck, Source, Subscription
from app.security import hint
from app.service import FAST_INTERVAL, FAST_WINDOW, PROBE_CONCURRENCY, proxy_key
from app.upstream import Gateway, InvalidToken
from tests.fixtures import FakeGateway


@pytest.mark.asyncio
async def test_coalesce_refresh_and_persistence(settings):
    app = create_app(settings)
    service = app.state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    with service.db.begin() as db:
        account = Account(name="测试", token_cipher=service.secrets.encrypt("a" * 32),
                          fingerprint=service.secrets.fingerprint("a" * 32), token_hint=hint("a" * 32))
        db.add(account)
        db.flush()
        account_id = account.id
    tasks = [service.ensure_refresh(account_id) for _ in range(8)]
    assert all(t is tasks[0] for t in tasks)
    await asyncio.gather(*tasks)
    assert len(service.gateway.calls) == 3
    with service.db.begin() as db:
        db.add(Job(kind="probe", status="running", total=5))
    await service.stop()
    second = create_app(settings).state.service
    await second.start()
    with second.db() as db:
        assert db.get(Account, account_id).last_success
        from sqlalchemy import select
        assert db.scalar(select(Job)).status == "interrupted"
    await second.stop()


@pytest.mark.asyncio
async def test_gateway_failover_and_no_redirect(settings):
    gateway = Gateway(settings)
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        assert request.headers["token"] == "a" * 32
        if count == 1:
            return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        return httpx.Response(200, json={"session": {}})
    await gateway.client.aclose()
    gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await gateway.request("a" * 32, "/session", body={"tester": {}}) == {"session": {}}
    assert count == 2
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_auth_does_not_retry(settings):
    gateway = Gateway(settings)
    await gateway.client.aclose()
    gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(InvalidToken):
        await gateway.request("a" * 32, "/session")
    await gateway.close()


@pytest.mark.asyncio
async def test_probe_blocks_private_address(settings):
    service = create_app(settings).state.service
    assert await service.probe_node("127.0.0.1", 443) is None
    await service.stop()


@pytest.mark.asyncio
async def test_probe_results_persist(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    with service.db.begin() as db:
        a = Account(name="测试", token_cipher=service.secrets.encrypt("a" * 32),
                    fingerprint=service.secrets.fingerprint("a" * 32), token_hint=hint("a" * 32))
        db.add(a)
        db.flush()
        account_id = a.id
    await service.refresh(account_id)
    async def fake_probe(server, port):
        return 15.5
    service.probe_node = fake_probe
    from sqlalchemy import select
    with service.db() as db:
        ids = list(db.scalars(select(Node.id)))
    job_id = service.new_job("probe", len(ids))
    await service.probe_job(job_id, ids)
    with service.db() as db:
        assert db.get(Job, job_id).status == "completed"
        assert all(n.reachable and n.latency == 15.5 and n.tested_at <= time.time()
                   for n in db.scalars(select(Node)))
    await service.stop()


@pytest.mark.asyncio
async def test_refresh_interval_accelerates_before_session_expiry(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    aged = time.time() - FAST_WINDOW - 60
    with service.db.begin() as db:
        old = Account(name="旧会话", token_cipher=service.secrets.encrypt("a" * 32),
                      fingerprint=service.secrets.fingerprint("a" * 32), token_hint=hint("a" * 32),
                      pk=service.gateway.pk, first_seen=aged)
        new = Account(name="新会话", token_cipher=service.secrets.encrypt("b" * 32),
                      fingerprint=service.secrets.fingerprint("b" * 32), token_hint=hint("b" * 32))
        db.add_all([old, new])
        db.flush()
        old_id, new_id = old.id, new.id
    await service.refresh(old_id)
    await service.refresh(new_id)
    with service.db() as db:
        old_account, new_account = db.get(Account, old_id), db.get(Account, new_id)
        assert old_account.first_seen == aged  # pk 未变，会话仍是那一轮
        assert old_account.next_refresh - time.time() <= FAST_INTERVAL
        assert new_account.next_refresh - time.time() > FAST_INTERVAL
    await service.stop()


def add_source(service, proxies, name="机场A"):
    with service.db.begin() as db:
        source = Source(name=name, url_cipher=service.secrets.encrypt("https://sub.example.com/s"),
                        proxies=proxies, last_fetch=time.time())
        db.add(source)
        db.flush()
        return source.id


@pytest.mark.asyncio
async def test_export_merges_sources_dedupes_and_caches_probes(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    probes = []

    async def probe(proxy):
        probes.append(proxy["server"])
        return True, 3.0, "tcp"

    service._probe_proxy = probe
    with service.db.begin() as db:
        account = Account(name="测试", token_cipher=service.secrets.encrypt("a" * 32),
                          fingerprint=service.secrets.fingerprint("a" * 32), token_hint=hint("a" * 32))
        db.add(account)
        db.flush()
        account_id = account.id
    duplicate = {"name": "节点A", "type": "ss", "server": "ss.example.com", "port": 8388,
                 "cipher": "aes-128-gcm", "password": "p"}
    source_id = add_source(service, [duplicate, duplicate | {"name": "节点A 副本"}])
    await service.refresh(account_id)

    proxies, groups = await service.export_proxies([account_id], [source_id])
    names = [p["name"] for p in proxies]
    assert len(names) == len(set(names))
    assert sum(1 for p in proxies if p["server"] == "ss.example.com") == 1
    assert any(group.startswith("账号 · 测试") for group in groups)
    assert any(group.startswith("来源 ·") for group in groups)

    probes.clear()
    await service.export_proxies([account_id], [source_id])
    assert probes == []  # 未开启“去除不可用”时不探测
    await service.export_proxies([account_id], [source_id], exclude_failed=True)
    assert len(probes) == 4  # 开启后对 3 个账号节点 + 1 个去重后的源节点各探测一次
    probes.clear()
    await service.export_proxies([account_id], [source_id], exclude_failed=True)
    assert probes == []  # 30 分钟内的探测结论直接复用

    kept, _ = await service.export_proxies([account_id], [source_id], dedupe=False)
    kept_names = [p["name"] for p in kept]
    assert len(kept_names) == len(set(kept_names))  # 关闭去重时同名节点加后缀
    assert sum(1 for p in kept if p["server"] == "ss.example.com") == 2
    await service.stop()


@pytest.mark.asyncio
async def test_export_excludes_failed_nodes_only(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()

    async def probe(proxy):
        return proxy["server"] == "good.example.com", 1.0, "tcp"

    service._probe_proxy = probe
    good = {"name": "好节点", "type": "trojan", "server": "good.example.com", "port": 443, "password": "x"}
    bad = {"name": "坏节点", "type": "trojan", "server": "bad.example.com", "port": 443, "password": "x"}
    source_id = add_source(service, [good, bad])
    with service.db.begin() as db:
        db.add(ProxyCheck(key=proxy_key(bad), reachable=False, latency=None,
                          method="tcp", tested_at=time.time()))

    kept, _ = await service.export_proxies([], [source_id], exclude_failed=True)
    assert [p["server"] for p in kept] == ["good.example.com"]

    everything, _ = await service.export_proxies([], [source_id], exclude_failed=False)
    assert {p["server"] for p in everything} == {"good.example.com", "bad.example.com"}
    await service.stop()


@pytest.mark.asyncio
async def test_shared_node_is_probed_once_across_accounts(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    calls = []

    async def probe(proxy):
        calls.append(proxy["server"])
        return True, 2.0, "tcp"

    service._probe_proxy = probe
    with service.db.begin() as db:
        for token in ("a" * 32, "b" * 32):
            db.add(Account(name=f"账号{token[0]}", token_cipher=service.secrets.encrypt(token),
                           fingerprint=service.secrets.fingerprint(token), token_hint=hint(token)))
    with service.db() as db:
        ids = list(db.scalars(select(Account.id)))
    for account_id in ids:
        await service.refresh(account_id)

    proxies, _ = await service.export_proxies(ids, [], dedupe=False, exclude_failed=True)
    assert len(proxies) == 6          # 两个账号各 3 个同名节点，关闭去重全部保留
    assert len(calls) == 3            # 相同节点只探测一次
    await service.stop()


def test_source_due_uses_backoff_after_failures():
    from types import SimpleNamespace

    from app.service import SOURCE_TTL, source_due

    now = time.time()
    healthy = SimpleNamespace(failures=0, last_fetch=now - 100, last_attempt=now - 100)
    stale = SimpleNamespace(failures=0, last_fetch=now - SOURCE_TTL - 1, last_attempt=now - SOURCE_TTL - 1)
    failing = SimpleNamespace(failures=1, last_fetch=None, last_attempt=now - 10)
    failing_later = SimpleNamespace(failures=1, last_fetch=None, last_attempt=now - 121)
    assert not source_due(healthy, now)
    assert source_due(stale, now)
    assert not source_due(failing, now)      # 失败后退避：1 次失败 = 120 秒
    assert source_due(failing_later, now)


@pytest.mark.asyncio
async def test_timed_out_probe_is_cached_as_unknown(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    calls = []

    async def unfinished(proxy):
        calls.append(proxy["server"])
        return None

    service._probe_proxy = unfinished
    source_id = add_source(service, [{"name": "慢节点", "type": "trojan",
                                      "server": "slow.example.com", "port": 443, "password": "x"}])
    kept, _ = await service.export_proxies([], [source_id], exclude_failed=True)
    assert [p["server"] for p in kept] == ["slow.example.com"]  # 未知状态不排除
    assert len(calls) == 1

    await service.export_proxies([], [source_id], exclude_failed=True)
    assert len(calls) == 1  # 未测完的结论会缓存，不会每轮重新排队
    await service.stop()


@pytest.mark.asyncio
async def test_probe_round_advances_instead_of_locking_the_tail(settings):
    """预算内没轮到的节点不能写“超时”结论，否则整批节点会被锁死一个缓存周期，队尾永远测不到。"""
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    tried = []

    async def hanging(proxy):
        tried.append(proxy["server"])
        await asyncio.sleep(30)
        return True, 1.0, "tcp"

    service._probe_proxy = hanging

    def rows_of(total):
        return [{"proxy": {"name": f"N{i}", "type": "trojan", "server": f"n{i}.example.com",
                           "port": 443, "password": "x"},
                 "reachable": None, "latency": None, "tested_at": None} for i in range(total)]

    rows = rows_of(40)
    await service._probe_stale(rows, time.time(), budget=1.6)
    first = set(tried)
    assert len(first) == PROBE_CONCURRENCY  # 预算只够发起一批探测
    assert all(row["reachable"] is None for row in rows)  # 没测出结论的一律保持未知
    with service.db() as db:
        stored = list(db.scalars(select(ProxyCheck)))
    assert len(stored) == PROBE_CONCURRENCY  # 只有真的发起过探测的节点才写结论
    assert all(c.method == "timeout" for c in stored)

    tried.clear()
    await service._probe_stale(rows_of(40), time.time(), budget=1.6)
    assert set(tried) and not set(tried) & first  # 下一轮换一批，队列会自己往后推进


@pytest.mark.asyncio
async def test_export_filters_by_max_latency(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()

    async def probe(proxy):
        return True, (900.0 if proxy["server"].startswith("slow") else 40.0), "tcp"

    service._probe_proxy = probe
    source_id = add_source(service, [
        {"name": "快节点", "type": "trojan", "server": "fast.example.com", "port": 443, "password": "x"},
        {"name": "慢节点", "type": "trojan", "server": "slow.example.com", "port": 443, "password": "x"},
    ])
    kept, _ = await service.export_proxies([], [source_id], max_latency=200)
    assert [p["server"] for p in kept] == ["fast.example.com"]

    everything, _ = await service.export_proxies([], [source_id])
    assert len(everything) == 2
    await service.stop()


@pytest.mark.asyncio
async def test_probe_subscription_writes_records_and_schedule(settings):
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    calls = []

    async def probe(proxy):
        calls.append(proxy["server"])
        return True, 10.0, "tcp"

    service._probe_proxy = probe
    source_id = add_source(service, [{"name": "N", "type": "trojan",
                                      "server": "n.example.com", "port": 443, "password": "x"}])
    with service.db.begin() as db:
        filtered = Subscription(name="过滤订阅", account_ids=[], source_ids=[source_id],
                                key_hash="hash-filtered", key_cipher=service.secrets.encrypt("k1"))
        filtered.exclude_failed = True
        plain = Subscription(name="普通订阅", account_ids=[], source_ids=[source_id],
                             key_hash="hash-plain", key_cipher=service.secrets.encrypt("k2"))
        db.add_all([filtered, plain])
        db.flush()
        filtered_id, plain_id = filtered.id, plain.id

    assert set(service._subscriptions_due(time.time())) == {filtered_id}  # 只有开启过滤的订阅才定时测速
    await service.probe_subscription(filtered_id)
    assert len(calls) == 1
    with service.db() as db:
        assert db.get(Subscription, filtered_id).last_probe
        assert db.scalar(select(ProxyCheck)) is not None
        cached = db.scalar(select(ProxyCheck))
        assert cached.reachable is True and cached.latency == 10.0
    assert filtered_id not in service._subscriptions_due(time.time())      # 刚测过
    assert filtered_id in service._subscriptions_due(time.time() + 3600)   # 一小时后到期
    assert plain_id not in service._subscriptions_due(time.time() + 3600)
    await service.stop()


@pytest.mark.asyncio
async def test_dedupe_keeps_node_in_every_group(settings):
    service = create_app(settings).state.service
    shared = {"name": "共享", "type": "trojan", "server": "s.example.com", "port": 443, "password": "x"}
    first = add_source(service, [shared], name="源一")
    second = add_source(service, [shared | {"name": "共享副本"}], name="源二")
    proxies, groups = await service.export_proxies([], [first, second])
    assert len(proxies) == 1
    assert groups["来源 · 源一"] == groups["来源 · 源二"] == [proxies[0]["name"]]
    await service.stop()


@pytest.mark.asyncio
async def test_stale_failed_result_is_not_excluded(settings):
    """账号节点上过期的“不可达”结论，探测没跑到时按未知保留，不能被剔除。"""
    service = create_app(settings).state.service
    await service.gateway.close()
    service.gateway = FakeGateway()
    with service.db.begin() as db:
        account = Account(name="测试", token_cipher=service.secrets.encrypt("a" * 32),
                          fingerprint=service.secrets.fingerprint("a" * 32), token_hint=hint("a" * 32))
        db.add(account)
        db.flush()
        account_id = account.id
    await service.refresh(account_id)
    with service.db.begin() as db:
        nodes = list(db.scalars(select(Node)))
        for node in nodes:
            node.reachable, node.tested_at = False, time.time() - 7200
            # 上次探测没测完：缓存里是 timeout，本轮会直接跳过
            db.merge(ProxyCheck(key=proxy_key({"type": "http", "server": node.server, "port": node.port,
                                               "tls": True, "skip-cert-verify": True}),
                                reachable=False, method="timeout", tested_at=time.time()))

    async def never(proxy):
        raise AssertionError("不应发起探测")

    service._probe_proxy = never
    proxies, _ = await service.export_proxies([account_id], [], exclude_failed=True)
    assert len(proxies) == len(nodes)
    await service.stop()


@pytest.mark.asyncio
async def test_failed_background_probe_backs_off(settings):
    service = create_app(settings).state.service
    source_id = add_source(service, [{"name": "N", "type": "trojan", "server": "n.example.com",
                                      "port": 443, "password": "x"}])
    with service.db.begin() as db:
        sub = Subscription(name="过滤", account_ids=[], source_ids=[source_id], exclude_failed=True,
                           key_hash="h", key_cipher=service.secrets.encrypt("k"))
        db.add(sub)
        db.flush()
        sub_id = sub.id

    async def boom(sub_id):
        raise RuntimeError("测速失败")

    service.probe_subscription = boom
    assert service.schedule_probe(sub_id)
    await asyncio.gather(*service.tasks)
    assert sub_id not in service._subscriptions_due(time.time())  # 不会每 15 秒重新拉起
    assert sub_id in service._subscriptions_due(time.time() + 600)
    assert f"sub:{sub_id}" not in service.locks
    await service.stop()


@pytest.mark.asyncio
async def test_http_source_probe_uses_node_scheme_and_auth(settings):
    service = create_app(settings).state.service
    seen = []

    async def probe_node(server, port, tls=True, username=None, password=None):
        seen.append((tls, username, password))
        return 9.0

    service.probe_node = probe_node
    await service._probe_proxy({"type": "http", "server": "p.example.com", "port": 8080,
                                "username": "u", "password": "p"})
    await service._probe_proxy({"type": "http", "server": "p.example.com", "port": 443, "tls": True})
    assert seen == [(False, "u", "p"), (True, None, None)]
    await service.stop()


@pytest.mark.asyncio
async def test_legacy_source_cache_is_sanitized_on_export(settings):
    """修复前写入的缓存仍带 dialer-proxy / https，导出时也要清洗掉。"""
    service = create_app(settings).state.service
    source_id = add_source(service, [
        {"name": "链式", "type": "ss", "server": "a.example.com", "port": 443, "cipher": "aes-128-gcm",
         "password": "p", "dialer-proxy": "前置", "interface-name": "eth0", "routing-mark": 1},
        {"name": "TLS", "type": "https", "server": "h.example.com", "port": 443},
    ])
    proxies, _ = await service.export_proxies([], [source_id])
    assert not {"dialer-proxy", "interface-name", "routing-mark"} & set(proxies[0])
    assert proxies[1]["type"] == "http" and proxies[1]["tls"] is True
    await service.stop()
