import time

import yaml
from sqlalchemy import select

from app.models import Account, LoginSession, Node
from app.sources import SourceError


def refresh(client, account_id):
    return client.portal.call(client.app.state.service.refresh, account_id)


def sub(client, ids):
    return client.post("/api/v1/subscriptions", json={"name": "聚合订阅", "account_ids": ids}).json()


def test_auth_csrf_and_logout(client, settings):
    client.headers.pop("Authorization")
    assert client.get("/api/v1/accounts").status_code == 401
    assert client.get("/api/v1/openapi.json").status_code == 401
    result = client.post("/api/v1/auth/login", json={"username": "admin", "password": settings.admin_password})
    assert result.status_code == 200
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "Max-Age=2592000" in result.headers["set-cookie"]  # 30 天
    assert client.post("/api/v1/accounts", json={"token": "a" * 32}).status_code == 403
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    assert client.post("/api/v1/accounts", json={"token": "a" * 32}).status_code == 201
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/accounts").status_code == 401


def test_session_expires_after_ttl(client, settings):
    client.headers.pop("Authorization")
    result = client.post("/api/v1/auth/login", json={"username": "admin", "password": settings.admin_password})
    assert result.status_code == 200
    csrf = result.json()["csrf"]
    with client.app.state.db.begin() as db:
        session = db.scalars(select(LoginSession)).one()
        assert session.expires > time.time() + 29 * 86400
        session.expires = time.time() - 1  # 模拟已过期
    client.headers["X-CSRF-Token"] = csrf
    assert client.get("/api/v1/accounts").status_code == 401


def test_login_throttle_and_origin(client):
    client.headers.pop("Authorization")
    payload = {"username": "admin", "password": "incorrect"}
    assert client.post("/api/v1/auth/login", json=payload, headers={"Origin": "https://other.invalid"}).status_code == 403
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 429


def test_validation_never_echoes_secrets(client):
    secret = "this-is-not-a-token-and-must-not-appear"
    result = client.post("/api/v1/accounts", json={"token": secret})
    assert result.status_code == 422
    assert secret not in result.text
    assert client.post("/api/v1/accounts", content=b"x" * 1_000_001).status_code == 413


def test_import_duplicate_and_encryption(client, account_id):
    result = client.post("/api/v1/accounts/import", json={"accounts": [
        {"name": "重复", "token": "a" * 32}, {"name": "另一个", "token": "b" * 32}]}).json()
    assert [r["status"] for r in result["results"]] == ["duplicate", "created"]
    assert "a" * 32 not in client.get("/api/v1/accounts").text
    with client.app.state.db() as db:
        assert "a" * 32 not in db.get(Account, account_id).token_cipher


def test_refresh_account_and_full_subscription(client, account_id):
    assert refresh(client, account_id) is None
    account = client.get("/api/v1/accounts").json()[0]
    assert account["node_count"] == 3
    assert account["info"]["email"] == "demo@example.invalid"
    assert "token" not in account["info"]
    subscription = sub(client, [account_id])
    result = client.get(subscription["clash_url"])
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    config = yaml.safe_load(result.text)
    assert len(config["proxies"]) == 3
    assert all(p["tls"] and p["type"] == "http" for p in config["proxies"])
    names = {p["name"] for p in config["proxies"]} | {g["name"] for g in config["proxy-groups"]} | {"DIRECT"}
    assert all(p in names for g in config["proxy-groups"] for p in g["proxies"])
    assert "proxy-groups" not in yaml.safe_load(client.get(subscription["proxies_url"]).text)


def test_rotate_revokes_old_subscription(client, account_id):
    refresh(client, account_id)
    subscription = sub(client, [account_id])
    rotated = client.post(f"/api/v1/subscriptions/{subscription['id']}/rotate").json()
    assert client.get(subscription["clash_url"]).status_code == 404
    assert client.get(rotated["clash_url"]).status_code == 200
    assert client.delete(f"/api/v1/subscriptions/{subscription['id']}").status_code == 200
    assert client.get(rotated["clash_url"]).status_code == 404


