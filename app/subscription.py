"""把第三方订阅内容解析成 Clash 节点数组（仅做数据转换，不发起网络请求）。"""

import base64
import binascii
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

MAX_PROXIES = 2000
MAX_CHARS = 5_000_000
SUPPORTED = {"ss", "ssr", "vmess", "trojan", "vless", "hysteria2", "http", "socks5"}
# 只保留 mihomo 认识的字段，避免把上游的任意键带进生成的配置。
# dialer-proxy 按名称引用其他节点（改名后会悬空），interface-name/routing-mark 是上游本机设置，都不保留。
KEEP = {
    "name", "type", "server", "port", "udp", "tfo", "mptcp", "ip-version", "smux", "username", "password", "uuid", "alterId", "cipher",
    "auth", "flow", "packet-encoding", "udp-over-tcp", "udp-over-tcp-version", "plugin",
    "plugin-opts", "obfs", "obfs-password", "protocol", "protocol-param", "protocol-version",
    "tls", "sni", "servername", "skip-cert-verify", "alpn", "fingerprint", "reality-opts",
    "client-fingerprint", "network", "ws-opts", "grpc-opts", "h2-opts", "http-opts",
}
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SubscriptionError(ValueError):
    pass


def clean_name(value, fallback):
    text = CONTROL.sub("", str(value or "")).strip()
    return (text or fallback)[:60]


def parse_subscription(text: str) -> list[dict]:
    """自动识别 Clash YAML 或链接列表；无法解析时抛 SubscriptionError。"""
    if not isinstance(text, str) or not text.strip():
        raise SubscriptionError("订阅内容为空")
    if len(text) > MAX_CHARS:
        raise SubscriptionError("订阅内容超过大小限制")
    text = text.lstrip("﻿")
    if re.search(r"^proxies\s*:", text, re.M):
        return parse_clash(text)
    return parse_links(decode_base64(text))


def decode_base64(text: str) -> str:
    """整体 base64 的订阅先解码；解码失败说明本来就是明文链接。"""
    compact = "".join(text.split())
    if not compact or "://" in compact:
        return text
    try:
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False)
        decoded = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return text
    return decoded if "://" in decoded else text


def parse_clash(text: str) -> list[dict]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise SubscriptionError("Clash 配置无法解析") from None
    items = data.get("proxies") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SubscriptionError("Clash 配置里没有 proxies 列表")
    nodes = []
    for index, item in enumerate(items[:MAX_PROXIES]):
        proxy = sanitize(item, f"节点{index + 1}")
        if proxy:
            nodes.append(proxy)
    if not nodes:
        raise SubscriptionError("订阅里没有支持的节点")
    return nodes


def sanitize(item, fallback_name: str) -> dict | None:
    """校验并裁剪单个 Clash 节点；不合法返回 None。"""
    if not isinstance(item, dict):
        return None
    node = {k: v for k, v in item.items() if k in KEEP}
    kind = str(node.get("type") or "").lower()
    if kind == "https":  # mihomo 没有 https 类型，TLS 的 HTTP 代理写作 http + tls
        kind, node["tls"] = "http", True
    server = str(node.get("server") or "").strip()
    if kind not in SUPPORTED or not _valid_server(server):
        return None
    port = node.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    node["type"], node["server"], node["port"] = kind, server, port
    node["name"] = clean_name(node.get("name"), f"{fallback_name}-{server}")
    return node


def _valid_server(server: str) -> bool:
    if not server or len(server) > 253:
        return False
    if server.startswith("[") and server.endswith("]"):  # IPv6 字面量
        return bool(re.fullmatch(r"\[[0-9A-Fa-f:.]+\]", server))
    return bool(HOST.fullmatch(server))


