import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text

from app import source_impact
from app.models import Source
from app.service import proxy_key
from tests.test_chain_orchestration import path_for, post
from tests.test_chains import setup_chain


def content(*names):
    return yaml.safe_dump({"proxies": [node(name) for name in names]})


def node(name):
    return dict(name=name, type="http", server=name + ".example.invalid", port=8080)


def source_url(source):
    return "/api/v1/sources/" + source["id"]


def replace(client, source, payload):
    response = client.put(source_url(source) + "/file", json={"content": payload})
    assert response.status_code == 200, response.text
    return client.get(source_url(source) + "/impact").json()


@pytest.mark.parametrize(("remaining", "level", "indexes"), [
    (("a", "c"), "backup_lost", [1]), (("b",), "primary_lost", [0]), (("c",), "all_lost", [0, 1])])
def test_path_losses_recovery_and_historical_report(client, remaining, level, indexes):
    _, entry, exit_row, _ = setup_chain(client)
    source = post(client, "/sources/file", dict(name="source", content=content("a", "b", "c")))
    sub = post(client, "/subscriptions", dict(name="sub", source_ids=[source["id"]]))
    nodes = client.get("/api/v1/subscriptions/" + sub["id"] + "/nodes").json()["nodes"]
    paths = [path_for(entry, exit_row, [dict(source="subscription", ref=sub["id"], node_key=n["key"])])
             for n in nodes[:2]]
    chain = post(client, "/chains", dict(name="chain", paths=paths, monitor_enabled=False))
    result = replace(client, source, content(*remaining))
    report = result["report"]
    assert result["status"] == "analyzed" and report["version"] == 2
    change, = report["chains"]
    assert (change["id"], change["revision"], change["level"]) == (chain["id"], 1, level)
    assert [p["path_index"] for p in change["paths"]] == indexes
    assert all(p["hops"] == [dict(hop_index=1, reason="missing", before=1, after=0)] for p in change["paths"])
    assert "example." not in json.dumps(report)
    assert report["subscriptions"][0]["removed"] == 3 - len(remaining)
    assert replace(client, source, content(*remaining)) == dict(version=3, status="unchanged", report=report)
    assert client.put(source_url(source) + "/file", json={"content": "invalid"}).status_code == 422
    assert client.get(source_url(source) + "/impact").json()["report"] == report
    restored = replace(client, source, content("a", "b", "c"))["report"]["chains"][0]
    assert restored["level"] == "restored" and restored["complete_paths"] == 2
    del client.headers["Authorization"]
    assert client.get(source_url(source) + "/impact").status_code == 401


def test_alternate_provider_and_name_changes(client):
    first = post(client, "/sources/file", dict(name="first", content=content("a", "b")))
    second = post(client, "/sources/file", dict(name="other", content=content("a")))
    sub = post(client, "/subscriptions", dict(name="combined", source_ids=[first["id"], second["id"]]))
    _, entry, exit_row, _ = setup_chain(client)
    key = client.get("/api/v1/subscriptions/" + sub["id"] + "/nodes").json()["nodes"][0]["key"]
    path = path_for(entry, exit_row, [dict(source="subscription", ref=sub["id"], node_key=key)])
    post(client, "/chains", dict(name="chain", paths=[path], monitor_enabled=False))
    report = replace(client, first, content("b"))["report"]
    assert report["removed"] == 1 and report["chains"] == report["subscriptions"] == []
    renamed = {"proxies": [dict(node("a"), name="elsewhere")]}
    assert replace(client, second, yaml.safe_dump(renamed))["status"] == "unchanged"
    different = {"proxies": [dict(node("c"), name="elsewhere")]}
    assert replace(client, second, yaml.safe_dump(different))["report"]["chains"][0]["level"] == "all_lost"


def test_legacy_pool_fixed_and_preexisting_damage(client):
    sub, entry, exit_row, legacy = setup_chain(client)
    source = {"id": sub["source_ids"][0]}
    nodes = client.get("/api/v1/entries/" + entry["id"] + "/nodes").json()
    path = path_for(entry, exit_row, strategy="fallback", ordered_keys=[nodes[0]["key"]])
    fixed = post(client, "/chains", dict(name="fallback", paths=[path], monitor_enabled=False))
    payload = {"proxies": [dict(name="entry-b", type="http", server="entry2.example.com", port=8080)]}
    report = replace(client, source, yaml.safe_dump(payload))["report"]
    changes = {c["id"]: c for c in report["chains"]}
    assert changes[legacy["id"]]["paths"][0]["hops"][0]["reason"] == "reduced"
    assert changes[fixed["id"]]["level"] == "all_lost"
    payload["proxies"].append(node("extra"))
    changes = replace(client, source, yaml.safe_dump(payload))["report"]["chains"]
    assert [c["id"] for c in changes] == [legacy["id"]]


