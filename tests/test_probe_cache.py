import time

from app.chains import ChainManager
from app.models import Chain, Exit, ProxyCheck, Subscription
from app.service import CHECK_TTL
from tests.test_chains import setup_chain


def test_complete_results_survive_manager_recreation_and_config_changes(client, monkeypatch):
    sub, entry, exit, chain = setup_chain(client)
    service = client.app.state.service

    async def measure(proxies, group):
        return {group['proxies'][0]: 37}

    monkeypatch.setattr(service.chains, '_measure', measure)
    for kind, resource in [('exits', exit), ('chains', chain)]:
        result = client.post(f"/api/v1/{kind}/{resource['id']}/probe").json()
        assert result['reachable'] and result['latency'] == 37
        listed = client.get(f'/api/v1/{kind}').json()[0]['probe']
        assert listed == result
        with service.db() as db:
            row = db.get(Exit if kind == 'exits' else Chain, resource['id'])
        assert ChainManager(service).cached_result(row) == result
        with service.db.begin() as db:
            check = db.get(ProxyCheck, service.chains.result_key(row))
            check.tested_at -= CHECK_TTL * 2
            check.result = dict(check.result, tested_at=check.tested_at)
        assert client.get(f'/api/v1/{kind}').json()[0]['probe']['latency'] == 37

    renamed = client.put(f"/api/v1/exits/{exit['id']}", json={
        'name': '改名', 'server': exit['server'], 'port': exit['port'], 'username': exit['username']})
    assert renamed.status_code == 200
    assert renamed.json()['probe']['latency'] == 37
    assert client.get('/api/v1/chains').json()[0]['probe']['latency'] == 37
    changed = client.put(f"/api/v1/exits/{exit['id']}", json={
        'name': '改名', 'server': 'new.example.invalid', 'port': 8080})
    assert changed.status_code == 200 and changed.json()['probe'] is None
    assert client.get('/api/v1/chains').json()[0]['probe'] is None


def test_failures_including_no_candidates_are_cached(client, monkeypatch):
    sub, entry, exit, chain = setup_chain(client)
    service = client.app.state.service

    async def failed(*args):
        return {}

    monkeypatch.setattr(service.chains, '_measure', failed)
    with service.db.begin() as db:
        db.get(Subscription, sub['id']).enabled = False
    for kind, resource in [('exits', exit), ('chains', chain)]:
        result = client.post(f"/api/v1/{kind}/{resource['id']}/probe").json()
        assert result['reachable'] is False and result['error']
        assert client.get(f'/api/v1/{kind}').json()[0]['probe'] == result


def test_expired_node_history_visible_but_not_used_for_health_filter(client):
    sub, _, _, _ = setup_chain(client)
    service = client.app.state.service
    path = f"/api/v1/subscriptions/{sub['id']}/nodes"
    node = client.get(path).json()['nodes'][0]
    timestamp = time.time() - CHECK_TTL * 2
    with service.db.begin() as db:
        db.get(Subscription, sub['id']).exclude_failed = True
        db.add(ProxyCheck(key=node['key'], reachable=False, latency=None, method='http', tested_at=timestamp))
    previous = next(n for n in client.get(path).json()['nodes'] if n['key'] == node['key'])
    assert previous['reachable'] is False and previous['tested_at'] == timestamp
    assert previous['included'] is True  # Expired failure cannot exclude a current candidate.
    second = client.post('/api/v1/subscriptions', json={'name': '共享结果', 'source_ids': sub['source_ids']}).json()
    shared = client.get(f"/api/v1/subscriptions/{second['id']}/nodes").json()['nodes'][0]
    assert shared['tested_at'] == timestamp and shared['reachable'] is False
    # An unfinished background attempt must not erase the last actual measurement.
    service._store_checks([ProxyCheck(key=node['key'], reachable=False, latency=None,
                                     method='timeout', tested_at=time.time())])
    previous = next(n for n in client.get(path).json()['nodes'] if n['key'] == node['key'])
    assert previous['tested_at'] == timestamp and previous['reachable'] is False


def test_account_node_list_reads_shared_cache(client, account_id):
    from app.service import proxy_key
    service = client.app.state.service
    client.portal.call(service.ensure_refresh, account_id)
    node = client.get('/api/v1/nodes').json()[0]
    key = proxy_key({'type': 'http', 'server': node['server'], 'port': node['port'],
                     'tls': True, 'skip-cert-verify': True, 'udp': False})
    timestamp = time.time() - CHECK_TTL * 2
    with service.db.begin() as db:
        db.merge(ProxyCheck(key=key, reachable=True, latency=51, method='mihomo', tested_at=timestamp))
    restored = next(n for n in client.get('/api/v1/nodes').json() if n['id'] == node['id'])
    assert restored['latency'] == 51 and restored['tested_at'] == timestamp


def test_legacy_chain_measurement_is_still_displayed(client):
    _, _, _, chain = setup_chain(client)
    service = client.app.state.service
    with service.db() as db:
        resource = db.get(Chain, chain['id'])
    proxies, _ = service.chains.build(resource)
    timestamp = time.time() - CHECK_TTL * 2
    with service.db.begin() as db:
        db.add(ProxyCheck(key=service.chains.check_key(proxies), reachable=True,
                          latency=123, method='chain', tested_at=timestamp))
    result = client.get('/api/v1/chains').json()[0]['probe']
    assert result['latency'] == 123 and result['tested_at'] == timestamp
