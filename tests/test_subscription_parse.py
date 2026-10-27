import base64
import json

import pytest

from app.subscription import (SubscriptionError, parse_clash, parse_link, parse_links,
                              parse_subscription)


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def test_clash_yaml_keeps_supported_and_drops_the_rest():
    text = """
proxies:
  - {name: 香港01, type: ss, server: hk.example.com, port: 443, cipher: aes-128-gcm, password: p}
  - {name: HTTP节点, type: http, server: 84.239.49.59, port: 9002, tls: true, sni: www.bing.com}
  - {name: 未知, type: wireguard, server: wg.example.com, port: 51820}
  - {name: 坏端口, type: ss, server: bad.example.com, port: 70000, cipher: aes-128-gcm, password: p}
  - name: 多余字段
    type: trojan
    server: t.example.com
    port: 8443
    password: secret
    some-unknown-key: should-be-dropped
"""
    nodes = parse_clash(text)
    assert [n["name"] for n in nodes] == ["香港01", "HTTP节点", "多余字段"]
    assert nodes[1]["sni"] == "www.bing.com" and nodes[1]["tls"] is True
    assert "some-unknown-key" not in nodes[2]


def test_clash_yaml_without_proxies_fails():
    with pytest.raises(SubscriptionError):
        parse_subscription("rules:\n  - MATCH,DIRECT\n")


def test_base64_document_is_decoded():
    body = "\n".join([
        "ss://" + b64("aes-256-gcm:pw") + "@ss.example.com:8388#节点A",
        "trojan://pass@tj.example.com:443?sni=tj.example.com#节点B",
    ])
    nodes = parse_subscription(b64(body))
    assert [n["name"] for n in nodes] == ["节点A", "节点B"]
    assert nodes[0] == {"name": "节点A", "type": "ss", "server": "ss.example.com", "port": 8388,
                        "cipher": "aes-256-gcm", "password": "pw", "udp": True}


def test_ss_legacy_format():
    node = parse_link("ss://" + b64("aes-128-gcm:secret@old.example.com:9000") + "#旧格式")
    assert node["server"] == "old.example.com"
    assert node["port"] == 9000
    assert node["password"] == "secret"


def test_vmess_json_link():
    payload = {"v": "2", "ps": "VM节点", "add": "vm.example.com", "port": "443", "id": "uuid-1",
               "aid": "0", "scy": "auto", "net": "ws", "host": "cdn.example.com",
               "path": "/ray", "tls": "tls", "sni": "cdn.example.com"}
    node = parse_link("vmess://" + b64(json.dumps(payload)))
    assert node["type"] == "vmess"
    assert node["uuid"] == "uuid-1"
    assert node["tls"] is True
    assert node["servername"] == "cdn.example.com"
    assert node["ws-opts"]["path"] == "/ray"
    assert node["ws-opts"]["headers"]["Host"] == "cdn.example.com"


def test_vless_reality_and_transport():
    link = ("vless://uuid-2@vl.example.com:443?encryption=none&security=reality&sni=www.example.com"
            "&pbk=publickey&sid=abcd&fp=chrome&type=grpc&serviceName=svc#VL节点")
    node = parse_link(link)
    assert node["tls"] is True
    assert node["reality-opts"] == {"public-key": "publickey", "short-id": "abcd"}
    assert node["client-fingerprint"] == "chrome"
    assert node["grpc-opts"]["grpc-service-name"] == "svc"


def test_trojan_and_hysteria2():
    trojan = parse_link("trojan://pw@tj.example.com:8443?sni=tj.example.com&allowInsecure=1#T节点")
    assert trojan["skip-cert-verify"] is True
    assert trojan["sni"] == "tj.example.com"

    hy2 = parse_link("hysteria2://pw@hy.example.com:443?sni=hy.example.com&insecure=1&obfs=salamander"
                     "&obfs-password=xyz#H节点")
    assert hy2["type"] == "hysteria2"
    assert hy2["obfs"] == "salamander"
    assert hy2["skip-cert-verify"] is True


@pytest.mark.parametrize("link", [
    "ss://not-base64@@@", "vmess://" + b64("not json"), "trojan://@host:443",
    "vless://uuid@:443", "http://plain.example.com",
])
def test_bad_links_are_skipped(link):
    with pytest.raises(SubscriptionError):
        parse_links(link)


def test_mixed_list_skips_bad_lines_but_keeps_good_ones():
    text = "\n".join([
        "# comment",
        "ss://" + b64("aes-128-gcm:pw") + "@good.example.com:443#好节点",
        "vmess://@@not-base64",
        "socks5://ignored.example.com:1080",
    ])
    nodes = parse_links(text)
    assert [n["name"] for n in nodes] == ["好节点"]


def test_control_characters_are_stripped_from_names():
    node = parse_link("ss://" + b64("aes-128-gcm:pw") + "@x.example.com:443#%00%E4%B8%AD%0A%E6%96%87")
    assert node["name"] == "中文"


def test_clash_drops_name_references_and_host_settings():
    text = """
proxies:
  - {name: 链式, type: ss, server: a.example.com, port: 443, cipher: aes-128-gcm, password: p,
     dialer-proxy: 前置, interface-name: eth0, routing-mark: 6666}
  - {name: TLS代理, type: https, server: h.example.com, port: 443, username: u, password: p}
"""
    chained, tls_http = parse_clash(text)
    assert not {"dialer-proxy", "interface-name", "routing-mark"} & set(chained)
    assert tls_http["type"] == "http" and tls_http["tls"] is True  # mihomo 没有 https 类型


def test_ss_plugin_is_converted_or_rejected():
    userinfo = b64("aes-128-gcm:pw")
    obfs = parse_link(f"ss://{userinfo}@ss.example.com:8388/?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dbing.com#A")
    assert obfs["plugin"] == "obfs" and obfs["plugin-opts"] == {"mode": "http", "host": "bing.com"}
    v2ray = parse_link(f"ss://{userinfo}@ss.example.com:8388/?plugin=v2ray-plugin%3Btls%3Bhost%3Dcdn.com#B")
    assert v2ray["plugin-opts"] == {"mode": "websocket", "tls": True, "host": "cdn.com"}
    # 不认识的插件不能静默丢掉，否则导出的节点必然连不上
    assert parse_link(f"ss://{userinfo}@ss.example.com:8388/?plugin=kcptun#C") is None


def test_hy2_scheme_alias():
    node = parse_link("hy2://secret@hy.example.com:8443?sni=x.com#H")
    assert node["type"] == "hysteria2" and node["password"] == "secret"
