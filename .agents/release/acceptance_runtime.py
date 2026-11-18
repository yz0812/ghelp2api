"""Run inside the deployed container. Never print credentials or subscription URLs."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx

STATE = Path('/data/backups/orchestration-qa.json')
PREFIX = '编排验收20261117'
api = httpx.Client(base_url='http://127.0.0.1:8000/api/v1', timeout=150,
                   headers={'Authorization': 'Bearer ' + os.environ['ADMIN_API_KEY']}, trust_env=False)

HOP_CODE = '''
import base64, http.server, os, select, socket, sys
class Proxy(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_CONNECT(self):
  expected='Basic '+base64.b64encode(b'qa:acceptance-loopback-only-20261117').decode()
  if self.headers.get('Proxy-Authorization') != expected:
   self.send_error(407); return
  try:
   host,port=self.path.rsplit(':',1)
   with socket.create_connection((host,int(port)),timeout=10) as remote:
    self.send_response(200); self.end_headers()
    peers=(self.connection,remote)
    while True:
     ready,_,_=select.select(peers,[],[],30)
     if not ready: return
     for peer in ready:
      data=peer.recv(65536)
      if not data: return
      (remote if peer is self.connection else self.connection).sendall(data)
  except OSError: pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1])),Proxy)
server.daemon_threads=True
server.serve_forever()
'''


def start_hop(state):
    import subprocess
    process = subprocess.Popen([sys.executable, '-c', HOP_CODE, str(state['hop_port'])],
                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state['hop_pid'] = process.pid
    save(state)
    time.sleep(1)
    assert process.poll() is None


def request(method, path, data=None):
    r = api.request(method, path, json=data)
    if not r.is_success:
        raise RuntimeError(f'{method} {path.split("?")[0]} failed: {r.status_code}')
    return r.json()


def save(state):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state))
    STATE.chmod(0o600)


def job(identifier):
    for _ in range(65):
        value = request('GET', '/jobs/' + identifier)
        if value['status'] not in ('pending', 'running'):
            return value
        time.sleep(2)
    raise RuntimeError('diagnostic timeout')


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


mode = sys.argv[1]
if mode == 'seed':
    assert not STATE.exists(), 'acceptance state already exists'
    chains = request('GET', '/chains')
    original = next(c for c in chains if c['enabled'] and c['relay_enabled'])
    fields = ['id', 'entry_id', 'exit_id', 'listen_port', 'public_host', 'entry_mode', 'entry_node_key']
    state = {'original': {k: original[k] for k in fields}, 'resources': [],
             'connection_hash': signature(request('GET', '/chains/' + original['id'] + '/connection')),
             'subscriptions': {s['id']: signature([s['clash_url'], s['proxies_url'], s['chain_ids']]) for s in request('GET', '/subscriptions')}}
    save(state)
    entry = next(e for e in request('GET', '/entries') if e['id'] == original['entry_id'])
    nodes = request('GET', '/entries/' + entry['id'] + '/nodes')
    nodes = sorted([n for n in nodes if n['included'] and n['type'] != 'hysteria2'], key=lambda n: (n.get('reachable') is False, n.get('latency') or 999999))
    assert len(nodes) >= 2, 'need at least two candidates for a real three-hop test'
    first = nodes[0]
    middle = next(n for n in nodes[1:] if (n['server'], n['port']) != (first['server'], first['port']))
    primary = {'name': '三跳主路径', 'strategy': 'fixed', 'hops': [
        {'source': 'entry', 'ref': entry['id'], 'node_key': first['key']},
        {'source': 'subscription', 'ref': entry['subscription_id'], 'node_key': middle['key']},
        {'source': 'proxy', 'ref': original['exit_id']}]}
    backup = original['paths'][0] | {'name': '两跳备用路径'}
    values = {'name': PREFIX + '链路', 'paths': [primary, backup], 'monitor_enabled': True,
              'interval': 60, 'enabled': True, 'relay_enabled': True}
    chain = request('POST', '/chains', values)
    state['chain'] = chain['id']; state['resources'].append(['chains', chain['id']]); save(state)
    assert chain['status']['state'] == 'running', chain['status']['state']
    template = request('POST', '/chain-templates', {'name': PREFIX + '模板', 'config': values})
    state['resources'].append(['chain-templates', template['id']]); save(state)
    sub = request('POST', '/subscriptions', {'name': PREFIX + '订阅', 'chain_ids': [chain['id']]})
    state['resources'].append(['subscriptions', sub['id']]); state['export_url'] = sub['clash_url']; save(state)
    with httpx.Client(trust_env=False, timeout=20) as client:
        export = client.get(sub['clash_url']); assert export.is_success and 'dialer-proxy' in export.text
    connection = request('GET', '/chains/' + chain['id'] + '/connection')
    state['connection'] = connection; save(state)
    print(json.dumps({'created': True, 'port': connection['port'], 'paths': [3, 2], 'native_export': True}))
elif mode == 'check':
    state = json.loads(STATE.read_text())
    original = request('GET', '/chains/' + state['original']['id'])
    assert {k: original[k] for k in state['original']} == state['original']
    assert signature(request('GET', '/chains/' + original['id'] + '/connection')) == state['connection_hash']
    subs = {s['id']: signature([s['clash_url'], s['proxies_url'], s['chain_ids']]) for s in request('GET', '/subscriptions')}
    assert all(subs.get(k) == v for k, v in state['subscriptions'].items())
    chain = request('GET', '/chains/' + state['chain'])
    assert chain['status']['state'] == 'running'
    assert len(chain['paths']) == 2 and len(chain['paths'][0]['hops']) == 3
    history = request('GET', '/chains/' + state['chain'] + '/history')
    print(json.dumps({'original_preserved': True, 'chain_running': True,
                      'current_path': chain['status'].get('current_path', {}).get('path_index'),
                      'samples': history['sample_count'], 'monitor_samples': history['monitor_sample_count'],
                      'availability': history['availability'], 'events': len(history['events'])}))
elif mode == 'preparehop':
    from app.mihomo import free_port
    state = json.loads(STATE.read_text())
    state['hop_port'] = free_port()
    start_hop(state)
    hop = request('POST', '/exits', {'name': PREFIX + '受控入口', 'server': '127.0.0.1', 'port': state['hop_port'],
                                   'username': 'qa', 'password': 'acceptance-loopback-only-20261117'})
    state['resources'].append(['exits', hop['id']]); save(state)
    original = request('GET', '/chains/' + state['original']['id'])
    entry = next(e for e in request('GET', '/entries') if e['id'] == original['entry_id'])
    key = original['status']['current_entry_key']
    chain = request('GET', '/chains/' + state['chain'])
    primary = {'name': '受控入口三跳', 'strategy': 'fixed', 'hops': [
        {'source': 'proxy', 'ref': hop['id']},
        {'source': 'subscription', 'ref': entry['subscription_id'], 'node_key': key},
        {'source': 'proxy', 'ref': original['exit_id']}]}
    request('PUT', '/chains/' + state['chain'], {'name': chain['name'], 'paths': [primary, chain['paths'][1]],
             'revision': chain['revision'], 'monitor_enabled': True, 'interval': 60, 'listen_port': chain['listen_port']})
    print('Controlled loopback ingress + live subscription node + live exit configured')
elif mode == 'resumehop':
    state = json.loads(STATE.read_text()); start_hop(state)
    print('Temporary loopback ingress restarted after container recreation')
elif mode == 'failhop':
    import signal
    state = json.loads(STATE.read_text())
    cmdline = Path('/proc/' + str(state['hop_pid']) + '/cmdline').read_bytes()
    assert HOP_CODE.encode() in cmdline
    os.kill(state['hop_pid'], signal.SIGTERM)
    print('Only the temporary acceptance ingress stopped; waiting for fallback')
elif mode == 'integrity':
    import sqlite3
    db = sqlite3.connect('/data/ghelper.db')
    assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
    assert not db.execute('pragma foreign_key_check').fetchall()
    assert db.execute('select version_num from alembic_version').fetchone()[0] == '0012'
    print('SQLite integrity, foreign keys and migration 0012 PASS')
elif mode == 'diagnostic':
    state = json.loads(STATE.read_text())
    chain = request('GET', '/chains/' + state['chain'])
    before = chain['status'].get('current_path')
    draft = {'name': PREFIX + '草稿', 'paths': [chain['paths'][0]], 'monitor_enabled': False}
    result = job(request('POST', '/chains/diagnostics', {'draft': draft})['job_id'])
    state['draft_job'] = result['id']
    state.setdefault('draft_jobs', []).append(result['id']); save(state)
    after = request('GET', '/chains/' + state['chain'])
    assert after['revision'] == chain['revision']
    print(json.dumps({'job': result['status'], 'reachable': result.get('result', {}).get('reachable'),
                      'steps': [{'label': s['label'], 'state': s['state']} for s in (result.get('result') or {}).get('steps', [])],
                      'exit_ip_present': bool((result.get('result') or {}).get('exit_ip')), 'same_path': before == after['status'].get('current_path')}))
elif mode == 'auth':
    state = json.loads(STATE.read_text())
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False) as client:
        response = client.post('/api/v1/auth/login', json={'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD']})
        assert response.is_success
        token = client.cookies.get('ghelper_session')
    import sqlite3
    db = sqlite3.connect('/data/ghelper.db')
    expires = time.time() + 3600
    digest = hashlib.sha256(token.encode()).hexdigest()
    db.execute('UPDATE login_sessions SET expires=? WHERE id=?', (expires, digest)); db.commit(); db.close()
    state['session_id'] = digest; save(state)
    auth = {'cookies': [{'name': 'ghelper_session', 'value': token, 'domain': 'ghelper.accsunny.com', 'path': '/',
                         'expires': expires, 'httpOnly': True, 'secure': True, 'sameSite': 'Strict'}], 'origins': []}
    file = Path('/tmp/orchestration-browser.json'); file.write_text(json.dumps({'storage': auth, 'chain_id': state['chain'], 'connection': state['connection']})); file.chmod(0o600)
    print('Short-lived browser session prepared; credentials not printed')
elif mode == 'cleanup':
    state = json.loads(STATE.read_text())
    request('POST', '/chains/batch', {'operation': 'monitor_off', 'ids': [state['chain']]})
    # Let already-started diagnostics finish before deleting their exact records.
    for _ in range(65):
        running = [j for j in request('GET', '/jobs') if j['kind'] == 'chain_diagnostic' and j['status'] in ('pending', 'running')]
        if not running:
            break
        time.sleep(2)
    import sqlite3
    db = sqlite3.connect('/data/ghelper.db')
    fingerprints = {r[0] for r in db.execute('SELECT DISTINCT fingerprint FROM chain_samples WHERE chain_id=?', (state['chain'],))}
    monitor_jobs = [identifier for identifier, value in db.execute("SELECT id,result FROM jobs WHERE kind='chain_diagnostic'")
                    if value and json.loads(value).get('fingerprint') in fingerprints]
    db.close()
    for kind, identifier in sorted(state['resources'], key=lambda x: {'subscriptions': 0, 'chain-templates': 1, 'chains': 2, 'exits': 3}[x[0]]):
        response = api.delete('/' + kind + '/' + identifier)
        assert response.status_code in (200, 404)
    import sqlite3
    db = sqlite3.connect('/data/ghelper.db')
    if state.get('session_id'): db.execute('DELETE FROM login_sessions WHERE id=?', (state['session_id'],))
    for identifier in set(monitor_jobs + state.get('draft_jobs', []) + [state.get('draft_job')]):
        if identifier: db.execute('DELETE FROM jobs WHERE id=?', (identifier,))
    db.commit(); db.close()
    if state.get('hop_pid'):
        import signal
        try:
            cmdline = Path('/proc/' + str(state['hop_pid']) + '/cmdline').read_bytes()
            if HOP_CODE.encode() in cmdline: os.kill(state['hop_pid'], signal.SIGTERM)
        except FileNotFoundError:
            pass
    for kind in ['chains', 'entries', 'exits', 'subscriptions', 'chain-templates']:
        assert not any(r['name'].startswith(PREFIX) for r in request('GET', '/' + kind)), kind
    original = request('GET', '/chains/' + state['original']['id'])
    assert {k: original[k] for k in state['original']} == state['original']
    assert signature(request('GET', '/chains/' + original['id'] + '/connection')) == state['connection_hash']
    STATE.unlink()
    Path('/tmp/orchestration-browser.json').unlink(missing_ok=True)
    print('Acceptance resources, samples/events and temporary session removed; original configuration and credentials preserved')
else:
    raise ValueError(mode)
