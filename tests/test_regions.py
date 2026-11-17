from app.regions import country, detect, flag, label, split, describe, country_label, CODES


def test_known_codes_get_chinese_name_and_flag():
    assert split("HKT") == ("🇭🇰", "香港")
    assert label("GZCM1") == "🇨🇳 广州移动"
    assert label("los") == "🇺🇸 洛杉矶"


def test_unknown_code_falls_back_to_itself():
    emoji, name = split("ZZ9")
    assert name == "ZZ9"
    assert emoji == flag("UN")
    assert label("ZZ9").endswith("ZZ9")


def test_flag_from_country_code():
    assert flag("DE") == "🇩🇪"
    assert flag("gb") == "🇬🇧"
    assert flag("X") == ""
    assert flag("12") == ""
    assert flag("中文") == ""
    assert flag("ZZ") == ""


def test_region_codes_map_to_country():
    assert country("LOS") == "US"
    assert country("los") == "US"
    assert country("SJ7") == "US"  # 同一国家的入口代号合并计数
    assert country("HKT") == "HK"
    assert country("ZZ9") == "UN"  # 未收录的代号归入未知


def test_detect_country_from_node_name():
    assert detect("🇯🇵 日本 01") == "JP"  # 国旗最可靠
    assert detect("HK-01") == "HK"  # 独立的两字母代号
    assert detect("洛杉矶 IPLC") == "US"  # 中文城市名
    assert detect("美国-圣何塞") == "US"
    assert detect("JP to US relay") == "JP"  # 多个命中时取最靠前的一个


def test_detect_ignores_unrelated_text():
    assert detect("VIP 专线 01") is None  # VIP 不是国家代号
    assert detect("plus-node") is None  # 小写单词里的 us 不算
    assert detect("") is None
    assert detect(None) is None


def test_official_names_resolve_egress_countries():
    for name, expected in {
        "東京": "JP", "台北": "TW", "汉城": "KR", "奧尅蘭": "NZ", "孟買": "IN",
        "新西伯利亚": "RU", "伊斯坦布爾": "TR", "Seattle 6G": "US",
        "Silicon Valley 4G": "US", "Los Angeles[CM\\": "US", "US-ISP": "US",
        "Mexico City": "MX", "Madrid": "ES", "香港01[CM]": "HK",
    }.items():
        assert detect(name) == expected


def test_normalized_regions_and_aliases():
    assert describe(' gzcm1 ') == {'code': 'GZCM1', 'name': '广州移动', 'country': 'CN'}
    assert describe('los')['country'] == 'US'
    assert describe('unknown')['country'] == 'UN'
    assert country(' uk ') == 'GB'
    assert country('mo') == 'MO'
    assert country_label('uk') == '🇬🇧 英国'
    assert country_label('jp') == '🇯🇵 日本'
    assert detect('UK-01') == 'GB'
    assert detect('Tokyo 01') == 'JP'
    assert detect('London Premium') == 'GB'
    assert detect('Germany 01') == 'DE'
    assert detect('🇯 separated 🇵') is None
    assert detect('Parisian premium') is None


def test_all_known_countries_have_local_flag_assets():
    from pathlib import Path
    import xml.etree.ElementTree as ET
    folder = Path(__file__).resolve().parents[1] / 'frontend/src/assets/flags'
    for code in CODES:
        svg = ET.parse(folder / (code.lower() + '.svg')).getroot()
        assert svg.tag == '{http://www.w3.org/2000/svg}svg'


def test_node_api_distinguishes_ingress_and_egress(client, account_id):
    from app.models import Node
    from sqlalchemy import select
    service = client.app.state.service
    client.portal.call(service.ensure_refresh, account_id)
    with service.db.begin() as db:
        node = db.scalar(select(Node).where(Node.region == 'GZCM1'))
        node.official_name = '台北'
    row = next(n for n in client.get('/api/v1/nodes').json() if n['region'] == 'GZCM1')
    assert row['region_country'] == 'CN' and row['region_name'] == '广州移动'
    sub = client.post('/api/v1/subscriptions', json={'name': '地区测试', 'account_ids': [account_id]}).json()
    detail = client.get(f"/api/v1/subscriptions/{sub['id']}/nodes").json()['nodes']
    assert next(n for n in detail if '台北' in n['name'])['country'] == 'TW'
