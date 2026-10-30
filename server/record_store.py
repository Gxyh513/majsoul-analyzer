"""Permanent Mahjong Soul source records, keyed only by game UUID."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import weakref
import zlib
from typing import Awaitable, Callable


_MAGIC = b"MSRAW\x01"
_HEADER = struct.Struct(">HQQI")
_UUID = re.compile(r"\d{6}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_locks: weakref.WeakValueDictionary[tuple[asyncio.AbstractEventLoop, str, str], asyncio.Lock] = weakref.WeakValueDictionary()
_flights: dict[tuple[asyncio.AbstractEventLoop, str, str], asyncio.Task] = {}


@dataclass(frozen=True)
class RawRecord:
    protocol_version: str
    response: bytes
    detail: bytes = b""


class RecordStorageError(Exception):
    pass


class CorruptRecordError(RecordStorageError):
    pass


def _record_uuid(game_uuid: str) -> None:
    if not isinstance(game_uuid, str) or not _UUID.fullmatch(game_uuid):
        raise ValueError("invalid game UUID")


def _encode(record: RawRecord) -> bytes:
    if not _VERSION.fullmatch(record.protocol_version) or not record.response:
        raise ValueError("invalid raw record")
    version = record.protocol_version.encode("ascii")
    checksum = zlib.crc32(record.detail, zlib.crc32(record.response, zlib.crc32(version)))
    return _MAGIC + _HEADER.pack(len(version), len(record.response), len(record.detail), checksum) + version + record.response + record.detail


def _decode(data: bytes) -> RawRecord:
    try:
        if not data.startswith(_MAGIC):
            raise ValueError("invalid record header")
        offset = len(_MAGIC)
        version_len, response_len, detail_len, checksum = _HEADER.unpack_from(data, offset)
        offset += _HEADER.size
        version = data[offset:offset + version_len].decode("ascii")
        start = offset + version_len
        response = data[start:start + response_len]
        detail = data[start + response_len:]
        if (not _VERSION.fullmatch(version) or len(response) != response_len or not response
                or len(detail) != detail_len
                or zlib.crc32(detail, zlib.crc32(response, zlib.crc32(version.encode("ascii")))) != checksum):
            raise ValueError("invalid record contents")
        return RawRecord(version, response, detail)
    except (ValueError, UnicodeError, struct.error) as exc:
        raise CorruptRecordError("原始牌谱文件损坏，请从备份恢复；系统不会自动重新下载。") from exc


def read_record(records_dir: Path, game_uuid: str, legacy_dirs: tuple[Path, ...] = ()) -> RawRecord | dict | None:
    _record_uuid(game_uuid)
    for directory in (Path(records_dir), *map(Path, legacy_dirs)):
        for suffix in (".pb", ".json"):
            path = directory / f"{game_uuid}{suffix}"
            try:
                if path.is_symlink():
                    raise CorruptRecordError("原始牌谱文件不能是符号链接。")
                data = path.read_bytes()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RecordStorageError("无法读取原始牌谱，请检查存储目录。") from exc
            if suffix == ".pb":
                return _decode(data)
            try:
                record = json.loads(data)
                if (not isinstance(record, dict) or not isinstance(record.get("head"), dict)
                        or record["head"].get("uuid") != game_uuid or not isinstance(record.get("actions"), list)):
                    raise ValueError("invalid legacy record")
                return record
            except (ValueError, UnicodeError) as exc:
                raise CorruptRecordError("原始牌谱文件损坏，请从备份恢复；系统不会自动重新下载。") from exc
    return None


def save_record(records_dir: Path, game_uuid: str, record: RawRecord) -> None:
    _record_uuid(game_uuid)
    data = _encode(record)
    records_dir = Path(records_dir)
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{game_uuid}.pb"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=records_dir, prefix=".record-", suffix=".tmp", delete=False) as file:
            temp_path = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        legacy = records_dir / f"{game_uuid}.json"
        if path.exists() or path.is_symlink() or legacy.exists() or legacy.is_symlink():
            raise RecordStorageError("原始牌谱已存在，拒绝覆盖。")
        os.replace(temp_path, path)
        if os.name != "nt":
            directory_fd = os.open(records_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@asynccontextmanager
async def _process_lock(records_dir: Path, game_uuid: str):
    lock_dir = Path(records_dir) / ".locks"
    if lock_dir.is_symlink():
        raise RecordStorageError("原始牌谱锁目录不能是符号链接。")
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{game_uuid}.lock"
    if path.is_symlink():
        raise RecordStorageError("原始牌谱锁文件不能是符号链接。")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o666), "a+b") as file:
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        while True:
            try:
                file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(exc, "winerror", None) not in (32, 33, 36):
                    raise
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


async def get_or_download_record(
    game_uuid: str,
    records_dir: Path,
    download: Callable[[str], Awaitable[RawRecord]],
    *,
    legacy_dirs: tuple[Path, ...] = (),
) -> RawRecord | dict:
    _record_uuid(game_uuid)
    existing = await asyncio.to_thread(read_record, records_dir, game_uuid, legacy_dirs)
    if existing is not None:
        return existing
    key = (asyncio.get_running_loop(), str(Path(records_dir).resolve()), game_uuid)
    flight = _flights.get(key)
    if flight is None:
        async def download_once():
            lock = _locks.setdefault(key, asyncio.Lock())
            async with lock:
                existing = await asyncio.to_thread(read_record, records_dir, game_uuid, legacy_dirs)
                if existing is not None:
                    return existing
                async with _process_lock(records_dir, game_uuid):
                    existing = await asyncio.to_thread(read_record, records_dir, game_uuid, legacy_dirs)
                    if existing is not None:
                        return existing
                    record = await download(game_uuid)
                    if not isinstance(record, RawRecord):
                        raise RecordStorageError("雀魂未返回原始 Protobuf 牌谱。")
                    try:
                        save = asyncio.create_task(asyncio.to_thread(save_record, records_dir, game_uuid, record))
                        try:
                            await asyncio.shield(save)
                        except asyncio.CancelledError:
                            await save
                            raise
                    except (OSError, ValueError, RecordStorageError) as exc:
                        raise RecordStorageError("牌谱下载成功，但原始牌谱保存失败。") from exc
                    return record

        flight = asyncio.create_task(download_once())
        _flights[key] = flight

        def discard(done: asyncio.Task) -> None:
            if _flights.get(key) is done:
                _flights.pop(key, None)
            if not done.cancelled():
                done.exception()

        flight.add_done_callback(discard)
    return await asyncio.shield(flight)


async def drain_record_downloads() -> None:
    loop = asyncio.get_running_loop()
    while pending := [task for key, task in _flights.items() if key[0] is loop]:
        await asyncio.gather(*(asyncio.shield(task) for task in pending), return_exceptions=True)
