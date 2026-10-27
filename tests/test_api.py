import time

import yaml
from sqlalchemy import select

from app.models import Account, Node


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
    assert client.post("/api/v1/accounts", json={"token": "a" * 32}).status_code == 403
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    assert client.post("/api/v1/accounts", json={"token": "a" * 32}).status_code == 201
    assert client.post("/api/v1/auth/logout").status_code == 200
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
