"""Readable labels for the region codes carried in PAC hostnames.

The upstream PAC only encodes a coarse ingress/egress code in the second label of each
node hostname (`<uuid>-<session>-tp1cgt-w7lw.<code>.<cdn>`), so these labels are our own
translation, not data returned by GHelper.
"""

import re

# code -> (中文名, ISO 3166-1 alpha-2 用于国旗)
REGIONS: dict[str, tuple[str, str]] = {
    # 香港
    "HKT": ("香港", "HK"),
    "HK": ("香港", "HK"),
    # 国内接入点，不是海外出口
    "GZCM1": ("广州移动", "CN"),
    "FSCM8": ("佛山移动", "CN"),
    "CM9": ("移动接入", "CN"),
    "CISP": ("CISP接入", "CN"),
    "VIPHUAWEI": ("华为云接入", "CN"),
    "CN": ("中国大陆", "CN"),
    # 海外
    "LOS": ("洛杉矶", "US"),
    "SJ7": ("圣何塞", "US"),
    "SEA2": ("西雅图", "US"),
    "US": ("美国", "US"),
    "CA": ("加拿大", "CA"),
    "MX": ("墨西哥", "MX"),
    "AU": ("澳大利亚", "AU"),
    "IN": ("印度", "IN"),
    "DE": ("德国", "DE"),
    "FR": ("法国", "FR"),
    "IT": ("意大利", "IT"),
    "ES": ("西班牙", "ES"),
    "RU": ("俄罗斯", "RU"),
    "JP": ("日本", "JP"),
    "KR": ("韩国", "KR"),
    "SG": ("新加坡", "SG"),
    "TW": ("台湾", "TW"),
    "UK": ("英国", "GB"),
    "GB": ("英国", "GB"),
    "NL": ("荷兰", "NL"),
    "BR": ("巴西", "BR"),
    "TR": ("土耳其", "TR"),
    "VN": ("越南", "VN"),
    "TH": ("泰国", "TH"),
    "MY": ("马来西亚", "MY"),
    "ID": ("印度尼西亚", "ID"),
    "PH": ("菲律宾", "PH"),
    "AE": ("阿联酋", "AE"),
    "IL": ("以色列", "IL"),
    "PL": ("波兰", "PL"),
    "SE": ("瑞典", "SE"),
    "CH": ("瑞士", "CH"),
    "IE": ("爱尔兰", "IE"),
    "NZ": ("新西兰", "NZ"),
    "ZA": ("南非", "ZA"),
    "AR": ("阿根廷", "AR"),
    "CL": ("智利", "CL"),
}

UNKNOWN = ("未知", "UN")

# 节点名里常见的城市/别名（机场常用城市而不是国家命名），归到所属国家。
CITIES: dict[str, str] = {
    "台北": "TW", "东京": "JP", "大阪": "JP", "名古屋": "JP", "埼玉": "JP", "首尔": "KR", "釜山": "KR",
    "纽约": "US", "芝加哥": "US", "达拉斯": "US", "硅谷": "US", "凤凰城": "US", "迈阿密": "US",
    "拉斯维加斯": "US", "波特兰": "US", "亚特兰大": "US", "阿什本": "US", "丹佛": "US", "圣克拉拉": "US",
    "伦敦": "GB", "法兰克福": "DE", "柏林": "DE", "慕尼黑": "DE", "巴黎": "FR", "阿姆斯特丹": "NL",
    "莫斯科": "RU", "多伦多": "CA", "温哥华": "CA", "蒙特利尔": "CA", "悉尼": "AU", "墨尔本": "AU",
    "孟买": "IN", "班加罗尔": "IN", "新德里": "IN", "迪拜": "AE", "圣保罗": "BR", "奥克兰": "NZ",
    "米兰": "IT", "罗马": "IT", "马德里": "ES", "苏黎世": "CH", "都柏林": "IE", "斯德哥尔摩": "SE",
    "华沙": "PL", "特拉维夫": "IL", "伊斯坦布尔": "TR", "胡志明": "VN", "河内": "VN", "曼谷": "TH",
    "吉隆坡": "MY", "雅加达": "ID", "马尼拉": "PH", "墨西哥城": "MX", "布宜诺斯艾利斯": "AR",
}