def parse_links(text: str) -> list[dict]:
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "://" not in line:
            continue
        try:
            node = parse_link(line)
        except (ValueError, KeyError, IndexError):
            node = None
        if node:
            nodes.append(node)
        if len(nodes) >= MAX_PROXIES:
            break
    if not nodes:
        raise SubscriptionError("订阅里没有支持的节点")
    return nodes


def parse_link(link: str) -> dict | None:
    scheme = link.split("://", 1)[0].lower()
    parser = {"ss": parse_ss, "vmess": parse_vmess, "trojan": parse_trojan,
              "vless": parse_vless, "hysteria2": parse_hysteria2, "hy2": parse_hysteria2}.get(scheme)
    return parser(link) if parser else None


def _host_port(target: str) -> tuple[str, int]:
    target = target.strip("/ ")
    parts = urlsplit("//" + target)
    if not parts.hostname or not parts.port:
        raise ValueError("缺少服务器或端口")
    if not _valid_server(parts.hostname) or not 1 <= parts.port <= 65535:
        raise ValueError("服务器或端口不合法")
    return parts.hostname, parts.port


def _fragment(link: str) -> str:
    return unquote(link.split("#", 1)[1]) if "#" in link else ""


def _query(link: str) -> dict[str, str]:
    raw = link.split("?", 1)[1].split("#", 1)[0] if "?" in link else ""
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items() if v}


def _truthy(value) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _b64(text: str) -> str:
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=False).decode("utf-8", "replace")


def parse_ss(link: str) -> dict | None:
    body = link[5:].split("#", 1)[0].split("?", 1)[0]
    if "@" not in body:
        body = _b64(body)  # 旧式：整体 base64
    userinfo, hostport = body.rsplit("@", 1)
    credentials = userinfo if ":" in userinfo else _b64(userinfo)
    if ":" not in credentials:
        credentials = unquote(userinfo)
    cipher, password = credentials.split(":", 1)
    server, port = _host_port(hostport)
    if not cipher or not password:
        return None
    node = {"name": clean_name(_fragment(link), f"ss-{server}"), "type": "ss", "server": server,
            "port": port, "cipher": cipher, "password": password, "udp": True}
    plugin = _query(link).get("plugin")
    if plugin:
        converted = _ss_plugin(plugin)
        if not converted:
            return None  # 不认识的插件：丢掉插件参数导出的节点必然连不上，宁可不要
        node["plugin"], node["plugin-opts"] = converted
    return node


def _ss_plugin(value: str) -> tuple[str, dict] | None:
    """SIP002 插件串（`obfs-local;obfs=http;obfs-host=x`）转成 mihomo 的 plugin/plugin-opts。"""
    name, *args = [part for part in value.split(";") if part]
    options = dict(arg.split("=", 1) if "=" in arg else (arg, "") for arg in args)
    if name in {"obfs-local", "simple-obfs"}:
        mode = options.get("obfs")
        if mode not in {"http", "tls"}:
            return None
        opts = {"mode": mode}
        if options.get("obfs-host"):
            opts["host"] = options["obfs-host"]
        return "obfs", opts
    if name == "v2ray-plugin":
        if options.get("mode", "websocket") != "websocket":
            return None
        opts = {"mode": "websocket"}
        if "tls" in options:
            opts["tls"] = True
        for key in ("host", "path"):
            if options.get(key):
                opts[key] = options[key]
        return "v2ray-plugin", opts
    return None


