import pytest
import yaml

from app.clash import render
from app.pac import parse_pac
from tests.fixtures import PAC


def test_all_current_session_nodes():
    nodes, degraded = parse_pac(PAC)
    assert len(nodes) == 3
    assert {n.region for n in nodes} == {"HKT", "LOS", "GZCM1"}
    assert not degraded


def test_fallback_and_bad_ports():
    nodes, degraded = parse_pac('var proxy="HTTPS valid.example.com:443; HTTPS invalid.example.com:70000; SOCKS5 other.example.com:80";')
    assert degraded and len(nodes) == 1


@pytest.mark.parametrize("pac", [None, "", 'var proxy="DIRECT";', 'var proxy="HTTPS bad.example.com:0";', 'var proxy="HTTPS bad.example.com:4430000";'])
def test_invalid_pac(pac):
    with pytest.raises(ValueError):
        parse_pac(pac)


def test_yaml_render_groups_and_unicode():
    name = "账号-🇭🇰 香港-HKT-abc123"
    proxy = {"name": name, "type": "http", "server": "node.example.com", "port": 443,
             "tls": True, "skip-cert-verify": True, "udp": False}
    output = yaml.safe_load(render([proxy], {"账号 · 测试": [name]}))
    assert output["proxies"] == [proxy]
    assert {g["name"] for g in output["proxy-groups"]} == {"节点选择", "自动选择", "账号 · 测试"}
    assert yaml.safe_load(render([proxy], {"账号 · 测试": [name]}, full=False)) == {"proxies": [proxy]}


def test_mihomo_validation_script_matches_render_signature():
    """脚本曾在 render() 改签名后静默失败（CI 才发现），这里把它纳进单元测试。"""
    from scripts.validate_mihomo import synthetic_config

    output = yaml.safe_load(synthetic_config())
    names = {p["name"] for p in output["proxies"]}
    assert {g["name"] for g in output["proxy-groups"]} >= {"账号 · 合成测试账号", "来源 · 机场"}
    builtin = {g["name"] for g in output["proxy-groups"]} | {"DIRECT"}
    assert all(p in names | builtin for g in output["proxy-groups"] for p in g["proxies"])
