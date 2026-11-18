import json
import subprocess
from pathlib import Path

import pytest
import yaml

from app.models import Chain, Entry, Exit, Source, Subscription


def setup_chain(client, fixed=False):
    source = client.post('/api/v1/sources/file', json={"name": "入口源", "content": """proxies:
  - {name: entry-a, type: http, server: entry.example.com, port: 8080}
  - {name: entry-b, type: http, server: entry2.example.com, port: 8080}
"""})
    assert source.status_code == 201, source.text
    sub = client.post('/api/v1/subscriptions', json={"name": "入口订阅", "source_ids": [source.json()['id']]}).json()
    key = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()['nodes'][0]['key']
    entry = client.post('/api/v1/entries', json={"name": "入口", "subscription_id": sub['id'],
                                               "mode": "fixed" if fixed else "auto", "node_key": key})
    assert entry.status_code == 201, entry.text
    exit = client.post('/api/v1/exits', json={"name": "出口", "content": '- {name: home, type: http, server: exit.example.com, port: 80, username: synthetic-user, password: synthetic-password}'})
    assert exit.status_code == 201, exit.text
    chain = client.post('/api/v1/chains', json={"name": "测试链路", "entry_id": entry.json()['id'], "exit_id": exit.json()['id']})
    assert chain.status_code == 201, chain.text
    return sub, entry.json(), exit.json(), chain.json()