def test_disabled_account_and_empty_fail(client, account_id):
    refresh(client, account_id)
    subscription = sub(client, [account_id])
    client.patch(f"/api/v1/accounts/{account_id}", json={"enabled": False})
    assert client.get(subscription["clash_url"]).status_code == 503
    assert client.delete(f"/api/v1/accounts/{account_id}").status_code == 200
    assert client.get(subscription["clash_url"]).status_code == 404


def test_token_rotation_used_by_following_calls(client, account_id):
    service = client.app.state.service
    service.gateway.rotate = "b" * 32
    refresh(client, account_id)
    assert service.gateway.calls[0][0] == "a" * 32
    assert all(token == "b" * 32 for token, _ in service.gateway.calls[1:])
    with service.db() as db:
        assert service.secrets.decrypt(db.get(Account, account_id).token_cipher) == "b" * 32


def test_clear_token_removes_nodes(client, account_id):
    refresh(client, account_id)
    service = client.app.state.service
    service.gateway.clear = True
    refresh(client, account_id)
    assert client.get("/api/v1/accounts").json()[0]["status"] == "invalid"
    assert client.get("/api/v1/nodes").json() == []
    with service.db() as db:
        assert db.get(Account, account_id).token_cipher == ""


def test_info_failure_preserves_nodes(client, account_id):
    client.app.state.service.gateway.info_fail = True
    refresh(client, account_id)
    account = client.get("/api/v1/accounts").json()[0]
    assert account["node_count"] == 3 and account["status"] == "ready"
    assert account["info_error"]


def test_cache_cutoff_and_partial_accounts(client, account_id):
    refresh(client, account_id)
    second = client.post("/api/v1/accounts", json={"name": "第二账号", "token": "b" * 32}).json()["id"]
    refresh(client, second)
    subscription = sub(client, [account_id, second])
    with client.app.state.db.begin() as db:
        a = db.get(Account, second)
        a.last_success = time.time() - 3700
        a.next_refresh = time.time() + 1800
    result = client.get(subscription["clash_url"])
    assert result.status_code == 200
    assert "第二账号" not in result.text
    with client.app.state.db.begin() as db:
        a = db.get(Account, account_id)
        a.last_success = time.time() - 3700
        a.next_refresh = time.time() + 1800
    assert client.get(subscription["clash_url"]).status_code == 503


def test_same_pk_does_not_reset_first_seen(client, account_id):
    refresh(client, account_id)
    with client.app.state.db.begin() as db:
        db.get(Account, account_id).first_seen = 1000
    refresh(client, account_id)
    assert client.get("/api/v1/accounts").json()[0]["first_seen"] == 1000


def test_fresh_failed_probe_filter_and_aging(client, account_id):
    refresh(client, account_id)
    with client.app.state.db.begin() as db:
        node = db.scalar(select(Node).where(Node.account_id == account_id, Node.region == "HKT"))
        node.reachable, node.tested_at = False, time.time()
    subscription = client.post("/api/v1/subscriptions", json={"name": "筛选", "account_ids": [account_id],
                                "regions": ["HKT"], "exclude_failed": True}).json()
    assert client.get(subscription["clash_url"]).status_code == 503

    # 结论过期后生成时会重新探测；这里让探测返回可用，节点应重新放行
    async def reachable(proxy):
        return True, 12.0, "tcp"

    client.app.state.service._probe_proxy = reachable
    with client.app.state.db.begin() as db:
        node = db.scalar(select(Node).where(Node.region == "HKT"))
        node.tested_at = time.time() - 1900
    assert client.get(subscription["clash_url"]).status_code == 200


def test_replace_token_clears_old_snapshot(client, account_id):
    refresh(client, account_id)
    assert client.patch(f"/api/v1/accounts/{account_id}", json={"token": "c" * 32}).status_code == 200
    assert client.get("/api/v1/nodes").json() == []
    assert client.get("/api/v1/accounts").json()[0]["last_success"] is None


def test_settings_and_health(client):
    assert client.get("/readyz").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.put("/api/v1/settings", json={"refresh_minutes": 1}).status_code == 422
    assert client.put("/api/v1/settings", json={"refresh_minutes": 10}).status_code == 200
    assert client.get("/api/v1/settings").json()["refresh_minutes"] == 10
    assert client.get("/api/v1/settings").json()["probe_timeout_ms"] == 5000  # 未设置时取默认值
    assert client.put("/api/v1/settings", json={"refresh_minutes": 10, "probe_timeout_ms": 500}).status_code == 422
    assert client.put("/api/v1/settings", json={"refresh_minutes": 10, "probe_timeout_ms": 3000}).status_code == 200
    assert client.get("/api/v1/settings").json()["probe_timeout_ms"] == 3000


