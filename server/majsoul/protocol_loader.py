"""Pinned Mahjong Soul protobuf schema verification and process-local cache."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import threading
from pathlib import Path

from ..paipu_performance import measure
from .proto import LiqiProto
from .errors import MajsoulError

LOCAL_PROTOCOL_VERSION = "0.11.252.w"
LOCAL_PROTOCOL_SHA256 = "3d9191ef064daeed099feb482501e2d24e7c1da80a738e9b671b4618f178fed2"
BUNDLED_PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
_PROTOCOL_LOCK = threading.Lock()
_PROTOCOL_CACHE: dict[str, LiqiProto] = {}


@lru_cache(maxsize=8)
def _load_local_protocol(path: Path, file_state: tuple[int, int, int]) -> LiqiProto:
    """Read and build the pinned schema once per unchanged file in this process."""
    try:
        with measure("Protobuf协议本地缓存读取耗时"):
            raw = path.read_bytes()
    except OSError as exc:
        raise MajsoulError(f"无法读取本地雀魂协议文件 {path}，请恢复该文件。") from exc
    try:
        with measure("Protobuf JSON解析及协议初始化耗时"):
            liqi = json.loads(raw)
        normalized = json.dumps(liqi, sort_keys=True, separators=(",", ":")).encode("utf8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise MajsoulError(f"本地雀魂协议文件 {path} 无法解析，请恢复原文件。") from exc
    if hashlib.sha256(normalized).hexdigest() != LOCAL_PROTOCOL_SHA256:
        raise MajsoulError(f"本地雀魂协议文件 {path} 完整性校验失败，请恢复原文件。")
    proto = _PROTOCOL_CACHE.get(LOCAL_PROTOCOL_SHA256)
    if proto is not None:
        return proto
    try:
        with measure("Protobuf JSON解析及协议初始化耗时"):
            # The bundled schema predates the Unity WebGL device UUID field.
            fields = liqi["nested"]["lq"]["nested"]["ClientDeviceInfo"]["fields"]
            fields.setdefault("uuid", {"type": "string", "id": 14})
            proto = LiqiProto(liqi)
    except (ValueError, KeyError, TypeError) as exc:
        raise MajsoulError(f"本地雀魂协议文件 {path} 无法解析，请恢复原文件。") from exc
    _PROTOCOL_CACHE[LOCAL_PROTOCOL_SHA256] = proto
    return proto


def _local_protocol(cache_dir: Path) -> LiqiProto:
    filename = f"liqi-{LOCAL_PROTOCOL_VERSION}.json"
    # lru_cache alone can construct the same entry twice under concurrent calls.
    with _PROTOCOL_LOCK:
        for path in (cache_dir / filename, BUNDLED_PROTOCOL_DIR / filename):
            try:
                path = path.resolve()
                stat = path.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise MajsoulError(f"无法读取本地雀魂协议文件 {path}，请恢复该文件。") from exc
            return _load_local_protocol(path, (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    raise MajsoulError(f"找不到本地雀魂协议文件 {filename}，请恢复随项目提供的协议文件。")


