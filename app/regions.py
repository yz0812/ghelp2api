"""Readable labels for the region codes carried in PAC hostnames.

The upstream PAC only encodes a coarse ingress/egress code in the second label of each
node hostname (`<uuid>-<session>-tp1cgt-w7lw.<code>.<cdn>`), so these labels are our own
translation, not data returned by GHelper.
"""

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