def base64_text(value: str) -> str:
    import base64
    return base64.b64encode(value.encode()).decode()


def offline_probe(client):
    async def probe(proxy):
        return True, 5.0, "tcp"

    client.app.state.service._probe_proxy = probe


def test_source_crud_and_merged_subscription(client, monkeypatch):
    async def fake_fetch(self, url):
        return "ss://" + base64_text("aes-128-gcm:pw") + "@ss.example.com:8388#节点A"

    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    offline_probe(client)
    created = client.post("/api/v1/sources",
                          json={"name": "机场", "url": "https://sub.example.com/x?token=secret"})
    assert created.status_code == 201
    body = created.json()
    assert body["node_count"] == 1
    assert body["address"] == "https://sub.example.com"  # 路径和 token 不外泄
    assert "secret" not in str(body)
    source_id = body["id"]

    assert client.post("/api/v1/subscriptions", json={"name": "空"}).status_code == 422
    subscription = client.post("/api/v1/subscriptions",
                               json={"name": "统一", "source_ids": [source_id]}).json()
    config = yaml.safe_load(client.get(subscription["clash_url"]).text)
    assert [p["type"] for p in config["proxies"]] == ["ss"]
    assert config["proxies"][0]["name"] == "机场-节点A"

    assert client.put(f"/api/v1/sources/{source_id}", json={"enabled": False}).status_code == 200
    assert client.get(subscription["clash_url"]).status_code == 503
    assert client.delete(f"/api/v1/sources/{source_id}").status_code == 200
    remaining = client.get("/api/v1/subscriptions").json()[0]
    assert remaining["source_ids"] == []
    assert remaining["enabled"] is False


def test_source_rejects_private_address(client):
    created = client.post("/api/v1/sources", json={"name": "内网", "url": "http://127.0.0.1:8000/x"})
    assert created.status_code == 201
    assert created.json()["node_count"] == 0
    assert "内网" in created.json()["last_error"]


def test_source_batch_import(client, monkeypatch):
    async def fake_fetch(self, url):
        host = url.split("//", 1)[1].split("/", 1)[0]
        return "ss://" + base64_text("aes-128-gcm:pw") + f"@{host}:8388#节点"

    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    offline_probe(client)
    result = client.post("/api/v1/sources/import", json={"sources": [
        {"name": "批量A", "url": "https://a.example.com/sub"},
        {"name": "批量B", "url": "https://b.example.com/sub"},
    ]})
    assert result.status_code == 201
    assert [item["node_count"] for item in result.json()] == [1, 1]
    assert len(client.get("/api/v1/sources").json()) == 2
    assert client.post("/api/v1/sources/import", json={"sources": []}).status_code == 422


def wait_job(client, job_id, timeout=5):
    import asyncio
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] not in {"pending", "running"}:
            return job
        client.portal.call(asyncio.sleep, 0.05)
    raise AssertionError(f"任务未在 {timeout} 秒内完成")


