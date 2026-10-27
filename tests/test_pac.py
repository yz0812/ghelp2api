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


def test_yaml_escapes_and_aggregate_deduplicates():
    row = {"server": "node.example.com", "port": 443, "region": "HKT", "account_id": "account1", "account_name": 'name: "test"\nunsafe'}
    other = row | {"account_id": "account2"}
    output = yaml.safe_load(render([row, other]))
    assert len(output["proxies"]) == 1
    assert len(output["proxy-groups"]) == 4
    assert "\n" not in output["proxies"][0]["name"]