def test_stale_writer_analysis_failure_and_restart(client, monkeypatch):
    service = client.app.state.service
    source = post(client, "/sources/file", dict(name="synthetic", content=content("a")))
    entered, release = Event(), Event()
    analyze = source_impact.analyze

    def delayed(*args):
        if args[-1] == 2:
            entered.set()
            assert release.wait(10)
        return analyze(*args)

    monkeypatch.setattr(source_impact, "analyze", delayed)
    with ThreadPoolExecutor() as pool:
        old = pool.submit(service.commit_source_refresh, source["id"], 1, [node("b")])
        try:
            assert entered.wait(10)
            assert service.commit_source_refresh(source["id"], 2, [node("c")])
        finally:
            release.set()
        assert old.result()
    assert not service.commit_source_refresh(source["id"], 1, [node("b")])
    report = client.get(source_url(source) + "/impact").json()["report"]
    assert report["version"] == 3

    def fail(*args):
        raise ValueError("private diagnostic")

    monkeypatch.setattr(source_impact, "analyze", fail)
    result = replace(client, source, content("d"))
    assert result == dict(version=4, status="failed", report=report)
    with service.db.begin() as db:
        row = db.get(Source, source["id"])
        assert row.proxies[0]["name"] == "d"
        row.impact_status = "pending"
    client.portal.call(service.stop)
    client.portal.call(service.start)
    result = client.get(source_url(source) + "/impact").json()
    assert result == dict(version=4, status="incomplete", report=report)


def test_upgrade_existing_source(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    config = Config("alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0012")
        conn.execute(text("""INSERT INTO sources (id,name,url_cipher,enabled,proxies,last_fetch,failures,created_at)
                          VALUES ('id','source','cipher',1,'[]',1,0,0)"""))
        command.upgrade(config, "head")
        assert conn.execute(text("SELECT refresh_version,impact_status,impact_report FROM sources")).one() == (
            1, "baseline", None)
    engine.dispose()


def test_large_sample_matches_runtime_without_per_hop_queries(client):
    service = client.app.state.service
    sub, entry, exit_row, _ = setup_chain(client)
    source = {"id": sub["source_ids"][0]}
    for i in range(6):
        post(client, "/chains", dict(name=f"sample-{i}", paths=[path_for(entry, exit_row)], monitor_enabled=False))
    with service.db() as db:
        engine = db.get_bind()
    queries = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            queries.append(statement.split()[0])

    event.listen(engine, "before_cursor_execute", count)
    started = time.perf_counter()
    try:
        assert service.commit_source_refresh(source["id"], 1, [node(str(i)) for i in range(1000)])
    finally:
        event.remove(engine, "before_cursor_execute", count)
    elapsed = time.perf_counter() - started
    changes = client.get(source_url(source) + "/impact").json()["report"]["chains"]
    expected = client.get("/api/v1/entries/" + entry["id"] + "/nodes").json()
    assert len(expected) == 1000 and all(c["paths"][0]["hops"][0]["after"] == len(expected) for c in changes)
    assert len(changes) == 7 and len(queries) <= 10
    print(f"1000 nodes, 7 chains: {elapsed:.3f}s, {len(queries)} SELECTs")


def test_same_fingerprint_name_changes_country_filter(client, monkeypatch):
    from app.models import ProxyCheck
    from app.subscription import parse_subscription

    raw = {"proxies": [dict(node("a"), name="日本 A"), dict(node("b"), name="美国 B")]}
    source = post(client, "/sources/file", dict(name="geo", content=yaml.safe_dump(raw)))
    service = client.app.state.service
    # Keep the seeded probe results stable while comparing the two country filters.
    monkeypatch.setattr(service, "schedule_probe", lambda sub_id: None)
    sub = post(client, "/subscriptions", dict(name="limited", max_per_country=1, source_ids=[source["id"]]))
    with service.db.begin() as db:
        for i, proxy in enumerate(parse_subscription(yaml.safe_dump(raw))):
            db.add(ProxyCheck(key=proxy_key(proxy), reachable=True, latency=i+1, method="http", tested_at=time.time()))
    raw["proxies"][1]["name"] = "日本 B"
    report = replace(client, source, yaml.safe_dump(raw))["report"]
    assert report["added"] == report["removed"] == 0
    assert report["subscriptions"] == [dict(id=sub["id"], enabled=True, added=0, removed=1)]


def test_url_refresh_failure_and_new_address_baseline(client, monkeypatch):
    from app.sources import SourceError

    replies = iter([content("a"), content("b"), None, content("c")])

    async def fetch(url):
        result = next(replies)
        if result is None:
            raise SourceError("无法连接订阅地址")
        return result

    service = client.app.state.service
    monkeypatch.setattr(service.fetcher, "fetch", fetch)
    source = post(client, "/sources", dict(name="url", url="https://test.example.invalid/old"))
    url = source_url(source)
    assert client.post(url + "/refresh").json() == {"node_count": 1}
    previous = client.get(url + "/impact").json()
    assert previous["report"]["removed"] == 1 and previous["report"]["added"] == 1
    assert client.post(url + "/refresh").status_code == 502
    assert client.get(url + "/impact").json() == previous
    with service.db() as db:
        row = db.get(Source, source["id"])
        assert row.proxies[0]["name"] == "b" and row.failures == 1
    assert client.put(url, json={"url": "https://test.example.invalid/new"}).is_success
    assert client.post(url + "/refresh").is_success
    assert client.get(url + "/impact").json() == dict(version=4, report=None, status="baseline")