def test_source_check_and_bulk_cleanup(client, monkeypatch):
    async def fake_fetch(self, url):
        if "dead" in url:
            raise SourceError("订阅地址返回 HTTP 403")
        if "empty" in url:
            return "proxies: []"
        return "ss://" + base64_text("aes-128-gcm:pw") + "@ss.example.com:8388#节点A"

    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    offline_probe(client)
    created = client.post("/api/v1/sources/import", json={"sources": [
        {"name": "正常", "url": "https://ok.example.com/sub"},
        {"name": "过期", "url": "https://dead.example.com/sub"},
        {"name": "空订阅", "url": "https://empty.example.com/sub"},
    ]}).json()
    assert [item["invalid"] for item in created] == [False, True, True]
    assert created[1]["last_error"] == "订阅地址返回 HTTP 403"
    assert created[2]["node_count"] == 0

    # 地址恢复后重新检测：失效标记跟着最近一次拉取结果走
    async def healthy(self, url):
        return "ss://" + base64_text("aes-128-gcm:pw") + "@ss.example.com:8388#节点A"

    monkeypatch.setattr("app.sources.Fetcher.fetch", healthy)
    started = client.post("/api/v1/sources/check")
    assert started.status_code == 202
    finished = wait_job(client, started.json()["job_id"])
    assert finished["kind"] == "source" and finished["status"] == "completed"
    assert finished["total"] == finished["completed"] == 3 and finished["errors"] == []
    assert [s["invalid"] for s in client.get("/api/v1/sources").json()] == [False, False, False]

    # 再次失效，验证一键禁用与一键删除
    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    for item in client.get("/api/v1/sources").json():
        client.portal.call(client.app.state.service.refresh_source, item["id"])
    assert client.post("/api/v1/sources/invalid/disable").json() == {"disabled": 2}
    assert [s["enabled"] for s in client.get("/api/v1/sources").json()] == [True, False, False]
    assert wait_job(client, client.post("/api/v1/sources/check").json()["job_id"])["total"] == 1  # 只检测启用中的源

    ids = [s["id"] for s in client.get("/api/v1/sources").json()]
    assert client.post("/api/v1/subscriptions", json={"name": "清理", "source_ids": ids}).status_code == 201
    assert client.post("/api/v1/sources/invalid/delete").json() == {"deleted": 2}
    assert [s["name"] for s in client.get("/api/v1/sources").json()] == ["正常"]
    remaining = client.get("/api/v1/subscriptions").json()[0]
    assert remaining["source_ids"] == [ids[0]] and remaining["enabled"] is True


def test_source_check_requires_enabled_sources(client):
    assert client.post("/api/v1/sources/check").status_code == 422
    source = client.post("/api/v1/sources", json={"name": "停用", "url": "https://dead.example.com/x"}).json()
    assert client.put(f"/api/v1/sources/{source['id']}", json={"enabled": False}).status_code == 200
    assert client.post("/api/v1/sources/check").status_code == 422


def test_subscription_max_latency_filter(client, account_id):
    refresh(client, account_id)

    async def slow(proxy):
        return True, 800.0, "tcp"

    client.app.state.service._probe_proxy = slow
    strict = client.post("/api/v1/subscriptions", json={
        "name": "限速", "account_ids": [account_id], "max_latency": 200}).json()
    assert client.get(strict["clash_url"]).status_code == 503

    relaxed = client.put(f"/api/v1/subscriptions/{strict['id']}", json={
        "name": "限速", "account_ids": [account_id], "max_latency": 1000}).json()
    assert relaxed["max_latency"] == 1000
    assert client.get(relaxed["clash_url"]).status_code == 200


def test_source_url_change_resets_state(client, monkeypatch):
    async def failing(self, url):
        raise SourceError("无法连接订阅地址")

    monkeypatch.setattr("app.sources.Fetcher.fetch", failing)
    source_id = client.post("/api/v1/sources", json={"name": "旧", "url": "https://old.example.com/s"}).json()["id"]
    assert client.get("/api/v1/sources").json()[0]["invalid"] is True
    assert client.put(f"/api/v1/sources/{source_id}", json={"url": "https://new.example.com/s"}).status_code == 200
    view = client.get("/api/v1/sources").json()[0]
    assert view["failures"] == 0 and view["last_error"] is None and view["invalid"] is False
    assert view["address"] == "https://new.example.com"
    assert client.put(f"/api/v1/sources/{source_id}", json={"name": "   "}).status_code == 422


def test_direct_config_rejects_bad_latency(client, account_id):
    assert client.get(f"/api/v1/config/clash?account_ids={account_id}&max_latency=-1").status_code == 422
    assert client.get(f"/api/v1/config/clash?account_ids={account_id}&max_per_country=0").status_code == 422


