import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Proxy:
    server: str
    port: int
    region: str


class PacError(ValueError):
    pass


def parse_pac(pac: str) -> tuple[list[Proxy], bool]:
    if not isinstance(pac, str) or len(pac) > 2_000_000:
        raise PacError("PAC 数据为空或超过大小限制")
    main = re.search(r"\b(?:var|let|const)\s+proxy\s*=\s*(['\"])(.*?)\1", pac, re.S)
    if not main:
        raise PacError("PAC 主节点格式无法识别")
    # 节点域名形如 <uuid>-<会话号6>-<标记6>-<批次标记>.<入口代号>.<CDN>。除 uuid 外的三段都由
    # 上游按账号/批次下发：曾从 tnqw00-tp1cgt-w7lw 换成 tomdc0-ttrpst-w7lw，随后第四段又出现
    # 1tuas（同一账号内也可能不同）。写死任何一段都会退化成只认出主列表里的少数节点，所以这里
    # 只取前两段 6 位代码作为过滤标记，第四段按任意短标记处理。
    marker = re.search(r"-([a-z0-9]{6})-([a-z0-9]{6})-[a-z0-9]{3,8}\.", main[2], re.I)
    tag = f"-{marker.group(1)}-{marker.group(2)}-".lower() if marker else None
    source = pac if tag else main[2]
    candidates = re.finditer(r"\bHTTPS\s+([a-zA-Z0-9.-]+):(\d{1,6})(?!\d)", source, re.I)
    nodes: dict[tuple[str, int], Proxy] = {}
    for match in candidates:
        server, port = match[1].lower(), int(match[2])
        if tag and tag not in server:
            continue
        if not 1 <= port <= 65535 or len(server) > 253:
            continue
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
               for p in server.split(".")):
            continue
        region = server.split(".")[1].upper() if tag else "UNKNOWN"
        nodes[(server, port)] = Proxy(server, port, region)
    if not nodes:
        raise PacError("PAC 中没有可用的 HTTPS 节点")
    return list(nodes.values()), tag is None