def test_system_proxy_host_connection_export_and_precedence(client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.models import Preference

    service = client.app.state.service
    service.settings.public_base_url = 'https://web.example.com'
    _, _, _, chain = setup_chain(client)
    endpoint = f"/api/v1/chains/{chain['id']}/connection"
    assert client.get(endpoint).json()['host'] == 'proxy.accsunny.com'
    response = client.put('/api/v1/settings', json={
        'refresh_minutes': 10, 'proxy_public_host': ' proxy.example.com ',
    })
    assert response.status_code == 200, response.text
    assert response.json()['proxy_public_host'] == 'proxy.example.com'
    with service.db() as db:
        assert db.get(Preference, 'general').value['proxy_public_host'] == 'proxy.example.com'
    assert client.get(endpoint).json()['host'] == 'proxy.example.com'

    # Older clients updating other settings must not erase the proxy hostname.
    assert client.put('/api/v1/settings', json={'refresh_minutes': 15}).status_code == 200
    assert client.get('/api/v1/settings').json()['proxy_public_host'] == 'proxy.example.com'
    monkeypatch.setattr(service.chains, 'sync', AsyncMock())
    service.chains.status[chain['id']] = {'state': 'running'}
    sub = client.post('/api/v1/subscriptions', json={
        'name': '中转订阅', 'chain_ids': [chain['id']], 'chain_mode': 'relay',
    }).json()
    response = client.get(sub['proxies_url'])
    assert response.status_code == 200, response.text
    assert yaml.safe_load(response.text)['proxies'][0]['server'] == 'proxy.example.com'

    with service.db.begin() as db:
        db.get(Chain, chain['id']).public_host = 'override.example.com'
    assert client.get(endpoint).json()['host'] == 'override.example.com'
    with service.db.begin() as db:
        db.get(Chain, chain['id']).public_host = ''
    assert client.put('/api/v1/settings', json={
        'refresh_minutes': 15, 'proxy_public_host': '',
    }).status_code == 200
    assert client.get(endpoint).json()['host'] == 'web.example.com'


def test_native_graph_and_provider_fail_closed(client, tmp_path):
    _, _, _, chain = setup_chain(client)
    sub = client.post('/api/v1/subscriptions', json={"name": "仅链路", "chain_ids": [chain['id']], "chain_mode": "native"}).json()
    response = client.get(sub['clash_url'])
    assert response.status_code == 200, response.text
    config = yaml.safe_load(response.text)
    assert len(config['proxies']) == 4
    names = {p['name'] for p in config['proxies']}
    assert all(p['dialer-proxy'] in names for p in config['proxies'] if 'dialer-proxy' in p)
    auto = next(g for g in config['proxy-groups'] if g['name'] == '自动选择')
    assert auto['proxies'] == [g['name'] for g in config['proxy-groups'] if g['name'].startswith('链路 ·')]
    assert client.get(sub['proxies_url']).status_code == 503  # no core: never leak the bare exit
    binary = (Path('.cache/mihomo/mihomo.exe') if Path('.cache/mihomo/mihomo.exe').exists() else Path('.cache/mihomo/mihomo')).resolve()
    if binary.is_file():
        config_file = tmp_path / 'native.yaml'
        # Validate the graph offline, without triggering a GeoIP database download.
        config['rules'] = ['MATCH,自动选择']
        config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        result = subprocess.run([str(binary), '-t', '-d', str(tmp_path), '-f', str(config_file)], capture_output=True, timeout=20)
        assert result.returncode == 0


def test_merge_chain_into_existing_subscription_and_refresh(client, monkeypatch):
    from unittest.mock import AsyncMock

    sub, _, _, chain = setup_chain(client)
    manager = client.app.state.service.chains
    with client.app.state.db.begin() as db:
        db.get(Subscription, sub['id']).chain_mode = 'native'  # Existing subscription without chains.
    ordinary = yaml.safe_load(client.get(sub['clash_url']).text)['proxies']
    payload = {'name': sub['name'], 'source_ids': sub['source_ids'], 'chain_ids': [chain['id']]}
    endpoint = '/api/v1/subscriptions/' + sub['id']
    merged = client.put(endpoint, json=payload).json()
    assert merged['chain_mode'] == 'relay' and merged['clash_url'] == sub['clash_url']
    monkeypatch.setattr(manager, 'sync', AsyncMock())
    manager.status[chain['id']] = {'state': 'running'}
    for url in (sub['clash_url'], sub['proxies_url']):
        response = client.get(url)
        assert response.headers['profile-update-interval'] == '1'
        config = yaml.safe_load(response.text)
        assert config['proxies'][:-1] == ordinary
        assert config['proxies'][-1]['name'] == chain['name']
        assert config['proxies'][-1]['port'] == chain['listen_port']
        for group in config.get('proxy-groups', [])[:2]:
            assert group['proxies'].count(chain['name']) == 1
    with client.app.state.db.begin() as db:
        db.get(Chain, chain['id']).name = ordinary[0]['name']
    assert yaml.safe_load(client.get(sub['clash_url']).text)['proxies'][-1]['name'] == ordinary[0]['name'] + ' · 2'
    manager.status[chain['id']] = {'state': 'error'}
    assert yaml.safe_load(client.get(sub['clash_url']).text)['proxies'] == ordinary
    assert client.put(endpoint, json=dict(payload, chain_mode='native')).json()['chain_mode'] == 'native'
    assert client.put(endpoint, json=payload).json()['chain_mode'] == 'native'


def test_secrets_and_references(client):
    sub, entry, exit, chain = setup_chain(client)
    assert exit['has_password'] and 'password' not in exit
    assert 'password' not in json.dumps(client.get('/api/v1/chains').json())
    assert 'synthetic-password' not in client.get('/api/v1/exits').text
    with client.app.state.db() as db:
        assert 'synthetic-password' not in db.get(Exit, exit['id']).config_cipher
    for kind, id in [('subscriptions', sub['id']), ('entries', entry['id']), ('exits', exit['id'])]:
        assert client.delete(f'/api/v1/{kind}/{id}').status_code == 409
    conn = client.get(f"/api/v1/chains/{chain['id']}/connection").json()
    rotated = client.post(f"/api/v1/chains/{chain['id']}/rotate").json()
    assert conn['password'] != rotated['password']
    assert client.delete(f"/api/v1/chains/{chain['id']}").status_code == 200
    assert client.delete(f"/api/v1/exits/{exit['id']}").status_code == 200


def test_fixed_entry_and_password_preserved(client):
    _, entry, exit, chain = setup_chain(client, True)
    service = client.app.state.service
    with service.db() as db:
        graph, _ = service.chains.build(db.get(Chain, chain['id']))
    assert len(graph) == 2
    response = client.put(f"/api/v1/exits/{exit['id']}", json={"name": "改名", "type": "https", "server": "exit.example.com", "port": 443})
    assert response.status_code == 200
    with service.db() as db:
        config = json.loads(service.secrets.decrypt(db.get(Exit, exit['id']).config_cipher))
    assert config['password'] == 'synthetic-password' and config['tls']
    response = client.put(f"/api/v1/entries/{entry['id']}", json={"name": "失效入口", "subscription_id": entry['subscription_id'], "mode": "fixed", "node_key": 'missing'})
    assert response.status_code == 422


def test_fixed_entry_pool_validation_and_native_export(client):
    sub, entry, _, chain = setup_chain(client, True)
    assert entry['node_keys'] == [entry['node_key']]  # Legacy API remains supported.
    nodes = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()['nodes']
    keys = [node['key'] for node in nodes]
    payload = {"name": "多选入口", "subscription_id": sub['id'], "mode": "fixed", "node_keys": keys + keys}
    url = f"/api/v1/entries/{entry['id']}"
    response = client.put(url, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['node_keys'] == keys
    service = client.app.state.service
    with service.db.begin() as db:
        source = db.get(Source, sub['source_ids'][0])
        source.proxies = source.proxies + [{"name": "not-selected", "type": "http", "server": "other.example.com", "port": 8080}]
    export = client.post('/api/v1/subscriptions', json={"name": "多选链路订阅", "chain_ids": [chain['id']], "chain_mode": "native"}).json()
    config = yaml.safe_load(client.get(export['clash_url']).text)
    assert len(config['proxies']) == 4
    assert all(node['server'] != 'other.example.com' for node in config['proxies'])
    group = next(g for g in config['proxy-groups'] if g['name'].startswith('链路 ·'))
    assert group['type'] == 'url-test' and len(group['proxies']) == 2
    assert group['tolerance'] == 0 and group['interval'] == 300 and group['lazy'] is False
    for invalid in ([], ['missing'], [keys[0], 'missing']):
        assert client.put(url, json=dict(payload, node_keys=invalid)).status_code == 422
    assert next(row for row in client.get('/api/v1/entries').json() if row['id'] == entry['id'])['node_keys'] == keys
    assert client.put(url, json=dict(payload, enabled=False)).status_code == 200
    assert client.put(url, json=payload).status_code == 200
    # A disappeared selected node does not widen the pool to unselected nodes.
    with service.db.begin() as db:
        source = db.get(Source, sub['source_ids'][0])
        source.proxies = source.proxies[1:]
    with service.db() as db:
        graph, group = service.chains.build(db.get(Chain, chain['id']))
        assert len(graph) == 2 and len(group['proxies']) == 1
    with service.db.begin() as db:
        source = db.get(Source, sub['source_ids'][0])
        source.proxies = source.proxies[1:]
    from app.chains import ChainError
    with service.db() as db, pytest.raises(ChainError, match='所选入口节点均已失效'):
        service.chains.build(db.get(Chain, chain['id']))


def test_entry_pool_migration_preserves_legacy_selection(tmp_path):
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    config = Config('alembic.ini')
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0008')
        # Exercise the old entry shape, before the new JSON column exists.
        connection.execute(sa.text("INSERT INTO entries (id,name,subscription_id,mode,node_key,enabled) VALUES ('legacy','legacy','subscription','fixed','selected-key',1), ('auto','auto','subscription','auto',NULL,1)"))
        command.upgrade(config, 'head')
        rows = dict(connection.execute(sa.text('SELECT id,node_keys FROM entries')).all())
        assert json.loads(rows['legacy']) == ['selected-key']
        assert json.loads(rows['auto']) == []
    engine.dispose()


def test_port_range_and_actionable_errors(client):
    settings = client.app.state.service.settings
    settings.proxy_port_start = 25000
    settings.proxy_port_end = 25001
    config = client.get('/api/v1/settings').json()
    assert (config['proxy_port_start'], config['proxy_port_end']) == (25000, 25001)
    _, entry, exit, chain = setup_chain(client)
    assert chain['listen_port'] == 25000
    payload = {"name": "端口测试", "entry_id": entry['id'], "exit_id": exit['id']}
    for port, message in [(24999, '超出允许范围'), (25002, '超出允许范围'), (25000, '已被其他链路使用')]:
        response = client.post('/api/v1/chains', json=dict(payload, listen_port=port))
        assert response.status_code == 422
        assert message in response.json()['detail']
        assert '25000–25001' in response.json()['detail']
    response = client.post('/api/v1/chains', json=dict(payload, listen_port=25001))
    assert response.status_code == 201
    response = client.put(f"/api/v1/chains/{chain['id']}", json=payload)
    assert response.status_code == 200
    assert response.json()['listen_port'] == 25000
    response = client.post('/api/v1/chains', json=payload)
    assert response.status_code == 422
    assert '25000–25001 已全部分配' in response.json()['detail']


def test_manual_probes_return_final_results_without_background_jobs(client, monkeypatch):
    import asyncio
    _, _, exit, chain = setup_chain(client)
    manager = client.app.state.service.chains
    calls = []

    async def measured(proxies, group):
        await asyncio.sleep(.01)
        calls.append(len(proxies))
        return {name: 30 + index * 10 for index, name in enumerate(group['proxies'])}

    monkeypatch.setattr(manager, '_measure', measured)
    before = {job['id'] for job in client.get('/api/v1/jobs').json()}
    response = client.post(f"/api/v1/chains/{chain['id']}/probe")
    assert response.status_code == 200
    assert response.json()['reachable'] is True and response.json()['latency'] == 30
    assert response.json()['entry'] and 'job_id' not in response.json()
    response = client.post(f"/api/v1/exits/{exit['id']}/probe")
    assert response.status_code == 200
    assert response.json()['reachable'] is True and response.json()['latency'] == 30
    assert calls == [4, 1]
    assert {job['id'] for job in client.get('/api/v1/jobs').json()} == before

    async def failed(*args):
        return {}

    monkeypatch.setattr(manager, '_measure', failed)
    for kind, id in [('chains', chain['id']), ('exits', exit['id'])]:
        result = client.post(f'/api/v1/{kind}/{id}/probe').json()
        assert result['reachable'] is False and result['latency'] is None and result['error']
        assert 'synthetic-password' not in json.dumps(result)
    assert manager.details([chain['id']])[0]['reachable'] is False

    async def timed_out(*args):
        raise TimeoutError()

    monkeypatch.setattr(manager, '_measure', timed_out)
    for kind, id in [('chains', chain['id']), ('exits', exit['id'])]:
        assert '超时' in client.post(f'/api/v1/{kind}/{id}/probe').json()['error']
    assert client.post('/api/v1/exits/missing/probe').status_code == 404


def test_manual_probes_explain_missing_core(client):
    _, _, exit, chain = setup_chain(client)
    for kind, id in [('chains', chain['id']), ('exits', exit['id'])]:
        result = client.post(f'/api/v1/{kind}/{id}/probe').json()
        assert result['reachable'] is False and result['latency'] is None
        assert '缺少 Mihomo' in result['error']


def test_port_conflict_disable_and_details(client):
    _, entry, exit, chain = setup_chain(client)
    response = client.post('/api/v1/chains', json={"name": "重复端口", "entry_id": entry['id'], "exit_id": exit['id'], "listen_port": chain['listen_port']})
    assert response.status_code == 422
    sub = client.post('/api/v1/subscriptions', json={"name": "链路", "chain_ids": [chain['id']]}).json()
    nodes = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()['nodes']
    assert len(nodes) == 1 and nodes[0]['type'] == 'chain'
    assert client.delete(f"/api/v1/chains/{chain['id']}").status_code == 409
    response = client.put(f"/api/v1/chains/{chain['id']}", json={"name": "停用", "entry_id": entry['id'], "exit_id": exit['id'], "enabled": False})
    assert response.status_code == 200
    assert client.get(sub['clash_url']).status_code == 503
    with client.app.state.db() as db:
        assert db.get(Subscription, sub['id']).enabled


@pytest.mark.parametrize('content', ['[]', 'password: secret', '- {type: ss, server: test, port: 80}', '[{}, {}]'])
def test_invalid_exit_content(client, content):
    response = client.post('/api/v1/exits', json={"name": "错误出口", "content": content})
    assert response.status_code == 422
    assert 'secret' not in response.text


@pytest.mark.parametrize('fixed_pool', [False, True])
@pytest.mark.parametrize('pinned', [False, True])
def test_real_relay_two_hops_auth_and_revocation(client, monkeypatch, fixed_pool, pinned):
    import http.server
    import select
    import socket
    import threading
    import time
    import httpx
    import app.chains as module
    from app.mihomo import free_port
    from app.models import Source

    binary = (Path('.cache/mihomo/mihomo.exe') if Path('.cache/mihomo/mihomo.exe').exists() else Path('.cache/mihomo/mihomo')).resolve()
    if not binary.exists():
        pytest.skip('本地未安装真实内核')
    hits = {'entry': 0, 'slow': 0, 'exit': 0}
    delay_by_label = {'entry': 0, 'slow': .2, 'exit': 0}

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    def handler(label):
        class Proxy(Target):
            def do_CONNECT(self):
                hits[label] += 1
                if delay_by_label[label]:
                    time.sleep(delay_by_label[label])
                host, port = self.path.rsplit(':', 1)
                with socket.create_connection((host, int(port)), timeout=5) as remote:
                    self.send_response(200)
                    self.end_headers()
                    peers = (self.connection, remote)
                    while True:
                        ready, _, _ = select.select(peers, [], [], 3)
                        if not ready:
                            return
                        for peer in ready:
                            data = peer.recv(65536)
                            if not data:
                                return
                            (remote if peer is self.connection else self.connection).sendall(data)
        return Proxy

    servers = [http.server.ThreadingHTTPServer(('127.0.0.1', 0), cls)
               for cls in (Target, handler('entry'), handler('exit'), handler('slow'))]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sub, entry, exit, chain = setup_chain(client)
        target_port, entry_port, exit_port, slow_port = [s.server_port for s in servers]
        target = f'http://127.0.0.1:{target_port}/'
        monkeypatch.setattr(module, 'TEST_URL', target)
        service = client.app.state.service
        service.settings.proxy_port_start = 1024
        service.settings.proxy_port_end = 65535
        with service.db.begin() as db:
            source = db.get(Source, sub['source_ids'][0])
            source.proxies = [{"name": "failed-entry", "type": "http", "server": "127.0.0.1", "port": free_port()},
                              {"name": "slow", "type": "http", "server": "127.0.0.1", "port": slow_port},
                              {"name": "local", "type": "http", "server": "127.0.0.1", "port": entry_port}]
            if fixed_pool:
                from app.service import proxy_key
                selected = db.get(Entry, entry['id'])
                selected.mode = 'fixed'
                selected.node_keys = [proxy_key(proxy) for proxy in source.proxies]
                selected.node_key = selected.node_keys[0]
            db.get(Exit, exit['id']).config_cipher = service.secrets.encrypt(json.dumps({"name": "local-exit", "type": "http", "server": "127.0.0.1", "port": exit_port}))
            db.get(Chain, chain['id']).listen_port = free_port()
            if pinned:
                from app.service import proxy_key
                configured = db.get(Chain, chain['id'])
                configured.entry_mode = 'fixed'
                configured.entry_node_key = proxy_key(source.proxies[1])
        monkeypatch.setenv('MIHOMO_BIN', str(binary))
        client.portal.call(service.chains.sync)
        assert service.chains.status[chain['id']]['state'] == 'running'
        from functools import partial
        group = service.chains.status[chain['id']]['group']
        from urllib.parse import quote
        client.portal.call(partial(service.chains._request, f"/group/{quote(group, safe='')}/delay",
                                   params={"url": target, "timeout": 1000}, timeout=5))
        status = client.portal.call(service.chains.inspect)[chain['id']]
        assert status['current_entry'].endswith('slow' if pinned else 'local'), status
        if fixed_pool:
            assert hits['slow'] > 0  # Both working candidates were measured.
        conn = client.get(f"/api/v1/chains/{chain['id']}/connection").json()
        proxy = f"http://{conn['username']}:{conn['password']}@127.0.0.1:{conn['port']}"
        with httpx.Client(proxy=proxy, trust_env=False, timeout=5) as transport:
            assert transport.get(target).status_code == 204
        assert hits['slow' if pinned else 'entry'] > 0 and hits['exit'] > 0
        if pinned:
            assert hits['entry'] == 0  # The faster candidate must never be used or tested.
        live = client.post(f"/api/v1/chains/{chain['id']}/probe")
        assert live.status_code == 200 and live.json()['reachable'] is True
        assert live.json()['latency'] > 0 and live.json()['entry'].endswith('slow' if pinned else 'local')
        if pinned:
            # Native-only probes also measure the single-member select group correctly.
            with service.db() as db:
                nodes, selected_group = service.chains.build(db.get(Chain, chain['id']))
            measured = client.portal.call(service.chains._measure, nodes, selected_group)
            assert set(measured) == set(selected_group['proxies'])
        direct = client.post(f"/api/v1/exits/{exit['id']}/probe")
        assert direct.status_code == 200 and direct.json()['reachable'] is True
        assert direct.json()['latency'] > 0
        job = service.new_job('chain_probe', 1)
        client.portal.call(service.chains.probe, chain['id'], job)
        assert service.chains.details([chain['id']])[0]['reachable'] is True
        with socket.create_connection(('127.0.0.1', conn['port']), timeout=2) as sock:
            sock.sendall(b'CONNECT example.invalid:443 HTTP/1.1\r\nHost: example.invalid\r\n\r\n')
            assert b'407' in sock.recv(512)
        with socket.create_connection(('127.0.0.1', conn['port']), timeout=2) as sock:
            sock.sendall(bytes([5, 2, 0, 2]))
            assert sock.recv(2) == bytes([5, 2])
        export_sub = client.post('/api/v1/subscriptions', json={"name": "中转订阅", "chain_ids": [chain['id']]}).json()
        exported = yaml.safe_load(client.get(export_sub['proxies_url']).text)['proxies']
        assert len(exported) == 1 and exported[0]['port'] == conn['port']
        # A dead core is recreated from the persisted graph without changing its proxy credentials.
        client.portal.call(service.chains._stop_process)
        client.portal.call(service.chains.sync)
        assert service.chains.status[chain['id']]['state'] == 'running'
        if not pinned:
            # Change the winner and prove manual probing updates the *running* group.
            delay_by_label.update(entry=.35, slow=.01)
            changed = client.post(f"/api/v1/chains/{chain['id']}/probe").json()
            assert changed['reachable'] and changed['entry'].endswith('slow')
            current = client.get('/api/v1/chains').json()[0]['status']
            assert current['current_entry'].endswith('slow')
            assert current['current_entry_key'] == changed['entry_key']
            refreshed = yaml.safe_load(client.get(export_sub['clash_url']).text)['proxies']
            assert refreshed == exported and refreshed[0]['name'] == chain['name']
        disabled = client.put(f"/api/v1/chains/{chain['id']}", json={"name": "停用", "entry_id": entry['id'], "exit_id": exit['id'], "enabled": False})
        assert disabled.status_code == 200, disabled.text
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1', conn['port']), timeout=.5)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_real_relay_rejects_occupied_listener(client, monkeypatch):
    import socket

    binary = (Path('.cache/mihomo/mihomo.exe') if Path('.cache/mihomo/mihomo.exe').exists()
              else Path('.cache/mihomo/mihomo')).resolve()
    assert binary.is_file(), '真实监听冲突测试需要固定版本 Mihomo'
    _, _, _, chain = setup_chain(client)
    service = client.app.state.service
    with socket.socket() as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(('0.0.0.0', 0))
        occupied.listen()
        with service.db.begin() as db:
            db.get(Chain, chain['id']).listen_port = occupied.getsockname()[1]
        monkeypatch.setenv('MIHOMO_BIN', str(binary))
        client.portal.call(service.chains.sync)
        assert service.chains.status[chain['id']]['state'] == 'error'
        assert service.chains.process is None
