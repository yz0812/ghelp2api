"""Read only the current GHelper token from Chrome/Edge extension storage."""
import argparse
import ctypes
import json
import os
import re
import struct
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_EXTENSION = "cieikaeocafmceoapfogpffaalkncpkc"
TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{32}")


class ExtractionError(Exception):
    pass


def crc_table():
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
        table.append(crc)
    return table


CRC_TABLE = crc_table()


def masked_crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    crc ^= 0xFFFFFFFF
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def validate_log(path: Path):
    data = path.read_bytes()
    fragmented = False
    for start in range(0, len(data), 32768):
        block = data[start:start + 32768]
        offset = 0
        while offset + 7 <= len(block):
            checksum, size, kind = struct.unpack_from("<IHB", block, offset)
            if not any(block[offset:offset + 7]):
                if any(block[offset:]):
                    raise ExtractionError("日志中存在无效填充")
                break
            end = offset + 7 + size
            if kind not in {1, 2, 3, 4} or end > len(block):
                raise ExtractionError("日志记录不完整或已损坏")
            if masked_crc32c(bytes([kind]) + block[offset + 7:end]) != checksum:
                raise ExtractionError("日志校验失败，未提取 token")
            if (kind in {1, 2} and fragmented) or (kind in {3, 4} and not fragmented):
                raise ExtractionError("日志分片不完整")
            fragmented = kind in {2, 3}
            offset = end
        if len(block) - offset < 7 and any(block[offset:]):
            raise ExtractionError("日志尾部不完整")
    if fragmented:
        raise ExtractionError("日志最后一条记录尚未写完")


def validate_table_block(block):
    if block.footer[0] not in {0, 1, 2}:
        raise ExtractionError("表文件压缩格式不受支持")
    if masked_crc32c(block.data + block.footer[:1]) != int.from_bytes(block.footer[1:], "little"):
        raise ExtractionError("表文件校验失败，未提取 token")


def shared_read(path: Path) -> bytes:
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ExtractionError("存储文件超过 128 MB 限制")
    if os.name != "nt":
        return path.read_bytes()
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "无法共享读取扩展存储文件")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, "rb") as file:
        return file.read(128 * 1024 * 1024 + 1)


def signature(folder: Path):
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()
            if p.is_file() and (p.name == "CURRENT" or p.name.startswith("MANIFEST-") or
                               p.suffix in {".log", ".ldb", ".sst"})}


def select_token(records) -> str | None:
    """Select by sequence, including deletion; never fall back to an older value."""
    latest = None
    for record in records:
        if record.key != b"token":
            continue
        if latest is None or record.sequence_number > latest.sequence_number:
            latest = record
    if latest is None:
        return None
    record_type = getattr(latest, "record_type", None)
    if record_type is None:
        raise ExtractionError("无法识别 token 记录类型")
    if int(record_type) == 0:  # LevelDB ValueType.kTypeDeletion
        return None
    if int(record_type) != 1:
        raise ExtractionError("token 记录类型不受支持")
    try:
        value = json.loads(latest.value.decode("utf-8"))
    except (UnicodeError, ValueError, AttributeError):
        raise ExtractionError("最新 token 记录不是可识别的 JSON") from None
    if not isinstance(value, str) or not TOKEN_PATTERN.fullmatch(value):
        raise ExtractionError("最新 token 格式不受支持，不使用历史值")
    return value


def read_snapshot_token(folder: Path) -> str | None:
    try:
        from dfindexeddb.leveldb import descriptor, ldb, log
    except ImportError:
        raise ExtractionError("缺少解析依赖，请先运行 pip install -r requirements-local.txt") from None
    current = (folder / "CURRENT").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"MANIFEST-\d+", current):
        raise ExtractionError("CURRENT 内容不正确")
    validate_log(folder / current)
    reader = descriptor.FileReader(str(folder / current))
    version = reader.GetLatestVersion()
    if not version:
        raise ExtractionError("无法读取当前 MANIFEST")
    tables = set()
    for entries in version.active_files.values():
        tables.update(entries.keys())
    files = []
    for name in sorted(tables):
        path = folder / name
        if not path.exists():
            path = path.with_suffix(".sst")
        if not path.exists():
            raise ExtractionError("快照缺少当前有效的表文件")
        files.append(path)
    logs = []
    if version.current_log:
        current_log = folder / version.current_log
        if not current_log.exists():
            raise ExtractionError("快照缺少当前日志文件")
        # Logs newer than the manifest may contain writes not flushed to its current log number yet.
        logs = [p for p in folder.glob("*.log")
                if p.stem.isdigit() and int(p.stem) >= int(current_log.stem)]
    # Preserve the previous WAL during recovery; LevelDB records it as tag 9.
    previous_log = 0
    for edit in reader.GetVersionEdits():
        previous = getattr(edit, "prev_log_number", None)
        if previous is not None:
            previous_log = previous
    if previous_log:
        previous_path = folder / f"{previous_log:06d}.log"
        if not previous_path.exists():
            raise ExtractionError("快照缺少恢复日志")
        if previous_path not in logs:
            logs.append(previous_path)

    def records():
        for path in files:
            reader = ldb.FileReader(str(path))
            validate_table_block(reader.index_block)
            for block in reader.GetBlocks():
                validate_table_block(block)
                yield from block.GetRecords()
        for path in logs:
            validate_log(path)
            yield from log.FileReader(str(path)).GetParsedInternalKeys()
    return select_token(records())