def test_subscription_limits_nodes_per_country(client, monkeypatch):
    async def fake_fetch(self, url):
        return """proxies:
- {name: 🇺🇸 美国 01, type: ss, server: us1.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
- {name: 🇺🇸 洛杉矶 02, type: ss, server: us2.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
- {name: 🇯🇵 日本 01, type: ss, server: jp1.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
"""

    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    latencies = {"us1.example.com": 200.0, "us2.example.com": 20.0, "jp1.example.com": 90.0}

    async def probe(proxy):
        return True, latencies[proxy["server"]], "tcp"

    client.app.state.service._probe_proxy = probe
    source = client.post("/api/v1/sources", json={"name": "机场", "url": "https://sub.example.com/x"}).json()
    sub = client.post("/api/v1/subscriptions", json={
        "name": "每国限额", "source_ids": [source["id"]], "max_per_country": 1}).json()
    assert sub["max_per_country"] == 1
    config = yaml.safe_load(client.get(sub["clash_url"]).text)
    # 两个美国入口合并计数，只留延迟最低的那个
    assert [p["server"] for p in config["proxies"]] == ["us2.example.com", "jp1.example.com"]

    relaxed = client.put(f"/api/v1/subscriptions/{sub['id']}", json={
        "name": "每国限额", "source_ids": [source["id"]], "max_per_country": 2}).json()
    assert relaxed["max_per_country"] == 2
    assert len(yaml.safe_load(client.get(relaxed["clash_url"]).text)["proxies"]) == 3
    assert client.post("/api/v1/subscriptions", json={
        "name": "非法", "source_ids": [source["id"]], "max_per_country": 0}).status_code == 422


def test_subscription_node_details_and_manual_probe(client, monkeypatch):
    async def fake_fetch(self, url):
        return """proxies:
- {name: 🇺🇸 美国 01, type: ss, server: us1.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
- {name: 🇺🇸 洛杉矶 02, type: ss, server: us2.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
- {name: 🇯🇵 日本 01, type: ss, server: jp1.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
"""

    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    latencies = {"us1.example.com": 200.0, "us2.example.com": 20.0, "jp1.example.com": None}

    async def probe(proxy):
        latency = latencies[proxy["server"]]
        return latency is not None, latency, "tcp"

    client.app.state.service._probe_proxy = probe
    source = client.post("/api/v1/sources", json={"name": "机场", "url": "https://sub.example.com/x"}).json()
    sub = client.post("/api/v1/subscriptions", json={
        "name": "详情", "source_ids": [source["id"]], "exclude_failed": True, "max_per_country": 1}).json()
    client.post(f"/api/v1/subscriptions/{sub['id']}/probe", json={})  # 可能与创建时的后台测速并行
    job_id = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()["job_id"]
    if job_id:
        assert wait_job(client, job_id)["kind"] == "sub_probe"

    nodes = {n["server"]: n for n in client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()["nodes"]}
    assert "password" not in str(nodes)
    assert nodes["us2.example.com"]["included"] and nodes["us2.example.com"]["latency"] == 20.0
    assert nodes["us2.example.com"]["country_label"] == "🇺🇸 美国"
    assert nodes["us1.example.com"]["reason"] == "超出每国 1 个名额"
    assert nodes["jp1.example.com"]["reason"] == "测速失败"
    # 详情与实际生成的订阅一致
    config = yaml.safe_load(client.get(sub["clash_url"]).text)
    assert [p["server"] for p in config["proxies"]] == ["us2.example.com"]

    # 只测选中的节点
    latencies["jp1.example.com"] = 50.0
    started = client.post(f"/api/v1/subscriptions/{sub['id']}/probe",
                          json={"keys": [nodes["jp1.example.com"]["key"]]}).json()
    assert started["count"] == 1
    assert wait_job(client, started["job_id"])["status"] == "completed"
    nodes = {n["server"]: n for n in client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()["nodes"]}
    assert nodes["jp1.example.com"]["included"] and nodes["jp1.example.com"]["latency"] == 50.0
    assert client.post(f"/api/v1/subscriptions/{sub['id']}/probe", json={"keys": ["x"]}).status_code == 422
    assert client.get("/api/v1/subscriptions/missing/nodes").status_code == 404


