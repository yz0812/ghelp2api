import asyncio
import copy
import json
import time
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from app.chain_graph import groups_for
from app.models import Chain, ChainEvent, ChainSample, Source
from tests.test_chains import setup_chain


def path_for(entry, exit, middle=(), strategy="auto", **extra):
    return {"name": "主路径", "strategy": strategy, "hops": [
        {"source": "entry", "ref": entry["id"]}, *middle, {"source": "proxy", "ref": exit["id"]}], **extra}


def post(client, endpoint, payload):
    response = client.post('/api/v1' + endpoint, json=payload)
    assert response.status_code in (200, 201, 202), response.text
    return response.json()


def wait_job(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get('/api/v1/jobs/' + identifier).json()
        if job['status'] not in ('pending', 'running'):
            return job
        time.sleep(.02)
    pytest.fail('diagnostic job did not finish')


@pytest.mark.parametrize('count', [2, 3, 5])
def test_ordered_paths_export_version_and_references(client, count):
    sub, entry, exit, _ = setup_chain(client)
    middle = []
    for index in range(count - 2):
        proxy = post(client, '/exits', {"name": f"middle-{index}", "server": f"middle{index}.example.com", "port": 8080})
        middle.append({"source": "proxy", "ref": proxy['id']})
    values = {"name": "多跳", "paths": [path_for(entry, exit, middle)], "monitor_enabled": False}
    chain = post(client, '/chains', values)
    assert chain['revision'] == 1 and len(chain['paths'][0]['hops']) == count
    export = post(client, '/subscriptions', {"name": "多跳导出", "chain_ids": [chain['id']], "chain_mode": "native"})
    config = yaml.safe_load(client.get(export['clash_url']).text)
    assert len(config['proxies']) == count * 2
    names = {n['name'] for n in config['proxies']}
    assert all(n['dialer-proxy'] in names for n in config['proxies'] if 'dialer-proxy' in n)
    assert 'synthetic-password' not in client.get('/api/v1/chains/' + chain['id']).text
    assert client.put('/api/v1/chains/' + chain['id'], json=values).status_code == 409
    response = client.put('/api/v1/chains/' + chain['id'], json=dict(values, revision=1))
    assert response.status_code == 200 and response.json()['revision'] == 2
    assert client.put('/api/v1/chains/' + chain['id'], json=dict(values, revision=1)).status_code == 409
    if middle:
        assert client.delete('/api/v1/exits/' + middle[0]['ref']).status_code == 409
        assert client.put('/api/v1/chains/' + chain['id'], json={"name": "legacy", "entry_id": entry['id'], "exit_id": exit['id']}).status_code == 409
    assert sub['id']


def test_subscription_hop_validation_lock_and_backup(client):
    sub, entry, exit, _ = setup_chain(client)
    source = post(client, '/sources/file', {"name": "中间订阅源", "content": 'proxies:\n - {name: middle, type: http, server: middle.example.com, port: 8080}'})
    middle_sub = post(client, '/subscriptions', {"name": "中间订阅", "source_ids": [source['id']]})
    key = client.get('/api/v1/subscriptions/' + middle_sub['id'] + '/nodes').json()['nodes'][0]['key']
    path = path_for(entry, exit, [{"source": "subscription", "ref": middle_sub['id'], "node_key": key}], strategy='stable')
    chain = post(client, '/chains', {"name": "稳定三跳", "paths": [path], "monitor_enabled": False})
    assert client.delete('/api/v1/subscriptions/' + middle_sub['id']).status_code == 409
    service = client.app.state.service
    with service.db() as db:
        nodes, group = service.chains.build(db.get(Chain, chain['id']))
    assert group['type'] == 'url-test' and group['tolerance'] == 50 and len(nodes) == 6
    invalid = copy.deepcopy(path)
    invalid['hops'][1]['node_key'] = 'missing'
    assert client.post('/api/v1/chains/validate', json={"name": "bad", "paths": [invalid]}).status_code == 422
    invalid = copy.deepcopy(path)
    invalid['hops'].insert(1, invalid['hops'][1])
    assert client.post('/api/v1/chains/validate', json={"name": "bad", "paths": [invalid]}).status_code == 422
    other = post(client, '/exits', {"name": "备用出口", "server": "other.example.com", "port": 8080})
    backup = path_for(entry, other)
    assert client.post('/api/v1/chains', json={"name": "locked", "paths": [path, backup]}).status_code == 422
    backup['hops'][-1]['ref'] = exit['id']
    multiple = post(client, '/chains', {"name": "主备", "paths": [path, backup], "monitor_enabled": False})
    with service.db() as db:
        _, group = service.chains.build(db.get(Chain, multiple['id']))
    assert len(groups_for(group)) == 3 and group['type'] == 'fallback'
    with service.db.begin() as db:
        db.get(Source, source['id']).proxies = []
    with service.db() as db:
        _, group = service.chains.build(db.get(Chain, multiple['id']))
    assert group['_errors'][0]['path_index'] == 0 and len(groups_for(group)) == 1


def test_templates_clone_batch_and_no_credential_copy(client):
    _, entry, exit, old = setup_chain(client)
    values = {"name": "模板链路", "paths": [path_for(entry, exit)], "enabled": False, "monitor_enabled": False}
    template = post(client, '/chain-templates', {"name": "模板", "config": values})
    assert 'password' not in json.dumps(template) and 'listen_port' not in template['config']
    copy_chain = post(client, '/chains/' + old['id'] + '/clone', {"name": "副本"})
    assert not copy_chain['enabled'] and copy_chain['listen_port'] != old['listen_port']
    old_connection = client.get('/api/v1/chains/' + old['id'] + '/connection').json()
    new_connection = client.get('/api/v1/chains/' + copy_chain['id'] + '/connection').json()
    assert old_connection['password'] != new_connection['password']
    result = post(client, '/chains/batch', {"operation": "monitor_on", "ids": [copy_chain['id'], 'missing']})
    assert result['results'][0]['ok'] and not result['results'][1]['ok']
    post(client, '/subscriptions', {"name": "引用", "chain_ids": [old['id']]})
    result = post(client, '/chains/batch', {"operation": "delete", "ids": [old['id'], copy_chain['id']]})
    assert not result['results'][0]['ok'] and result['results'][1]['ok']
    assert client.delete('/api/v1/chain-templates/' + template['id']).status_code == 200


def test_background_loop_runs_monitoring(client, monkeypatch):
    manager = client.app.state.service.chains
    calls = []

    async def noop(*args):
        pass

    async def tick():
        calls.append('tick')
        raise asyncio.CancelledError

    monkeypatch.setattr(manager, 'refresh', noop)
    monkeypatch.setattr(manager, 'sync', noop)
    monkeypatch.setattr(manager, 'inspect', noop)
    monkeypatch.setattr(manager.diagnostics, 'tick', tick)
    manager.service.settings.scheduler_enabled = True

    async def once():
        with pytest.raises(asyncio.CancelledError):
            await manager.run()

    client.portal.call(once)
    assert calls == ['tick']


def test_concurrent_edit_does_not_hold_database_lock(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    _, entry, exit, _ = setup_chain(client)
    values = {'name': 'concurrent', 'paths': [path_for(entry, exit)], 'monitor_enabled': False}
    chain = post(client, '/chains', values)
    entered = 0

    async def validate(candidate):
        nonlocal entered
        entered += 1
        # Represents asynchronous Mihomo validation while another request arrives.
        for _ in range(50):
            if entered == 2:
                break
            await asyncio.sleep(.01)
        # A monitor update must still be able to acquire a write transaction.
        with client.app.state.service.db.begin() as db:
            db.add(ChainEvent(chain_id=chain['id'], kind='test', detail={}))

    monkeypatch.setattr(client.app.state.service.chains, 'validate_core', validate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(client.put, '/api/v1/chains/' + chain['id'],
                                   json=dict(values, revision=chain['revision'], name=f'edit-{i}')) for i in range(2)]
        assert sorted(f.result().status_code for f in futures) == [200, 409]


def test_diagnostic_budget_concurrency_and_immediate_cancel(client, monkeypatch):
    from contextlib import asynccontextmanager
    from app.models import Job
    import app.chain_diagnostics as module
    _, entry, exit, _ = setup_chain(client)
    chain = post(client, '/chains', {'name': 'bounded', 'paths': [path_for(entry, exit)], 'monitor_enabled': False})
    manager = client.app.state.service.chains
    with manager.service.db() as db:
        row = db.get(Chain, chain['id'])
    active, maximum = 0, 0

    @asynccontextmanager
    async def slow_core(*args):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        try:
            await asyncio.sleep(10)
            yield None
        finally:
            active -= 1

    monkeypatch.setattr(module, 'isolated_core', slow_core)

    async def exercise():
        outcomes = await asyncio.gather(*(manager.diagnostics.measure(row, timeout=.2) for _ in range(5)))
        assert all(r['reachable'] is None and '预算' in r['error'] for r in outcomes)
        identifier = manager.diagnostics.start(row)
        await manager.diagnostics.cancel(identifier)
        with manager.service.db() as db:
            assert db.get(Job, identifier).status == 'cancelled'
        assert not manager.diagnostics.active_chains

    client.portal.call(exercise)
    assert maximum == 2 and active == 0


def test_failed_core_validation_keeps_saved_configuration(client, monkeypatch):
    from app.chains import ChainError
    _, entry, exit, _ = setup_chain(client)
    values = {'name': 'before', 'paths': [path_for(entry, exit)], 'monitor_enabled': False}
    chain = post(client, '/chains', values)

    async def rejected(*args):
        raise ChainError('Mihomo 配置校验失败，配置未应用')

    monkeypatch.setattr(client.app.state.service.chains, 'validate_core', rejected)
    response = client.put('/api/v1/chains/' + chain['id'], json=dict(values, name='after', revision=chain['revision']))
    assert response.status_code == 422
    current = client.get('/api/v1/chains/' + chain['id']).json()
    assert current['name'] == 'before' and current['revision'] == chain['revision']


def test_diagnostics_cancellation_history_and_retention(client, monkeypatch):
    _, entry, exit, _ = setup_chain(client)
    chain = post(client, '/chains', {"name": "监控", "paths": [path_for(entry, exit)], "monitor_enabled": True})
    manager = client.app.state.service.chains

    async def measured(row, **kwargs):
        await asyncio.sleep(.02)
        return {"reachable": True, "latency": 30, "error": None, "tested_at": time.time(),
                "source": kwargs.get('source', 'manual'), "revision": row.revision, "steps": [], "exit_ip": "198.51.100.1"}

    monkeypatch.setattr(manager.diagnostics, 'measure', measured)
    task = post(client, '/chains/diagnostics', {"chain_id": chain['id']})
    job = wait_job(client, task['job_id'])
    assert job['status'] == 'done' and job['result']['reachable']
    history = client.get('/api/v1/chains/' + chain['id'] + '/history').json()
    assert history['sample_count'] == 1 and history['availability'] is None
    client.portal.call(manager.diagnostics.tick)
    jobs = client.get('/api/v1/jobs').json()
    wait_job(client, next(j['id'] for j in jobs if j['id'] != task['job_id']))
    history = client.get('/api/v1/chains/' + chain['id'] + '/history').json()
    assert history['availability'] == 100 and history['monitor_sample_count'] == 1
    with manager.service.db.begin() as db:
        db.add(ChainSample(chain_id=chain['id'], tested_at=time.time() - 31 * 86400, fingerprint='old', result={}))
        db.add(ChainEvent(chain_id=chain['id'], created_at=time.time() - 31 * 86400, kind='old', detail={}))
    manager.diagnostics.last_cleanup = 0
    client.portal.call(manager.diagnostics.tick)
    with manager.service.db() as db:
        assert not db.scalar(select(ChainSample).where(ChainSample.fingerprint == 'old'))
        assert not db.scalar(select(ChainEvent).where(ChainEvent.kind == 'old'))

    async def slow(*args, **kwargs):
        await asyncio.sleep(60)
    monkeypatch.setattr(manager.diagnostics, 'measure', slow)
    task = post(client, '/chains/diagnostics', {"chain_id": chain['id']})
    assert client.post('/api/v1/jobs/' + task['job_id'] + '/cancel').status_code == 200
    assert wait_job(client, task['job_id'])['status'] == 'cancelled'
    assert chain['id'] not in manager.diagnostics.active_chains


@pytest.fixture
def proxy_lab():
    import http.server
    import select
    import socket
    import threading
    controls = [{"hits": [], "delay": 0, "failed": False} for _ in range(7)]

    class Target(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b'{"ip":"198.51.100.42"}' if self.path == '/ip' else b'complete-chain-target'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def handler(index):
        class Proxy(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_CONNECT(self):
                state = controls[index]
                if state['failed']:
                    self.send_error(502)
                    return
                time.sleep(state['delay'])
                host, port = self.path.rsplit(':', 1)
                state['hits'].append(int(port))
                try:
                    with socket.create_connection((host, int(port)), timeout=3) as remote:
                        self.send_response(200)
                        self.end_headers()
                        peers = (self.connection, remote)
                        while True:
                            ready, _, _ = select.select(peers, [], [], 2)
                            if not ready:
                                return
                            for peer in ready:
                                data = peer.recv(65536)
                                if not data:
                                    return
                                (remote if peer is self.connection else self.connection).sendall(data)
                except OSError:
                    pass
        return Proxy

    servers = [http.server.ThreadingHTTPServer(('127.0.0.1', 0), Target)]
    servers += [http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler(i)) for i in range(7)]
    for server in servers:
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield servers, controls
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize('strategy', ['auto', 'stable', 'fixed', 'fallback', 'paths'])
def test_real_selection_and_recovery(client, monkeypatch, proxy_lab, strategy):
    from urllib.parse import quote
    import httpx
    import app.chains as chains_module
    import app.mihomo as mihomo_module
    from app.mihomo import free_port
    binary = Path('.cache/mihomo/mihomo.exe' if Path('.cache/mihomo/mihomo.exe').exists() else '.cache/mihomo/mihomo').resolve()
    assert binary.is_file()
    servers, controls = proxy_lab
    target = f'http://127.0.0.1:{servers[0].server_port}'
    monkeypatch.setattr(chains_module, 'TEST_URL', target)
    monkeypatch.setattr(mihomo_module, 'TEST_URL', target)
    source = post(client, '/sources/file', {'name': 'selection', 'content': 'proxies:\n' + '\n'.join(
        f' - {{name: candidate-{i}, type: http, server: 127.0.0.1, port: {servers[i + 1].server_port}}}' for i in range(2))})
    sub = post(client, '/subscriptions', {'name': 'selection', 'source_ids': [source['id']]})
    entry = post(client, '/entries', {'name': 'selection', 'subscription_id': sub['id']})
    exit = post(client, '/exits', {'name': 'exit', 'server': '127.0.0.1', 'port': servers[3].server_port})
    nodes = client.get('/api/v1/entries/' + entry['id'] + '/nodes').json()
    keys = [next(n['key'] for n in nodes if n['port'] == servers[i + 1].server_port) for i in range(2)]
    path = path_for(entry, exit, strategy='fixed' if strategy == 'paths' else strategy,
                    ordered_keys=keys if strategy == 'fallback' else [], tolerance_ms=500)
    if strategy in {'fixed', 'paths'}:
        path['hops'][0]['node_key'] = keys[0]
    paths = [path]
    if strategy == 'paths':
        backup = copy.deepcopy(path)
        backup['name'] = '备用'
        backup['hops'][0]['node_key'] = keys[1]
        paths.append(backup)
    service = client.app.state.service
    service.settings.proxy_port_start, service.settings.proxy_port_end = 1024, 65535
    chain = post(client, '/chains', {'name': 'selection', 'paths': paths, 'monitor_enabled': False, 'listen_port': free_port()})
    monkeypatch.setenv('MIHOMO_BIN', str(binary))
    controls[1]['delay'] = .15
    manager = service.chains
    client.portal.call(manager.sync)
    assert manager.status[chain['id']]['state'] == 'running'

    async def refresh():
        group = manager.status[chain['id']]['group']
        data = await manager._request('/proxies', timeout=3)
        for name in [n for n, g in data['proxies'].items() if g.get('type') in {'URLTest', 'Fallback', 'Selector'}]:
            try:
                await manager._request('/group/' + quote(name, safe='') + '/delay', params={'url': target, 'timeout': 1000}, timeout=10)
            except httpx.HTTPStatusError as exc:
                assert exc.response.status_code == 504
        try:
            await manager._request('/group/' + quote(group, safe='') + '/delay', params={'url': target, 'timeout': 1000}, timeout=10)
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 504
        await manager.inspect()
        return manager.status[chain['id']]['current_entry_key']

    assert client.portal.call(refresh) == keys[0]
    controls[0]['delay'], controls[1]['delay'] = .2, 0
    assert client.portal.call(refresh) == (keys[1] if strategy == 'auto' else keys[0])
    if strategy in {'fallback', 'paths', 'fixed'}:
        controls[0]['failed'] = True
        assert client.portal.call(refresh) == (keys[0] if strategy == 'fixed' else keys[1])
        controls[0]['failed'] = False
        assert client.portal.call(refresh) == keys[0]
    if strategy == 'stable':
        controls[0]['delay'] = .8
        assert client.portal.call(refresh) == keys[1]


@pytest.mark.parametrize('count', [2, 3, 5])
def test_real_multihop_native_relay_draft_and_failure(client, monkeypatch, proxy_lab, tmp_path, count):
    import httpx
    import app.chains as chains_module
    import app.mihomo as mihomo_module
    import app.chain_diagnostics as diagnostics_module
    from app.chain_diagnostics import isolated_core
    from app.mihomo import free_port
    binary = Path('.cache/mihomo/mihomo.exe' if Path('.cache/mihomo/mihomo.exe').exists() else '.cache/mihomo/mihomo').resolve()
    assert binary.is_file(), '验收必须安装固定版本 Mihomo，不能跳过真实转发测试'
    servers, controls = proxy_lab
    target = f'http://127.0.0.1:{servers[0].server_port}'
    monkeypatch.setattr(chains_module, 'TEST_URL', target)
    monkeypatch.setattr(mihomo_module, 'TEST_URL', target)
    monkeypatch.setattr(diagnostics_module, 'IP_URL', target + '/ip')
    service = client.app.state.service
    service.settings.proxy_port_start, service.settings.proxy_port_end = 1024, 65535
    resources = [post(client, '/exits', {"name": f"hop-{i}", "server": "127.0.0.1", "port": servers[i + 1].server_port}) for i in range(count)]
    path = {"name": "实际路径", "strategy": "fixed", "hops": [{"source": "proxy", "ref": r['id']} for r in resources]}
    values = {"name": "真实多跳", "paths": [path], "monitor_enabled": False, "listen_port": free_port()}
    chain = post(client, '/chains', values)
    monkeypatch.setenv('MIHOMO_BIN', str(binary))
    client.portal.call(service.chains.sync)
    assert service.chains.status[chain['id']]['state'] == 'running'
    conn = client.get('/api/v1/chains/' + chain['id'] + '/connection').json()
    proxy = f"http://{conn['username']}:{conn['password']}@127.0.0.1:{conn['port']}"
    with httpx.Client(proxy=proxy, trust_env=False, timeout=10) as transport:
        assert transport.get(target).text == 'complete-chain-target'
    for i in range(count):
        assert (servers[i + 2].server_port if i < count - 1 else servers[0].server_port) in controls[i]['hits']
    before = client.get('/api/v1/chains/' + chain['id']).json()
    task = post(client, '/chains/diagnostics', {"draft": values})
    job = wait_job(client, task['job_id'])
    assert job['status'] == 'done', job
    assert job['result']['reachable'] and job['result']['exit_ip'] == '198.51.100.42', job
    assert len([s for s in job['result']['steps'] if s['label'].startswith('经过前')]) == count
    after = client.get('/api/v1/chains/' + chain['id']).json()
    assert after['revision'] == before['revision'] and after['status']['current_path'] == before['status']['current_path']
    assert not list(service.settings.data_dir.glob('chain-diagnostic-*'))
    exported = post(client, '/subscriptions', {"name": "原生真实", "chain_ids": [chain['id']], "chain_mode": "native"})
    config = yaml.safe_load(client.get(exported['clash_url']).text)
    terminal = config['proxy-groups'][0]['proxies'][0]
    graph = config['proxies']
    terminal = next(n['name'] for n in graph if n['name'].endswith(f'-h{count - 1}'))

    async def native_request():
        async with isolated_core(service, graph, [terminal]) as (_, port):
            async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}', trust_env=False, timeout=10) as transport:
                assert (await transport.get(target)).text == 'complete-chain-target'
    client.portal.call(native_request)
    controls[count - 1]['failed'] = True
    with httpx.Client(proxy=proxy, trust_env=False, timeout=3) as transport:
        try:
            response = transport.get(target)
            assert response.status_code >= 400
        except httpx.HTTPError:
            pass
