from urllib.parse import urlsplit

import pytest
import yaml

ROOT = "/api/v1/convert"
LINK = "vless://test@example.org:443?security=reality&pbk=key&sid=0012&flow=xtls-rprx-vision#A"


def publish(client, **options):
    result = client.post(ROOT + "/publish", json={"content": LINK, "name": "手机", **options})
    assert result.status_code == 201, result.text
    record = result.json()
    return record, urlsplit(record["clash_url"]).path


@pytest.mark.parametrize("template", ["basic", "enhanced"])
def test_preview_download_publish_identical(client, template):
    data = {"content": LINK + "\n" + LINK.replace("#A", "#Apple"), "template": template}
    preview = client.post(ROOT + "/preview", json=data).json()
    assert preview["count"] == 2 and preview["issues"] == []
    offline = client.post(ROOT, json=data).text
    sub, path = publish(client, **data, request_key="replay")
    assert client.get(path).text == offline == preview["yaml"]
    pure = client.get(urlsplit(sub["proxies_url"]).path).text
    assert pure == client.post(ROOT + "?full=false", json=data).text
    assert list(yaml.safe_load(pure)) == ["proxies"]
    assert publish(client, **data, request_key="replay")[0]["id"] == sub["id"]
    collision = client.post(ROOT + "/publish", json={**data, "request_key": "replay", "name": "其他"})
    assert collision.status_code == 409
    listing = client.get(ROOT + "/records?q=手机&enabled=true").json()
    assert listing["total"] == 1
    assert "clash_url" not in listing["items"][0]
    assert not client.get(ROOT + "/records?q=其他").json()["items"]
    config = yaml.safe_load(offline)
    names = {p["name"] for p in config["proxies"]}
    groups = {g["name"] for g in config["proxy-groups"]}
    assert not names & groups
    for group in config["proxy-groups"]:
        assert set(group["proxies"]) <= names | groups | {"DIRECT", "REJECT"}
    assert ("rule-providers" in config) == (template == "enhanced")


def test_partial_edit_and_lifecycle(client):
    sub, path = publish(client)
    endpoint = ROOT + "/records/" + sub["id"]
    bad = {"name": "修改", "content": LINK.replace("example.org", "new.example") + "\ninvalid", "version": 1}
    preview = client.post(ROOT + "/preview?record_id=" + sub["id"], json=bad).json()
    assert preview["changes"] == {"added": 0, "removed": 0, "modified": 1}
    assert "第 2 行" in preview["issues"][0]
    original = client.get(path).text
    assert client.put(endpoint, json=bad).status_code == 422
    assert client.get(path).text == original
    saved = client.put(endpoint, json={**bad, "allow_partial": True})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    assert client.put(endpoint, json={**bad, "allow_partial": True}).status_code == 409
    assert client.get(path).text == preview["yaml"]
    assert client.patch(endpoint + "?enabled=false").is_success
    assert client.get(path).status_code == 404
    assert client.patch(endpoint + "?enabled=true").is_success
    assert client.get(path).status_code == 200
    rotated = client.post("/api/v1/subscriptions/" + sub["id"] + "/rotate").json()
    assert client.get(path).status_code == 404
    assert client.get(urlsplit(rotated["clash_url"]).path).status_code == 200
    assert client.delete("/api/v1/sources/" + sub["source_ids"][0]).status_code == 409
    entry = client.post("/api/v1/entries", json={"name": "关联入口", "subscription_id": sub["id"]})
    assert entry.is_success
    assert client.delete("/api/v1/subscriptions/" + sub["id"]).status_code == 409
    assert client.delete("/api/v1/entries/" + entry.json()["id"]).is_success
    assert client.delete("/api/v1/subscriptions/" + sub["id"]).is_success
    assert client.get(ROOT + "/records").json()["total"] == 0
    assert client.get("/api/v1/sources").json() == []


def test_legacy_candidate_adoption_and_shared_edit(client):
    source = client.post("/api/v1/sources/file", json={"name": "转换订阅 · A", "content": LINK}).json()
    old = client.post("/api/v1/subscriptions", json={"name": source["name"], "source_ids": [source["id"]]}).json()
    other = client.post("/api/v1/subscriptions", json={"name": "另一个订阅", "source_ids": [source["id"]]}).json()
    path, other_path = (urlsplit(s["clash_url"]).path for s in (old, other))
    previous = client.get(path).text
    assert client.get(ROOT + "/candidates").json()[0]["id"] == old["id"]
    assert client.post(ROOT + "/adopt/" + old["id"]).is_success
    assert client.get(path).text == previous
    endpoint = ROOT + "/records/" + old["id"]
    detail = client.get(endpoint).json()
    assert detail["created_at"] is None and detail["template"] == "legacy"
    assert client.put(endpoint, json={"name": "改名", "content": LINK.replace("#A", "#B"), "version": 1}).is_success
    assert client.get(other_path).text == previous
    assert client.get(path).text != previous
    assert client.delete("/api/v1/subscriptions/" + old["id"]).is_success
    assert client.get(other_path).text == previous


