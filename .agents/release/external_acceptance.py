import json
from pathlib import Path
from urllib.parse import quote

import httpx

auth = json.loads(Path('.cache/release/browser.json').read_text())
cookie = auth['storage']['cookies'][0]['value']
with httpx.Client(base_url='https://ghelper.accsunny.com', cookies={'ghelper_session': cookie}, trust_env=False, timeout=30) as api:
    response = api.get('/api/v1/chains')
    assert response.is_success, 'external authenticated API failed'
    chains = response.json()
    for chain in chains:
        if chain['id'] != auth['chain_id'] and chain['listen_port'] != 20000:
            continue
        connection = api.get('/api/v1/chains/' + chain['id'] + '/connection').json()
        host = connection['host']
        proxy = f"http://{quote(connection['username'], safe='')}:{quote(connection['password'], safe='')}@{host}:{connection['port']}"
        result = {'port': connection['port'], 'tcp_http_proxy': False, 'https_tunnel': False}
        with httpx.Client(proxy=proxy, trust_env=False, timeout=50) as transport:
            for url, key in [('http://example.com', 'tcp_http_proxy'), ('https://api.ipify.org?format=json', 'https_tunnel')]:
                try:
                    r = transport.get(url)
                    result[key] = r.is_success
                    result[key + '_status'] = r.status_code
                except httpx.HTTPError as exc:
                    result[key + '_error'] = type(exc).__name__
        print(json.dumps(result), flush=True)
        assert result['tcp_http_proxy'] and result['https_tunnel'], 'external proxy business acceptance failed'