# 节点名里会被当作国家代号识别的两字母代码（含 REGIONS 未收录但机场常用的）。
CODES = frozenset({country for _, country in REGIONS.values()} | set(CITIES.values()) | {
    "MO", "UA", "KZ", "EG", "SA", "QA", "FI", "NO", "DK", "AT", "BE", "PT", "CZ", "GR", "HU",
    "RO", "BG", "HR", "LT", "LV", "EE", "SK", "SI", "RS", "BY", "GE", "AM", "AZ", "UZ", "MN",
    "NP", "PK", "BD", "LK", "KH", "LA", "BN", "GU", "PR", "PA", "CR", "PE", "CO", "VE", "UY",
    "PY", "BO", "EC", "GT", "DO", "JM", "CU", "NG", "KE", "MA", "TN", "DZ", "KW", "OM", "BH",
    "JO", "LB", "CY", "IS", "LU", "MT", "MC", "MD",
})

_NAMES = {name: country for name, country in REGIONS.values() if country != "UN"} | CITIES
# 长名字优先：先匹配「洛杉矶」就不会退化成「美国」这类更宽的名字
_NAMES_BY_LENGTH = sorted(_NAMES.items(), key=lambda item: -len(item[0]))


def flag(country: str) -> str:
    """地区代码两字母 → 国旗 emoji（区域指示符字符），非法国家码返回空串。"""
    if len(country) != 2 or not country.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in country.upper())


def split(code: str) -> tuple[str, str]:
    """代号 → (国旗, 中文名)；未收录的代号原样返回。"""
    name, country = REGIONS.get(code.upper(), (code, "UN"))
    return flag(country), name


def label(code: str) -> str:
    """代号 → '🇭🇰 香港'；未收录的代号返回 '🇺🇳 代号'。"""
    emoji, name = split(code)
    return f"{emoji} {name}" if emoji else name


def country(code: str) -> str:
    """入口代号 → 国家两字母代码（LOS/圣何塞都归 US）；未收录的代号返回 UN。"""
    return REGIONS.get(str(code).upper(), ("", "UN"))[1]


def _from_flag(name: str) -> str | None:
    """名字里的国旗 emoji（两个区域指示符）→ 国家代码。"""
    letters = [ord(ch) - 0x1F1E6 for ch in name if 0x1F1E6 <= ord(ch) <= 0x1F1FF]
    for first, second in zip(letters, letters[1:]):  # 相邻的两个区域指示符才是一面旗
        if 0 <= first < 26 and 0 <= second < 26:
            code = chr(65 + first) + chr(65 + second)
            if code in CODES:
                return code
    return None


def detect(name: str) -> str | None:
    """从订阅源节点名猜国家：国旗 → 中英文地名 → 独立的两字母代号；猜不出返回 None。

    机场的节点名没有统一格式，这里只做保守识别：宁可归入「未知」，也不把
    名字里偶然出现的字母当成国家。GHelper 节点不走这里，直接用自己的地区代号。
    """
    if not isinstance(name, str) or not name:
        return None
    if found := _from_flag(name):
        return found
    best = None  # (名字长度, 出现位置) 越小越优先
    for text, code in _NAMES_BY_LENGTH:
        index = name.find(text)
        if index >= 0 and (best is None or (-len(text), index) < best[0]):
            best = ((-len(text), index), code)
    if best:
        return best[1]
    if found := next((t for t in re.findall(r"(?<![A-Za-z0-9])[A-Z]{2}(?![A-Za-z0-9])", name)
                      if t in CODES), None):
        return found
    return None
