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
import sys
import tempfile
import threading
import time

from .engine.model_policy import MODEL_SHA256, REVIEW_SCHEMA, valid_review_source


CACHE_FORMAT_VERSION = 2
CACHE_TTL_SECONDS = 24 * 3600
CACHE_BUDGET_BYTES = 256 * 1024 * 1024  # Hard safety ceiling.
CACHE_SOFT_BUDGET_BYTES = int(CACHE_BUDGET_BYTES * 0.85)
CACHE_ENTRY_LIMIT_BYTES = 20 * 1024 * 1024
LOW_DISK_WARNING_BYTES = 512 * 1024 * 1024
# Lazy housekeeping: no permanent scheduler and no work while the service is idle.
CACHE_HOUSEKEEPING_SECONDS = 60 * 60
_KEY = re.compile(r"[0-9a-f]{64}")
_cache_lock = threading.RLock()
_last_cleanup: dict[Path, float] = {}


def _cache_path(directory: Path, key: str) -> Path:
    if not _KEY.fullmatch(key):
        raise ValueError("invalid cache key")
    return Path(directory) / f"{key}.json"


def load(directory: Path, key: str, game_id: str, seat: int):
    """Return a verified, unexpired review, never following symbolic links."""
    path = _cache_path(directory, key)
    _maybe_cleanup(directory)
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
        # The stored fingerprint must match the requested key. Otherwise a
        # misplaced cache file for a different temperature or source could be
        # accepted just because its game UUID and seat happen to match.
        result = (envelope.get("result") if isinstance(envelope, dict)
                  and envelope.get("version") == CACHE_FORMAT_VERSION
                  and envelope.get("key") == key else None)
        if (not valid_review_source(result, MODEL_SHA256)
                or result.get("game_id") != game_id
                or type(result.get("target_actor")) is not int
                or result["target_actor"] != seat):
            return None
        return result
    except (OSError, ValueError, UnicodeError, TypeError, AttributeError):
        return None


def save(directory: Path, key: str, result: dict) -> bool:
    """Atomically save one validated result while preserving cache headroom."""
    path = _cache_path(directory, key)
    if not valid_review_source(result, MODEL_SHA256):
        return False
    payload = json.dumps({"version": CACHE_FORMAT_VERSION, "key": key, "result": result},
                         ensure_ascii=False, separators=(",", ":")).encode("utf8")
    if len(payload) > CACHE_ENTRY_LIMIT_BYTES:
        return False
    with _cache_lock:
        temporary = None
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Trim before writing so the largest allowed new entry still fits
            # inside the 256 MiB hard ceiling (85% soft target + 20 MiB entry).
            _evict(directory)
            # Results are disposable; preserve free disk for permanent raw
            # records and SQLite rather than filling the volume with reviews.
            if shutil.disk_usage(directory).free - len(payload) < LOW_DISK_WARNING_BYTES:
                return False
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
            _last_cleanup[directory] = time.monotonic()
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _maybe_cleanup(directory: Path) -> None:
    """Prune expired and over-budget public reviews on reads/status, at most hourly per directory.

    Uses the same lock as writes. No daemon, timer, or raw-record deletion.
    """
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        return
    with _cache_lock:
        now = time.monotonic()
        last = _last_cleanup.get(directory)
        if last is not None and now - last < CACHE_HOUSEKEEPING_SECONDS:
            return
        try:
            _evict(directory)
        except OSError:
            return
        _last_cleanup[directory] = now
        # Keep memory bounded even if callers use many temporary directories.
        if len(_last_cleanup) > 32:
            _last_cleanup.pop(next(iter(_last_cleanup)))


def _evict(directory: Path):
    """Remove expired entries, then trim the cache to its soft budget.

    CACHE_BUDGET_BYTES remains the absolute hard ceiling; the 85% soft target
    leaves headroom for atomic replacement and short-lived concurrent writes.
    """
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
        if total <= CACHE_SOFT_BUDGET_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            pass


def storage_metrics(directory: Path) -> dict:
    """No filenames, paths, UUIDs or user data are ever returned."""
    directory = Path(directory)
    _maybe_cleanup(directory)
    try:
        entries = [p.stat().st_size for p in directory.glob("[0-9a-f]*.json")
                   if _KEY.fullmatch(p.stem) and not p.is_symlink()]
        capacity = shutil.disk_usage(directory if directory.is_dir() else directory.parent)
        # Some FUSE-backed volumes report UINT64_MAX-like sentinel values.
        # Treat impossible capacities as unknown instead of claiming vast free space.
        capacity_valid = (
            type(capacity.total) is int and type(capacity.free) is int
            and 0 < capacity.total <= sys.maxsize
            and 0 <= capacity.free <= capacity.total
        )
        free_bytes = capacity.free if capacity_valid else None
        total_bytes = capacity.total if capacity_valid else None
        return {"entries": len(entries), "bytes": sum(entries),
                "budget_bytes": CACHE_BUDGET_BYTES,
                "soft_budget_bytes": CACHE_SOFT_BUDGET_BYTES, "free_bytes": free_bytes,
                "disk_total_bytes": total_bytes,
                "low_disk_space": free_bytes < LOW_DISK_WARNING_BYTES if free_bytes is not None else None,
                "capacity_status": "ok" if capacity_valid else "unknown",
                "warning_threshold_bytes": LOW_DISK_WARNING_BYTES}
    except OSError:
        return {"entries": 0, "bytes": 0, "budget_bytes": CACHE_BUDGET_BYTES,
                "soft_budget_bytes": CACHE_SOFT_BUDGET_BYTES,
                "free_bytes": None, "disk_total_bytes": None,
                "low_disk_space": None, "capacity_status": "unknown",
                "warning_threshold_bytes": LOW_DISK_WARNING_BYTES}