def extract_token(store: Path) -> str | None:
    for _ in range(3):
        try:
            before = signature(store)
            if "CURRENT" not in before:
                raise ExtractionError("扩展存储缺少 CURRENT，无法确认当前 token")
            with tempfile.TemporaryDirectory(prefix="ghelper-token-") as temp:
                target = Path(temp)
                for name in before:
                    (target / name).write_bytes(shared_read(store / name))
                if before != signature(store):
                    time.sleep(0.15)
                    continue
                token = read_snapshot_token(target)
                if before == signature(store):
                    return token
        except ExtractionError:
            raise
        except Exception:
            time.sleep(0.15)
    raise ExtractionError("存储持续变化、被占用或格式损坏；请稍后重试，必要时先关闭浏览器")


def discover(user_data: Path, profile: str, extension_id: str | None):
    if not user_data.is_dir():
        return
    profiles = [p for p in user_data.iterdir() if p.is_dir() and
                (p.name == "Default" or re.fullmatch(r"Profile \d+", p.name))] if profile == "all" else [user_data / profile]
    for directory in sorted(profiles):
        ids = {extension_id} if extension_id else {DEFAULT_EXTENSION}
        extensions = directory / "Extensions"
        if not extension_id and extensions.is_dir():
            for candidate in extensions.iterdir():
                if candidate.is_dir():
                    for manifest in candidate.glob("*/manifest.json"):
                        try:
                            info = json.loads(manifest.read_text(encoding="utf-8"))
                            if "ghelper" in (str(info.get("name", "")) + str(info.get("homepage_url", ""))).lower():
                                ids.add(candidate.name)
                        except (OSError, ValueError):
                            continue
        for ext_id in sorted(ids):
            store = directory / "Local Extension Settings" / ext_id
            if store.is_dir():
                versions = []
                for manifest in (extensions / ext_id).glob("*/manifest.json"):
                    try:
                        versions.append(str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "")))
                    except (OSError, ValueError):
                        pass
                version = max(versions, key=lambda v: tuple(int(n) for n in re.findall(r"\d+", v)), default="未知")
                yield directory.name, ext_id, version, store


def main():
    parser = argparse.ArgumentParser(description="只读提取本机 Chrome / Edge 的 GHelper token")
    parser.add_argument("--browser", choices=["all", "chrome", "edge"], default="all")
    parser.add_argument("--profile", default="all", help="Default、Profile 1 或 all")
    parser.add_argument("--user-data-dir", type=Path)
    parser.add_argument("--extension-id")
    parser.add_argument("--show-secrets", action="store_true")
    parser.add_argument("--output", type=Path, help="显式导出包含完整 token 的 JSON；拒绝覆盖已有文件")
    args = parser.parse_args()
    if args.profile != "all" and (Path(args.profile).name != args.profile or args.profile in {".", ".."}):
        parser.error("--profile 必须是单个配置目录名")
    if args.extension_id and not re.fullmatch(r"[a-p]{32}", args.extension_id):
        parser.error("--extension-id 格式不正确")
    if os.name != "nt" and not args.user_data_dir:
        parser.error("自动发现当前支持 Windows；其他系统可显式提供 --user-data-dir")
    if args.user_data_dir and args.browser == "all":
        parser.error("指定 --user-data-dir 时请同时指定 --browser chrome 或 edge")
    local = Path(os.environ.get("LOCALAPPDATA", "."))
    roots = {"chrome": local / "Google/Chrome/User Data", "edge": local / "Microsoft/Edge/User Data"}
    results, failed = [], False
    for browser, default in roots.items():
        if args.browser not in {"all", browser}:
            continue
        for profile, ext_id, version, store in discover(args.user_data_dir or default, args.profile, args.extension_id):
            try:
                token = extract_token(store)
                if token:
                    results.append({"name": f"{browser}-{profile}", "token": token,
                                    "browser": browser, "profile": profile, "extension_id": ext_id})
                    shown = token if args.show_secrets else token[:4] + "****" + token[-4:]
                    print(f"{browser} | {profile} | GHelper {version} | {shown}")
                else:
                    print(f"{browser} | {profile} | 当前没有 token（未登录或已清除）")
            except ExtractionError as exc:
                failed = True
                print(f"{browser} | {profile} | {exc}", file=sys.stderr)
    if args.output and results:
        try:
            with args.output.open("x", encoding="utf-8") as file:
                json.dump({"accounts": results}, file, ensure_ascii=False, indent=2)
            args.output.chmod(0o600)
            print(f"已导出 {len(results)} 条记录。该文件包含完整凭证，请通过管理台导入并妥善保管。")
        except FileExistsError:
            parser.exit(2, "输出文件已存在，未覆盖。\n")
    if not results:
        print("没有提取到当前有效的 token。请确认插件已登录和浏览器配置目录正确。")
    return 2 if failed else 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
