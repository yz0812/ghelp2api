import ast
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Proxy:
    server: str
    port: int
    region: str


class PacError(ValueError):
    pass


def official_names(pac: str, servers: list) -> dict[tuple[str, int], str]:
    """按测速域名关联官方名称；只使用 PAC 内明确给出的代理地址，不按入口猜测。"""
    match = re.search(r"\b(?:var|let|const)\s+test_proxy_domains\s*=\s*(\{[^{}]*\})\s*;", pac)
    if not match or not isinstance(servers, list):
        return {}
    try:
        routes = ast.literal_eval(match[1])
    except (ValueError, SyntaxError, RecursionError):
        return {}
    if not isinstance(routes, dict):
        return {}
    names: dict[tuple[str, int], set[str]] = {}
    for server in servers:
        if not isinstance(server, dict):
            continue
        name, ping_url = server.get("name"), server.get("ping_url")
        if not isinstance(name, str) or not isinstance(ping_url, str):
            continue
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:160]
        try:
            host = urlsplit(ping_url).hostname
        except ValueError:
            continue
        route = routes.get(host)
        if not name or not isinstance(route, str):
            continue
        for target, port in re.findall(r"\bHTTPS\s+([a-zA-Z0-9.-]+):(\d{1,6})(?!\d)", route, re.I):
            names.setdefault((target.lower(), int(port)), set()).add(name)
    # 同一代理关联多个不同名称时无法确定出口，保留未知状态。
    return {key: next(iter(values)) for key, values in names.items() if len(values) == 1}


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