def test_published_source_copy_on_write(client):
    sub, path = publish(client)
    other = client.post("/api/v1/subscriptions", json={"name": "引用", "source_ids": sub["source_ids"]}).json()
    other_path = urlsplit(other["clash_url"]).path
    before = client.get(other_path).text
    assert client.put(ROOT + "/records/" + sub["id"], json={
        "name": "独立", "content": LINK.replace("#A", "#B"), "version": 1}).is_success
    assert client.get(other_path).text == before
    assert yaml.safe_load(client.get(path).text)["proxies"][0]["name"] == "B"


def test_conversion_probes_use_published_names_and_shared_results(client, monkeypatch):
    from tests.test_api import wait_job

    first = LINK.replace("#A", "#Apple")
    second = first.replace("example.org", "other.example")
    sub, path = publish(client, content="\n".join([first, first, second]), template="enhanced")
    config = client.get(path).text
    endpoint = "/api/v1/subscriptions/" + sub["id"]
    nodes = client.get(endpoint + "/nodes").json()["nodes"]
    assert [n["name"] for n in nodes] == [p["name"] for p in yaml.safe_load(config)["proxies"]]
    assert all(n["included"] and n["tested_at"] is None for n in nodes)
    assert nodes[0]["key"] == nodes[1]["key"] != nodes[2]["key"]

    measured = []

    async def probe(server, port):
        measured.append(server)
        return (True, 25, "tcp") if server == "example.org" else (False, None, "tcp")

    monkeypatch.setattr(client.app.state.service, "probe_tcp", probe)
    selected = client.post(endpoint + "/probe", json={"keys": [nodes[1]["key"]]})
    assert selected.status_code == 202 and selected.json()["count"] == 1
    wait_job(client, selected.json()["job_id"])
    after = client.get(endpoint + "/nodes").json()["nodes"]
    assert [n["latency"] for n in after] == [25, 25, None]
    assert after[0]["method"] == "tcp" and after[2]["tested_at"] is None
    assert measured == ["example.org"]

    all_nodes = client.post(endpoint + "/probe", json={"keys": None}).json()
    assert all_nodes["count"] == 2
    wait_job(client, all_nodes["job_id"])
    after = client.get(endpoint + "/nodes").json()["nodes"]
    assert all(n["tested_at"] and n["included"] for n in after)
    assert after[2]["reachable"] is False
    assert client.get(path).text == config


def test_upgrade_legacy_database_copy_and_restart(settings, tmp_path):
    import shutil

    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.main import create_app
    from app.models import Source, Subscription
    from app.security import Secrets, digest
    from app.subscription import parse_subscription

    legacy = tmp_path / "before.db"
    engine = create_engine("sqlite:///" + legacy.as_posix())
    config = Config("alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0013")
    key = "legacy-test-key"
    with Session(engine) as db, db.begin():
        source = Source(name="转换订阅 · A", kind="file", url_cipher="", proxies=parse_subscription(LINK))
        db.add(source)
        db.flush()
        sub = Subscription(name=source.name, source_ids=[source.id], account_ids=[],
                           key_hash=digest(key), key_cipher=Secrets(settings.encryption_key).encrypt(key))
        db.add(sub)
        db.flush()
        sub_id = sub.id
    engine.dispose()
    shutil.copyfile(legacy, settings.data_dir / "ghelper.db")
    path = "/sub/" + key + "/clash.yaml"
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.admin_api_key
        assert client.get(ROOT + "/records").json()["total"] == 0
        previous = client.get(path)
        assert previous.is_success
        assert client.post(ROOT + "/adopt/" + sub_id).is_success
        record, published = publish(client)
    with TestClient(create_app(settings)) as client:
        assert client.get(path).text == previous.text
        assert client.get(published).status_code == 200
        assert client.get(ROOT + "/records").status_code == 401
        client.headers["Authorization"] = "Bearer " + settings.admin_api_key
        assert {r["id"] for r in client.get(ROOT + "/records").json()["items"]} == {sub_id, record["id"]}
