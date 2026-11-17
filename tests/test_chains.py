import json
import subprocess
from pathlib import Path

import pytest
import yaml

from app.models import Chain, Exit, Subscription


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


def test_native_graph_and_provider_fail_closed(client, tmp_path):
    _, _, _, chain = setup_chain(client)
    sub = client.post('/api/v1/subscriptions', json={"name": "仅链路", "chain_ids": [chain['id']]}).json()
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


def test_real_relay_two_hops_auth_and_revocation(client, monkeypatch):
    import http.server
    import select
    import socket
    import threading
    import httpx
    import app.chains as module
    from app.mihomo import free_port
    from app.models import Source

    binary = (Path('.cache/mihomo/mihomo.exe') if Path('.cache/mihomo/mihomo.exe').exists() else Path('.cache/mihomo/mihomo')).resolve()
    if not binary.exists():
        pytest.skip('本地未安装真实内核')
    hits = {'entry': 0, 'exit': 0}

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
               for cls in (Target, handler('entry'), handler('exit'))]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sub, entry, exit, chain = setup_chain(client)
        target_port, entry_port, exit_port = [s.server_port for s in servers]
        target = f'http://127.0.0.1:{target_port}/'
        monkeypatch.setattr(module, 'TEST_URL', target)
        service = client.app.state.service
        service.settings.proxy_port_start = 1024
        service.settings.proxy_port_end = 65535
        with service.db.begin() as db:
            source = db.get(Source, sub['source_ids'][0])
            source.proxies = [{"name": "failed-entry", "type": "http", "server": "127.0.0.1", "port": free_port()},
                              {"name": "local", "type": "http", "server": "127.0.0.1", "port": entry_port}]
            db.get(Exit, exit['id']).config_cipher = service.secrets.encrypt(json.dumps({"name": "local-exit", "type": "http", "server": "127.0.0.1", "port": exit_port}))
            db.get(Chain, chain['id']).listen_port = free_port()
        monkeypatch.setenv('MIHOMO_BIN', str(binary))
        client.portal.call(service.chains.sync)
        assert service.chains.status[chain['id']]['state'] == 'running'
        from functools import partial
        group = service.chains.status[chain['id']]['group']
        from urllib.parse import quote
        client.portal.call(partial(service.chains._request, f"/group/{quote(group, safe='')}/delay",
                                   params={"url": target, "timeout": 1000}, timeout=5))
        conn = client.get(f"/api/v1/chains/{chain['id']}/connection").json()
        proxy = f"http://{conn['username']}:{conn['password']}@127.0.0.1:{conn['port']}"
        with httpx.Client(proxy=proxy, trust_env=False, timeout=5) as transport:
            assert transport.get(target).status_code == 204
        assert hits['entry'] > 0 and hits['exit'] > 0
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
        disabled = client.put(f"/api/v1/chains/{chain['id']}", json={"name": "停用", "entry_id": entry['id'], "exit_id": exit['id'], "enabled": False})
        assert disabled.status_code == 200, disabled.text
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1', conn['port']), timeout=.5)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
