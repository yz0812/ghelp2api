import json
import struct
from types import SimpleNamespace

import pytest

from scripts.extract_browser_tokens import (ExtractionError, discover, extract_token,
                                           masked_crc32c, read_snapshot_token, select_token)


def record(seq, value, deleted=False):
    return SimpleNamespace(key=b"token", sequence_number=seq, record_type=0 if deleted else 1,
                           value=json.dumps(value).encode())


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def log_block(data):
    return struct.pack("<IHB", masked_crc32c(b"\x01" + data), len(data), 1) + data


def create_log_store(path, values):
    path.mkdir(parents=True)
    (path / "CURRENT").write_text("MANIFEST-000001\n", encoding="ascii")
    # VersionEdit: log_number=2, last_sequence=number of writes.
    (path / "MANIFEST-000001").write_bytes(log_block(varint(2) + varint(2) + varint(4) + varint(len(values))))
    batches = []
    for sequence, value in enumerate(values, 1):
        kv = bytes([0 if value is None else 1]) + varint(5) + b"token"
        if value is not None:
            data = json.dumps(value).encode()
            kv += varint(len(data)) + data
        batches.append(log_block(struct.pack("<QI", sequence, 1) + kv))
    (path / "000002.log").write_bytes(b"".join(batches))


def test_latest_sequence_and_deletion():
    assert select_token([record(3, "c" * 32), record(1, "a" * 32), record(2, "b" * 32)]) == "c" * 32
    assert select_token([record(1, "a" * 32), record(2, None, deleted=True)]) is None
    with pytest.raises(ExtractionError):
        select_token([record(1, "a" * 32), record(2, "bad-value")])


def test_real_log_decoder_and_shared_copy(tmp_path):
    store = tmp_path / "store"
    create_log_store(store, ["a" * 32, "b" * 32])
    assert read_snapshot_token(store) == "b" * 32
    assert extract_token(store) == "b" * 32


def test_real_deleted_log(tmp_path):
    store = tmp_path / "store"
    create_log_store(store, ["a" * 32, None])
    assert extract_token(store) is None


def test_discover_profiles_and_custom_id(tmp_path):
    ext = "b" * 32
    for profile in ["Default", "Profile 1"]:
        directory = tmp_path / profile
        manifest = directory / "Extensions" / ext / "2.9.3" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "GHelper", "version": "2.9.3"}))
        (directory / "Local Extension Settings" / ext).mkdir(parents=True)
    found = list(discover(tmp_path, "all", None))
    assert len(found) == 2
    assert all(row[1] == ext for row in found)
    assert len(list(discover(tmp_path, "Default", ext))) == 1


def test_corrupt_snapshot_fails_closed(tmp_path):
    (tmp_path / "CURRENT").write_text("../../somewhere")
    with pytest.raises(ExtractionError):
        extract_token(tmp_path)


def table_block(data, compression=0):
    if compression == 1:
        import snappy
        data = snappy.compress(data)
    return data + bytes([compression]) + struct.pack("<I", masked_crc32c(data + bytes([compression])))


def entry_block(key, value):
    return (varint(0) + varint(len(key)) + varint(len(value)) + key + value + struct.pack("<II", 0, 1))


def create_table(path, token, sequence):
    from dfindexeddb.leveldb.definitions import TABLE_MAGIC
    key = b"token" + struct.pack("<Q", (sequence << 8) | 1)
    data = table_block(entry_block(key, json.dumps(token).encode()), compression=1)
    meta = table_block(struct.pack("<II", 0, 1))
    index = table_block(entry_block(key, varint(0) + varint(len(data) - 5)))
    handles = (varint(len(data)) + varint(len(meta) - 5) +
               varint(len(data) + len(meta)) + varint(len(index) - 5))
    payload = data + meta + index + handles.ljust(40, b"\x00") + TABLE_MAGIC
    path.write_bytes(payload)
    return key, len(payload)


def test_compressed_tables_multiple_l0_and_sst(tmp_path):
    create_log_store(tmp_path / "db", [])
    store = tmp_path / "db"
    edits = varint(2) + varint(2) + varint(4) + varint(10)
    for number, sequence, value in [(3, 2, "b" * 32), (4, 10, "c" * 32)]:
        key, size = create_table(store / f"{number:06d}.ldb", value, sequence)
        edits += varint(7) + varint(0) + varint(number) + varint(size)
        edits += varint(len(key)) + key + varint(len(key)) + key
    (store / "MANIFEST-000001").write_bytes(log_block(edits))
    (store / "000004.ldb").rename(store / "000004.sst")
    assert extract_token(store) == "c" * 32


def test_corrupt_log_checksum_rejected(tmp_path):
    store = tmp_path / "db"
    create_log_store(store, ["a" * 32])
    data = bytearray((store / "000002.log").read_bytes())
    data[-2] = ord("b")
    (store / "000002.log").write_bytes(data)
    with pytest.raises(ExtractionError, match="校验失败"):
        extract_token(store)


def test_unstable_shared_snapshot_retries(tmp_path, monkeypatch):
    from scripts import extract_browser_tokens as extractor
    store = tmp_path / "db"
    create_log_store(store, ["a" * 32])
    count = 0
    original = extractor.signature
    def changing(folder):
        nonlocal count
        result = original(folder)
        count += 1
        key = next(iter(result))
        size, modified = result[key]
        result[key] = (size, modified + count)
        return result
    monkeypatch.setattr(extractor, "signature", changing)
    with pytest.raises(ExtractionError):
        extract_token(store)


def test_crc32c_known_vector():
    crc = 0xE3069283  # CRC32C('123456789'), before the LevelDB mask.
    assert masked_crc32c(b"123456789") == (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF
