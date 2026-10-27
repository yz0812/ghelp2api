from app.regions import flag, label, split


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