def test_subscription_refresh_refetches_sources_and_reprobes(client, monkeypatch):
    fetched = []

    async def fake_fetch(self, url):
        fetched.append(url)
        return "ss://" + base64_text("aes-128-gcm:pw") + "@ss.example.com:8388#节点A"

    monkeypatch.setattr("app.sources.Fetcher.fetch", fake_fetch)
    offline_probe(client)
    source = client.post("/api/v1/sources", json={"name": "机场", "url": "https://sub.example.com/x"}).json()
    sub = client.post("/api/v1/subscriptions", json={"name": "刷新", "source_ids": [source["id"]]}).json()
    fetched.clear()

    started = client.post(f"/api/v1/subscriptions/{sub['id']}/refresh")
    assert started.status_code == 202 and started.json()["count"] == 1
    assert fetched == ["https://sub.example.com/x"]  # 缓存未过期也会强制重新拉取
    assert wait_job(client, started.json()["job_id"])["status"] == "completed"
    node = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()["nodes"][0]
    assert node["origin"] == "订阅源 · 机场" and node["latency"] == 5.0
    assert client.post("/api/v1/subscriptions/missing/refresh").status_code == 404


def test_source_file_import_and_reupload(client, monkeypatch):
    async def must_not_fetch(self, url):
        raise AssertionError("文件源不应发起网络请求")

    monkeypatch.setattr("app.sources.Fetcher.fetch", must_not_fetch)
    offline_probe(client)
    content = """port: 7890
proxies:
- {name: 🇯🇵 日本 01, type: ss, server: jp1.example.com, port: 8388, cipher: aes-128-gcm, password: pw}
- {name: 🇺🇸 美国 01, type: trojan, server: us1.example.com, port: 443, password: pw}
proxy-groups: []
"""
    created = client.post("/api/v1/sources/file", json={"name": "导出文件", "content": content})
    assert created.status_code == 201
    source = created.json()
    assert source["kind"] == "file" and source["node_count"] == 2 and source["address"] == "本地文件"
    assert source["invalid"] is False

    sub = client.post("/api/v1/subscriptions", json={"name": "文件", "source_ids": [source["id"]]}).json()
    config = yaml.safe_load(client.get(sub["clash_url"]).text)
    assert [p["server"] for p in config["proxies"]] == ["jp1.example.com", "us1.example.com"]
    assert client.post(f"/api/v1/sources/{source['id']}/refresh").json() == {"node_count": 2}
    assert client.post(f"/api/v1/subscriptions/{sub['id']}/refresh").status_code == 202
    assert wait_job(client, client.post("/api/v1/sources/check").json()["job_id"])["errors"] == []

    replaced = client.put(f"/api/v1/sources/{source['id']}/file", json={
        "content": "ss://" + base64_text("aes-128-gcm:pw") + "@hk.example.com:8388#香港"})
    assert replaced.status_code == 200 and replaced.json()["node_count"] == 1
    assert client.put(f"/api/v1/sources/{source['id']}", json={"url": "https://x.example.com/s"}).status_code == 422
    assert client.post("/api/v1/sources/file", json={"name": "坏", "content": "hello"}).status_code == 422


def test_source_file_accepts_up_to_10mb(client):
    offline_probe(client)
    line = "- {name: 节点, type: ss, server: jp1.example.com, port: 8388, cipher: aes-128-gcm, password: pw}\n"
    # 约 8 MB 的导出文件（大段注释 + 一个节点），超过旧的 5 MB 限制但在 10 MB 以内
    content = "# " + "x" * 8_000_000 + "\nproxies:\n" + line
    created = client.post("/api/v1/sources/file", json={"name": "大文件", "content": content})
    assert created.status_code == 201 and created.json()["node_count"] == 1
    too_big = "# " + "x" * 10_000_001 + "\nproxies:\n" + line
    assert client.post("/api/v1/sources/file", json={"name": "过大", "content": too_big}).status_code == 422
    # 其他接口仍按 1 MB 限制
    assert client.post("/api/v1/sources", content=b"x" * 1_000_001).status_code == 413


def test_version_comes_from_single_source(client):
    """版本号只写在仓库根目录的 VERSION 里，后端读取后也要对外暴露同一致的值。"""
    from app.config import APP_VERSION, VERSION_FILE

    assert APP_VERSION == VERSION_FILE.read_text(encoding="utf-8").strip().removeprefix("v")
    assert client.get("/api/v1/settings").json()["version"] == APP_VERSION
