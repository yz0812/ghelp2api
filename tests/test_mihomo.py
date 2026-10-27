import time

import pytest
from sqlalchemy import select

import app.service as service_module
from app.main import create_app
from app.mihomo import MihomoTester, MihomoUnavailable
from app.models import ProxyCheck
from app.service import proxy_key


def row(server, reachable=None, latency=None, tested_at=None):
    proxy = {"name": server, "type": "trojan", "server": server, "port": 443, "password": "x"}
    return {"proxy": proxy, "country": "US", "key": proxy_key(proxy),
            "reachable": reachable, "latency": latency, "tested_at": tested_at}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (200, b'{"delay":88}', (True, 88.0)),      # 真测通了，拿到延迟
    (503, b'{"message":"An error occurred in the delay test"}', (False, None)),  # 连不上
    (504, b'{"message":"Timeout"}', (False, None)),  # 超时
    (400, b'{"message":"proxy not support url test"}', (False, None)),  # 配置被内核拒绝
    (404, b'{"message":"not found"}', ("unfinished", None)),  # 名字对不上：当没测，不写结论
])
async def test_delay_outcome_follows_controller_status(settings, monkeypatch, status, body, expected):
    tester = MihomoTester(settings.data_dir / "mihomo", binary="mihomo")

    async def fake_get(port, path, timeout):
        assert path.startswith("/proxies/n0/delay?")
        return status, body

    monkeypatch.setattr(MihomoTester, "_get", staticmethod(fake_get))
    assert await tester._delay(1234, 0, time.time() + 30) == expected


@pytest.mark.asyncio
async def test_probe_rows_uses_kernel_and_records_method(settings, monkeypatch):
    service = create_app(settings).state.service
    probed = []

    class FakeTester:
        binary = "fake-mihomo"

        def __init__(self, home, limit=None, timeout=None):
            self.limit = limit

        async def delays(self, proxies, deadline, on_done=None):
            probed.extend(p["server"] for p in proxies)
            return [(True, 42.0) if p["server"].startswith("ok") else (False, None) for p in proxies]

    async def unexpected(proxy):
        raise AssertionError("有内核时不该退回端口探测")

    monkeypatch.setattr(service_module, "MihomoTester", FakeTester)
    service._probe_proxy = unexpected
    rows = [row("ok.example.com"), row("dead.example.com"), row("ok2.example.com")]
    assert await service._probe_rows(rows, time.time(), budget=5) == 3
    assert probed == ["ok.example.com", "dead.example.com", "ok2.example.com"]
    # 没测通的节点明确记为不可用：开启“去除不可用”时才会被剔掉
    assert [(r["reachable"], r["latency"]) for r in rows] == [(True, 42.0), (False, None), (True, 42.0)]
    with service.db() as db:
        stored = {c.key: c for c in db.scalars(select(ProxyCheck))}
    assert {c.method for c in stored.values()} == {"mihomo"}
    assert stored[rows[1]["key"]].reachable is False
    await service.stop()


@pytest.mark.asyncio
async def test_probe_rows_falls_back_when_kernel_unavailable(settings, monkeypatch):
    service = create_app(settings).state.service

    class BrokenTester:
        binary = "fake-mihomo"

        def __init__(self, home, limit=None, timeout=None):
            pass

        async def delays(self, proxies, deadline, on_done=None):
            raise MihomoUnavailable("内核没起来")

    async def port_probe(proxy):
        return True, 12.0, "tcp"

    monkeypatch.setattr(service_module, "MihomoTester", BrokenTester)
    service._probe_proxy = port_probe
    rows = [row("ok.example.com")]
    assert await service._probe_rows(rows, time.time(), budget=5) == 1
    with service.db() as db:
        assert db.get(ProxyCheck, rows[0]["key"]).method == "tcp"
    await service.stop()


@pytest.mark.asyncio
async def test_port_only_conclusion_is_retested_when_kernel_available(settings, monkeypatch, tmp_path):
    service = create_app(settings).state.service
    target = row("ok.example.com")
    with service.db.begin() as db:
        db.merge(ProxyCheck(key=target["key"], reachable=True, latency=33.0,
                            method="tcp", tested_at=time.time()))
    service._probe_rows = lambda *a, **k: None  # 只验证结论的取舍，不真的去测

    # 没有内核时端口连通的老结论继续沿用
    assert service._apply_checks([dict(target)], time.time()) == []
    monkeypatch.setenv("MIHOMO_BIN", str(tmp_path / "mihomo"))
    (tmp_path / "mihomo").write_text("", encoding="utf-8")
    checked = dict(target)
    # 有内核时端口探测的结论不可信：清成未知并重新排队，用真实测速覆盖
    assert len(service._apply_checks([checked], time.time())) == 1
    assert checked["reachable"] is None and checked["latency"] is None
    await service.stop()


@pytest.mark.asyncio
async def test_kernel_probe_uses_configured_timeout(settings, monkeypatch):
    from app.models import Preference
    service = create_app(settings).state.service
    seen = []

    class FakeTester:
        binary = "fake-mihomo"

        def __init__(self, home, limit=None, timeout=None):
            seen.append(timeout)

        async def delays(self, proxies, deadline, on_done=None):
            return [(True, 10.0) for _ in proxies]

    monkeypatch.setattr(service_module, "MihomoTester", FakeTester)
    with service.db.begin() as db:
        db.merge(Preference(key="general", value={"refresh_minutes": 30, "probe_minutes": 30,
                                                  "probe_timeout_ms": 2500}))
    await service._probe_rows([row("ok.example.com")], time.time(), budget=5)
    assert seen == [2500]
    await service.stop()
