"""Cached metadata listing for locally persisted Mahjong Soul records."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from .majsoul.convert import game_meta
from .record_store import RawRecord, read_record
from .runtime import RECORDS_DIR, LEGACY_RECORDS_DIRS, ms_client

router = APIRouter()


_LOCAL_META_CACHE_MAX = 512
_local_record_meta_cache: OrderedDict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = OrderedDict()
_local_record_meta_lock = threading.Lock()


def _collect_local_records() -> list[dict[str, Any]]:
    records = []
    seen = set()
    for directory in (RECORDS_DIR, *LEGACY_RECORDS_DIRS):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: (item.stem, item.suffix != ".pb")):
            if path.suffix not in (".pb", ".json") or path.stem in seen:
                continue
            seen.add(path.stem)
            try:
                stat = path.stat()
                file_sig = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                with _local_record_meta_lock:
                    cached_entry = _local_record_meta_cache.get(path)
                    if cached_entry is not None and cached_entry[0] == file_sig:
                        _local_record_meta_cache.move_to_end(path)
                        records.append(dict(cached_entry[1]))
                        continue
                stored = read_record(directory, path.stem)
                record = ms_client.decode_record_head(stored, path.stem) if isinstance(stored, RawRecord) else stored
                meta = game_meta(record)
                meta.update({"uuid": path.stem, "file_name": path.name, "file_size": stat.st_size})
                with _local_record_meta_lock:
                    _local_record_meta_cache[path] = (file_sig, dict(meta))
                    _local_record_meta_cache.move_to_end(path)
                    while len(_local_record_meta_cache) > _LOCAL_META_CACHE_MAX:
                        _local_record_meta_cache.popitem(last=False)
            except Exception:
                meta = {"uuid": path.stem, "file_name": path.name, "names": ["无法解析"], "error": "牌谱文件不可读取"}
            records.append(meta)
    records.sort(key=lambda record: record.get("start_time") or 0, reverse=True)
    return records


@router.get("/api/records/local")
async def api_records_local():
    records = await asyncio.to_thread(_collect_local_records)
    return {"records": records, "total": len(records)}


