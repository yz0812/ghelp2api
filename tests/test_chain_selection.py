from types import SimpleNamespace

import httpx
import pytest
import yaml

from app.chains import ChainError
from app.models import Chain, Source
from tests.test_chains import setup_chain


def test_pinned_entry_validation_export_probe_and_invalidation(client, monkeypatch):
    sub, entry, exit, chain = setup_chain(client)
    nodes = client.get(f"/api/v1/entries/{entry['id']}/nodes").json()
    selected = nodes[1]
    url = f"/api/v1/chains/{chain['id']}"
    payload = dict(name=chain['name'], entry_id=entry['id'], exit_id=exit['id'], entry_mode='fixed')
    for key in (None, '', 'missing'):
        assert client.put(url, json=dict(payload, entry_node_key=key)).status_code == 422
    assert client.get('/api/v1/chains').json()[0]['entry_mode'] == 'auto'

    payload['entry_node_key'] = selected['key']
    response = client.put(url, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['selected_entry'] == selected['name']
    export = client.post('/api/v1/subscriptions', json={'name': '指定链路', 'chain_ids': [chain['id']]}).json()
    config = yaml.safe_load(client.get(export['clash_url']).text)
    assert len(config['proxies']) == 2
    assert config['proxies'][0]['server'] == selected['server']
    group = next(g for g in config['proxy-groups'] if g['name'].startswith('链路 ·'))
    assert group['type'] == 'select' and len(group['proxies']) == 1

    async def measure(proxies, group):
        assert len(proxies) == 2
        assert proxies[0]['server'] == selected['server']
        return {group['proxies'][0]: 150}

    service = client.app.state.service
    monkeypatch.setattr(service.chains, '_measure', measure)
    result = client.post(url + '/probe').json()
    assert result['reachable'] and result['entry_key'] == selected['key']
    assert client.get('/api/v1/chains').json()[0]['probe'] == result
    # A different pinned node must not reuse the previous node's result.
    changed = client.put(url, json=dict(payload, entry_node_key=nodes[0]['key'])).json()
    assert changed['probe'] is None
    restored = client.put(url, json=payload).json()
    assert restored['probe'] == result
    # Removing the pinned node must never fall back to another candidate.
    with service.db.begin() as db:
        source = db.get(Source, sub['source_ids'][0])
        source.proxies = [p for p in source.proxies if p['server'] != selected['server']]
    with service.db() as db, pytest.raises(ChainError, match='指定入口节点已失效'):
        service.chains.build(db.get(Chain, chain['id']))
    result = client.post(url + '/probe').json()
    assert result['reachable'] is False and '指定入口' in result['error']
    assert client.get(export['clash_url']).status_code == 503
    # Switching back to automatic mode clears the pinned key.
    changed = client.put(url, json=dict(payload, entry_mode='auto')).json()
    assert changed['entry_node_key'] is None
    assert client.get(export['clash_url']).status_code == 200


def test_pinned_node_must_belong_to_entry_pool(client):
    sub, entry, exit, chain = setup_chain(client, fixed=True)
    nodes = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()['nodes']
    candidates = client.get(f"/api/v1/entries/{entry['id']}/nodes").json()
    assert [n['key'] for n in candidates] == entry['node_keys']
    outside = next(n for n in nodes if n['key'] not in entry['node_keys'])
    response = client.put(f"/api/v1/chains/{chain['id']}", json={
        'name': chain['name'], 'entry_id': entry['id'], 'exit_id': exit['id'],
        'entry_mode': 'fixed', 'entry_node_key': outside['key']})
    assert response.status_code == 422 and '候选' in response.json()['detail']


def test_current_entry_comes_from_live_core_and_clears_when_unavailable(client, monkeypatch):
    _, _, _, chain = setup_chain(client)
    service = client.app.state.service
    manager = service.chains
    with service.db() as db:
        _, group = manager.build(db.get(Chain, chain['id']))
    terminal = group['proxies'][1]

    async def request(*args, **kwargs):
        return {'proxies': {group['name']: {'now': terminal}, terminal: {'history': [{'delay': 42}]}}}

    # Avoid launching a real process; restore it before fixture teardown.
    with monkeypatch.context() as patch:
        patch.setattr(manager, 'process', SimpleNamespace(returncode=None))
        patch.setattr(manager, '_request', request)
        manager.status[chain['id']] = {'state': 'running', 'group': group['name']}
        status = client.get('/api/v1/chains').json()[0]['status']
        assert status['current_entry'] == manager.entry_labels[terminal]
        assert status['current_entry_key'] == manager.entry_keys[terminal]
        assert status['latency'] == 42

        async def failed(*args, **kwargs):
            raise httpx.ConnectError('unavailable')

        patch.setattr(manager, '_request', failed)
        status = client.get('/api/v1/chains').json()[0]['status']
        assert 'current_entry' not in status and 'latency' not in status
        manager.process.returncode = 1
        assert client.get('/api/v1/chains').json()[0]['status']['state'] == 'error'


def test_entry_selection_migration_defaults_existing_chains_to_auto(tmp_path):
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    config = Config('alembic.ini')
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0010')
        connection.execute(sa.text("INSERT INTO chains "
                                   "(id,name,entry_id,exit_id,enabled,relay_enabled,listen_port,public_host,username,password_cipher) "
                                   "VALUES ('old','old','entry','exit',1,1,25000,'','user','cipher')"))
        command.upgrade(config, 'head')
        assert connection.execute(sa.text('SELECT entry_mode,entry_node_key FROM chains')).one() == ('auto', None)
        command.downgrade(config, '0010')
        assert connection.execute(sa.text('SELECT name FROM chains')).scalar() == 'old'
    engine.dispose()