def parse_vmess(link: str) -> dict | None:
    body = link[8:].split("#", 1)[0]
    info = json.loads(_b64(body))
    server = str(info.get("add") or "").strip()
    port = int(str(info.get("port") or 0))
    uuid = str(info.get("id") or "").strip()
    if not _valid_server(server) or not uuid or not 1 <= port <= 65535:
        return None
    node = {"name": clean_name(info.get("ps") or _fragment(link), f"vmess-{server}"),
            "type": "vmess", "server": server, "port": port, "uuid": uuid,
            "alterId": int(str(info.get("aid") or 0) or 0), "cipher": str(info.get("scy") or "auto"),
            "udp": True}
    network = str(info.get("net") or "").lower()
    if network in {"ws", "grpc", "h2"}:
        node["network"] = network
        host = str(info.get("host") or "")
        path = str(info.get("path") or "/")
        if network == "ws":
            node["ws-opts"] = {"path": path, "headers": {"Host": host} if host else {}}
        elif network == "grpc":
            node["grpc-opts"] = {"grpc-service-name": path.strip("/")}
        else:
            node["h2-opts"] = {"path": path, "host": [h for h in host.split(",") if h]}
    if str(info.get("tls") or "").lower() in {"tls", "reality"}:
        node["tls"] = True
        if info.get("sni") or info.get("host"):
            node["servername"] = str(info.get("sni") or info.get("host"))
    return node


def parse_trojan(link: str) -> dict | None:
    parts = urlsplit(link)
    password = unquote(parts.username or "")
    if not password or not parts.hostname or not parts.port:
        return None
    query = _query(link)
    node = {"name": clean_name(_fragment(link), f"trojan-{parts.hostname}"), "type": "trojan",
            "server": parts.hostname, "port": parts.port, "password": password, "udp": True}
    if query.get("sni") or query.get("peer"):
        node["sni"] = query.get("sni") or query.get("peer")
    if _truthy(query.get("allowInsecure")) or _truthy(query.get("insecure")):
        node["skip-cert-verify"] = True
    _apply_transport(node, query)
    return node


def parse_vless(link: str) -> dict | None:
    parts = urlsplit(link)
    uuid = unquote(parts.username or "")
    if not uuid or not parts.hostname or not parts.port:
        return None
    query = _query(link)
    node = {"name": clean_name(_fragment(link), f"vless-{parts.hostname}"), "type": "vless",
            "server": parts.hostname, "port": parts.port, "uuid": uuid, "udp": True}
    security = str(query.get("security") or "").lower()
    if security in {"tls", "reality"}:
        node["tls"] = True
        if query.get("sni") or query.get("host"):
            node["servername"] = query.get("sni") or query.get("host")
    if security == "reality":
        reality = {k: query[v] for k, v in (("public-key", "pbk"), ("short-id", "sid")) if query.get(v)}
        if query.get("fp"):
            node["client-fingerprint"] = query["fp"]
        if reality:
            node["reality-opts"] = reality
    if query.get("flow"):
        node["flow"] = query["flow"]
    if _truthy(query.get("allowInsecure")) or _truthy(query.get("insecure")):
        node["skip-cert-verify"] = True
    _apply_transport(node, query)
    return node


def parse_hysteria2(link: str) -> dict | None:
    parts = urlsplit(link)
    password = unquote(parts.username or "") or unquote(parts.password or "")
    if not password or not parts.hostname or not parts.port:
        return None
    query = _query(link)
    node = {"name": clean_name(_fragment(link), f"hy2-{parts.hostname}"), "type": "hysteria2",
            "server": parts.hostname, "port": parts.port, "password": password}
    if query.get("sni"):
        node["sni"] = query["sni"]
    if _truthy(query.get("insecure")) or _truthy(query.get("allowInsecure")):
        node["skip-cert-verify"] = True
    if query.get("obfs"):
        node["obfs"] = query["obfs"]
        if query.get("obfs-password"):
            node["obfs-password"] = query["obfs-password"]
    return node


def _apply_transport(node: dict, query: dict[str, str]) -> None:
    network = str(query.get("type") or "").lower()
    if network == "ws":
        node["network"] = "ws"
        node["ws-opts"] = {"path": query.get("path") or "/",
                           "headers": {"Host": query["host"]} if query.get("host") else {}}
    elif network == "grpc":
        node["network"] = "grpc"
        node["grpc-opts"] = {"grpc-service-name": (query.get("serviceName") or query.get("path") or "").strip("/")}
