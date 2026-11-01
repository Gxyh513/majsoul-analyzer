"""Capacity-bounded, expiring on-disk cache for completed public paipu reviews.

Only API callers with a Mahjong Soul paipu link may use this cache; uploaded JSON
and demonstration inputs are never cached. Files contain already-public review
results and are kept separate from immutable raw protobuf records.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from .engine.model_policy import MODEL_SHA256, REVIEW_SCHEMA, valid_review_source


CACHE_TTL_SECONDS = 24 * 3600
CACHE_BUDGET_BYTES = 256 * 1024 * 1024
CACHE_ENTRY_LIMIT_BYTES = 20 * 1024 * 1024
_KEY = re.compile(r"[0-9a-f]{64}")
_cache_lock = threading.RLock()


def _cache_path(directory: Path, key: str) -> Path:
    if not _KEY.fullmatch(key):
        raise ValueError("invalid cache key")
    return Path(directory) / f"{key}.json"


def load(directory: Path, key: str, game_id: str, seat: int):
    """Return a verified, unexpired review, never following symbolic links."""
    path = _cache_path(directory, key)
    try:
        if path.is_symlink():
            return None
        stat = path.stat()
        if stat.st_size > CACHE_ENTRY_LIMIT_BYTES or stat.st_size <= 0:
            return None
        if time.time() - stat.st_mtime > CACHE_TTL_SECONDS:
            return None
        with path.open("rb") as reader:
            data = reader.read(CACHE_ENTRY_LIMIT_BYTES + 1)
        if len(data) > CACHE_ENTRY_LIMIT_BYTES:
            return None
        envelope = json.loads(data)
        result = envelope.get("result") if isinstance(envelope, dict) and envelope.get("version") == 1 else None
        if (not valid_review_source(result, MODEL_SHA256)
                or result.get("game_id") != game_id
                or type(result.get("target_actor")) is not int
                or result["target_actor"] != seat):
            return None
        return result
    except (OSError, ValueError, UnicodeError, TypeError, AttributeError):
        return None


def save(directory: Path, key: str, result: dict) -> bool:
    """Atomically save one validated result; evict oldest entries to cap usage."""
    path = _cache_path(directory, key)
    if not valid_review_source(result, MODEL_SHA256):
        return False
    payload = json.dumps({"version": 1, "result": result}, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    if len(payload) > CACHE_ENTRY_LIMIT_BYTES:
        return False
    with _cache_lock:
        temporary = None
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                return False
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".review-", suffix=".tmp", delete=False) as writer:
                temporary = Path(writer.name)
                writer.write(payload)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, path)
            temporary = None
            _evict(directory)
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _evict(directory: Path):
    now = time.time()
    files = []
    total = 0
    for path in directory.glob("[0-9a-f]*.json"):
        if not _KEY.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            stat = path.stat()
            if stat.st_mtime < now - CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                continue
            files.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        except OSError:
            continue
    for _mtime, size, path in sorted(files):
        if total <= CACHE_BUDGET_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            pass


def storage_metrics(directory: Path) -> dict:
    """No filenames, paths, UUIDs or user data are ever returned."""
    directory = Path(directory)
    try:
        entries = [p.stat().st_size for p in directory.glob("[0-9a-f]*.json")
                   if _KEY.fullmatch(p.stem) and not p.is_symlink()]
        capacity = shutil.disk_usage(directory if directory.is_dir() else directory.parent)
        return {"entries": len(entries), "bytes": sum(entries),
                "budget_bytes": CACHE_BUDGET_BYTES, "free_bytes": capacity.free,
                "disk_total_bytes": capacity.total}
    except OSError:
        return {"entries": 0, "bytes": 0, "budget_bytes": CACHE_BUDGET_BYTES,
                "free_bytes": None, "disk_total_bytes": None}
