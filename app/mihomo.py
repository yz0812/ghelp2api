"""用 mihomo 内核给节点做真实测速。

TCP 端口能连上不等于代理可用：机场节点常挂在同一批 CDN 入口后面，端口永远是开的，
但代理握手照样失败。这里起一个只做 URL 测试的 mihomo 进程，用它的 /proxies/{name}/delay
接口按节点自身配置真正走一遍代理。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import time
import urllib.parse
from pathlib import Path

import yaml

TEST_URL = "https://www.gstatic.com/generate_204"
# 单个节点的测速上限；超时即认定节点不可用（与 mihomo 的 delay 接口语义一致）
DELAY_TIMEOUT = 5000
# 一次进程内最多载入的节点数，避免上千个候选拖慢内核启动
PROBE_BATCH = 600
# 单个进程同时进行的测速数；真实并发还受 Service.proxy_limit 约束
PROBE_WORKERS = 32
# 预算剩余不足这个值时不再发起新的测速
PROBE_MIN_SLICE = 1.5


class MihomoUnavailable(RuntimeError):
    """内核启动失败（二进制缺失、配置被拒、端口被占），调用方应回退到端口探测。"""


def binary_path() -> str | None:
    """mihomo 可执行文件：MIHOMO_BIN → PATH → 仓库内 .cache/mihomo（开发自测用）。

    MIHOMO_BIN 一旦设置就以它为准（指向不存在的文件即等于关闭内核测速），方便测试与部署。
    """
    configured = os.environ.get("MIHOMO_BIN")
    if configured:
        return configured if Path(configured).is_file() else None
    if found := shutil.which("mihomo"):
        return found
    root = Path(__file__).resolve().parent.parent / ".cache" / "mihomo"
    for name in ("mihomo", "mihomo.exe"):
        if (root / name).is_file():
            return str(root / name)
    return None


def available() -> bool:
    return binary_path() is not None


class MihomoTester:
    """启动一个临时 mihomo，用 delay 接口批量测速后关掉。

    delays() 的返回值与入参等长：True/False 表示测出了可用或不可用，None 表示本轮没轮到。
    """

    def __init__(self, home, binary=None, workers=PROBE_WORKERS, timeout=DELAY_TIMEOUT,
                 url=TEST_URL, batch=PROBE_BATCH, limit=None):
        self.home = Path(home)
        self.binary = binary or binary_path()
        self.workers, self.timeout, self.url, self.batch = workers, timeout, url, batch
        self.limit = limit

    async def delays(self, proxies, deadline, on_done=None):
        results: list[tuple[bool, float | None] | None] = [None] * len(proxies)
        if not self.binary or not proxies:
            return results
        batch = proxies[: self.batch]
        self.home.mkdir(parents=True, exist_ok=True)
        process = None
        try:
            port = free_port()
            config = self.home / f"probe-{port}.yaml"
            config.write_text(yaml.safe_dump({
                "mixed-port": 0, "allow-lan": False, "mode": "rule", "log-level": "silent",
                "ipv6": False, "external-controller": f"127.0.0.1:{port}",
                # 名字换成一维编号：不同来源的节点可能重名，内核要求唯一
                "proxies": [dict(proxy, name=f"n{index}") for index, proxy in enumerate(batch)],
                "proxy-groups": [], "rules": ["MATCH,DIRECT"],
            }, allow_unicode=True), encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                self.binary, "-d", str(self.home), "-f", str(config),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await self._ready(port, process, deadline)
            await self._test(port, len(batch), deadline, results, on_done)
            return results
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                with contextlib.suppress(ProcessLookupError, TimeoutError):
                    await asyncio.wait_for(process.wait(), 5)
            if "config" in locals():
                with contextlib.suppress(OSError):
                    config.unlink()

    async def _ready(self, port, process, deadline):
        """等内核的 REST 接口就绪；起不来或超时就抛 MihomoUnavailable 让调用方回退。"""
        started = time.time()
        while time.time() < min(deadline, started + 15):
            if process.returncode is not None:
                raise MihomoUnavailable("mihomo 启动失败")
            try:
                status, _ = await self._get(port, "/version", 2)
                if status == 200:
                    return
            except (OSError, TimeoutError, ValueError):
                pass
            await asyncio.sleep(0.1)
        raise MihomoUnavailable("mihomo 未就绪")

    async def _test(self, port, count, deadline, results, on_done):
        queue: asyncio.Queue[int] = asyncio.Queue()
        for index in range(count):
            queue.put_nowait(index)

        async def worker():
            while time.time() < deadline - PROBE_MIN_SLICE:
                try:
                    index = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    if self.limit is not None:
                        async with self.limit:
                            results[index] = await self._delay(port, index, deadline)
                    else:
                        results[index] = await self._delay(port, index, deadline)
                finally:
                    if on_done:
                        on_done()

        workers = min(self.workers, count) or 1
        await asyncio.gather(*(worker() for _ in range(workers)), return_exceptions=True)

    async def _delay(self, port, index, deadline):
        remaining = deadline - time.time()
        timeout = max(0.5, min(self.timeout / 1000, remaining))
        path = (f"/proxies/n{index}/delay?timeout={int(timeout * 1000)}"
                f"&url={urllib.parse.quote(self.url, safe='')}")
        try:
            status, body = await self._get(port, path, timeout + 0.5)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
            return self._unfinished(deadline)
        if status == 200:
            delay = json.loads(body or b"{}").get("delay")
            return (True, float(delay)) if isinstance(delay, (int, float)) and delay > 0 else self._unfinished(deadline)
        if status in (400, 503, 504):
            # 400 配置被内核拒绝、504 超时、503 其它连接错误：客户端同样用不了
            return False, None
        return self._unfinished(deadline)

    @staticmethod
    def _unfinished(deadline):
        """被预算截断的请求不算测过：留到下一轮，不要记成“超时”。"""
        return None if time.time() >= deadline - PROBE_MIN_SLICE else ("unfinished", None)

    @staticmethod
    async def _get(port, path, timeout):
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout)
        try:
            writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
            await asyncio.wait_for(writer.drain(), timeout)
            raw = await asyncio.wait_for(reader.read(), timeout)
        finally:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)
        head, _, body = raw.partition(b"\r\n\r\n")
        return int(head.split(b" ", 3)[1]), body


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
