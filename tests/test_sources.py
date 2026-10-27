import asyncio

import httpx
import pytest

from app.sources import Fetcher, SourceError, ensure_public, redact, validate_url


def test_only_http_urls():
    for url in ["ftp://x.example.com/a", "file:///etc/passwd", "javascript:alert(1)", "", "   "]:
        with pytest.raises(SourceError):
            validate_url(url)
    assert validate_url(" https://sub.example.com/a ") == "https://sub.example.com/a"


def test_redact_hides_path_and_query():
    assert redact("https://sub.example.com:8443/api?token=secret") == "https://sub.example.com"


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "localhost"])
@pytest.mark.asyncio
async def test_private_targets_rejected(host):
    with pytest.raises(SourceError):
        await ensure_public(host)


@pytest.fixture
def offline(monkeypatch):
    async def noop(host):
        return None

    monkeypatch.setattr("app.sources.ensure_public", noop)


@pytest.mark.asyncio
async def test_redirect_is_rejected(offline):
    fetcher = Fetcher()
    await fetcher.client.aclose()
    fetcher.client = httpx.AsyncClient(follow_redirects=False, transport=httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "https://evil.example.com"})))
    with pytest.raises(SourceError, match="重定向"):
        await fetcher.fetch("https://sub.example.com/x")
    await fetcher.close()


@pytest.mark.asyncio
async def test_error_status_and_size_limit(offline, monkeypatch):
    fetcher = Fetcher()
    await fetcher.client.aclose()
    fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(SourceError, match="HTTP 404"):
        await fetcher.fetch("https://sub.example.com/x")
    await fetcher.close()

    monkeypatch.setattr("app.sources.MAX_BYTES", 8)
    fetcher = Fetcher()
    await fetcher.client.aclose()
    fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 64)))
    with pytest.raises(SourceError, match="大小限制"):
        await fetcher.fetch("https://sub.example.com/x")
    await fetcher.close()


@pytest.mark.asyncio
async def test_successful_fetch_decodes_utf8(offline):
    fetcher = Fetcher()
    await fetcher.client.aclose()
    fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content="ss://abc@h.example.com:1#香港节点".encode())))
    assert "香港节点" in await fetcher.fetch("https://sub.example.com/x")
    await fetcher.close()


@pytest.mark.asyncio
async def test_connect_time_check_blocks_rebinding(monkeypatch):
    """首次解析是公网，连接时被解析成内网：必须在建连时拒绝。"""
    import socket
    from app.sources import PublicBackend

    async def rebinding(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebinding)
    with pytest.raises(SourceError):
        await PublicBackend().connect_tcp("sub.example.com", 443)


@pytest.mark.asyncio
async def test_total_timeout_for_slow_body(offline, monkeypatch):
    monkeypatch.setattr("app.sources.TOTAL_TIMEOUT", 0.2)

    async def trickle():
        while True:
            yield b"x"
            await asyncio.sleep(0.05)

    fetcher = Fetcher()
    await fetcher.client.aclose()
    fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=trickle())))
    with pytest.raises(SourceError, match="超时"):
        await fetcher.fetch("https://sub.example.com/x")
    await fetcher.close()
