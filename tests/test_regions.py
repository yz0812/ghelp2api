from app.regions import country, detect, flag, label, split


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
