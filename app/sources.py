"""外部订阅源的拉取；地址来自用户输入，必须防 SSRF。"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpcore
import httpx

# 与解析器的 MAX_CHARS 对齐：按字节计数，UTF-8 解码后的字符数不会更多
MAX_BYTES = 10_000_000
TIMEOUT = 10
# 单次读写超时挡不住“慢慢吐数据”的服务器，整个拉取另设总时限
TOTAL_TIMEOUT = 30


class SourceError(Exception):
    pass


def validate_url(raw: str) -> str:
    url = (raw or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise SourceError("只支持 http/https 订阅地址")
    if len(url) > 2000:
        raise SourceError("订阅地址过长")
    return url


def redact(url: str) -> str:
    """列表展示用：只保留 scheme 和主机，隐藏可能含 token 的路径与查询。"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else "已隐藏"


async def ensure_public(host: str) -> str:
    """解析到的所有地址都必须是公网地址，否则拒绝；返回第一个地址供直连。"""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None), 5)
    except (socket.gaierror, TimeoutError, UnicodeError):
        raise SourceError("订阅域名无法解析") from None
    if not infos:
        raise SourceError("订阅域名无法解析")
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise SourceError("订阅地址无法识别") from None
        if not address.is_global:
            raise SourceError("订阅地址指向内网或保留地址，已拒绝")
    return infos[0][4][0]


class PublicBackend(httpcore.AsyncNetworkBackend):
    """建连时现场解析并校验，直接连校验过的 IP，堵住 DNS rebinding。

    TLS 的 SNI 与 Host 头仍取自原始域名，证书校验不受影响。
    """

    def __init__(self):
        self.inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address = await ensure_public(host)
        return await self.inner.connect_tcp(address, port, timeout, local_address, socket_options)

    async def sleep(self, seconds):
        await self.inner.sleep(seconds)


def public_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    # httpx 没有公开注入 network backend 的参数，只能换掉底层连接池
    transport._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(),
                                                   network_backend=PublicBackend())
    return transport


class Fetcher:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False,
                                        transport=public_transport(), headers={"user-agent": "clash.meta"})

    async def close(self):
        await self.client.aclose()

    async def fetch(self, url: str) -> str:
        await ensure_public(urlsplit(validate_url(url)).hostname)
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT), self.client.stream("GET", url) as response:
                if response.is_redirect:
                    raise SourceError("订阅地址返回重定向，请填写最终地址")
                if response.status_code != 200:
                    raise SourceError(f"订阅地址返回 HTTP {response.status_code}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise SourceError("订阅内容超过大小限制")
        except SourceError:
            raise
        except TimeoutError:
            raise SourceError("拉取订阅超时") from None
        except httpx.HTTPError:
            raise SourceError("无法连接订阅地址") from None
        try:
            return bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(data).decode("latin-1")
