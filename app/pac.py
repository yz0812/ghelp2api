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
    code = re.search(r"-([a-z0-9]{6})-tp1cgt-w7lw\.", main[2], re.I)
    source = pac if code else main[2]
    candidates = re.finditer(r"\bHTTPS\s+([a-zA-Z0-9.-]+):(\d{1,6})(?!\d)", source, re.I)
    nodes: dict[tuple[str, int], Proxy] = {}
    for match in candidates:
        server, port = match[1].lower(), int(match[2])
        if code and f"-{code[1].lower()}-tp1cgt-w7lw." not in server:
            continue
        if not 1 <= port <= 65535 or len(server) > 253:
            continue
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
               for p in server.split(".")):
            continue
        region = server.split(".")[1].upper() if code else "UNKNOWN"
        nodes[(server, port)] = Proxy(server, port, region)
    if not nodes:
        raise PacError("PAC 中没有可用的 HTTPS 节点")
    return list(nodes.values()), code is None
