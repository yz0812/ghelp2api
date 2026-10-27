"""Download a pinned official core and validate synthetic generated configurations."""
import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.clash import render  # noqa: E402

VERSION = "v1.19.33"
CHECKSUMS = {
    "windows": "6ab2180875f9f6fff8b19386d760daec257f66b45f72b3afc650634272fa837a",
    "linux": "132f6f3edf9a65e62f04853aed3358472e0b2b7666daa8419edfa2ddf5bb6323",
}


def synthetic_config() -> str:
    """合成一份覆盖两类来源的配置，供 mihomo 校验；同时把脚本和 render() 的接口绑在一起。"""
    # GHelper 账号节点，以及订阅源里常见的带插件/传输层配置的节点
    proxies = [
        {"name": "合成测试账号-🇭🇰 香港-HKT-0000000000", "type": "http",
         "server": "synthetic.example.com", "port": 443, "tls": True,
         "skip-cert-verify": True, "udp": False},
        {"name": "机场-ss-obfs", "type": "ss", "server": "ss.example.com", "port": 8388,
         "cipher": "aes-128-gcm", "password": "synthetic", "udp": True,
         "plugin": "obfs", "plugin-opts": {"mode": "http", "host": "bing.com"}},
        {"name": "机场-trojan-ws", "type": "trojan", "server": "trojan.example.com", "port": 443,
         "password": "synthetic", "udp": True, "network": "ws",
         "ws-opts": {"path": "/ws", "headers": {"Host": "cdn.example.com"}}},
    ]
    groups = {"账号 · 合成测试账号": [proxies[0]["name"]],
              "来源 · 机场": [proxies[1]["name"], proxies[2]["name"]]}
    return render(proxies, groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    directory = (Path(__file__).resolve().parent.parent / ".cache/mihomo").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    binary = args.binary
    if args.download:
        system = platform.system().lower()
        if system not in CHECKSUMS or platform.machine().lower() not in {"amd64", "x86_64"}:
            parser.error("自动下载支持 Windows/Linux amd64，其他架构请提供 --binary")
        binary = directory / ("mihomo.exe" if system == "windows" else "mihomo")
        if not binary.exists():
            suffix = "zip" if system == "windows" else "gz"
            url = f"https://github.com/MetaCubeX/mihomo/releases/download/{VERSION}/mihomo-{system}-amd64-{VERSION}.{suffix}"
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != CHECKSUMS[system]:
                raise RuntimeError("mihomo 下载校验失败")
            if system == "windows":
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    entries = [n for n in archive.namelist() if n.endswith(".exe")]
                    if len(entries) != 1:
                        raise RuntimeError("mihomo 压缩包格式异常")
                    binary.write_bytes(archive.read(entries[0]))
            else:
                binary.write_bytes(gzip.decompress(data))
                binary.chmod(0o755)
    if not binary:
        parser.error("请使用 --download 或 --binary")
    config = directory / "validation.yaml"
    config.write_text(synthetic_config(), encoding="utf-8")
    subprocess.run([str(binary.resolve()), "-t", "-d", str(directory), "-f", str(config)],
                   check=True, timeout=120, env=os.environ.copy())
    print(f"mihomo {VERSION}: generated Clash configuration validated")


if __name__ == "__main__":
    main()
