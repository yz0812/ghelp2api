import asyncio
import time

import httpx
import pytest

from app.main import create_app
from app.models import Account, Job, Node
from app.security import hint
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
