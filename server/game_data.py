"""Demo, MJAI and immutable Mahjong Soul record resolution to game events."""
from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from collections import OrderedDict
from contextvars import copy_context
from typing import Any

from fastapi import HTTPException

from .majsoul.client import LOCAL_PROTOCOL_SHA256, MajsoulError, parse_paipu, parse_paipu_seat
from .majsoul.convert import ConvertError, game_meta, to_mjai
from .record_store import CorruptRecordError, RawRecord, RecordDownloadBusyError, RecordStorageError
from .runtime_state import DEMO_PATH, RECORDS_DIR, LEGACY_RECORDS_DIRS, ms_client
from .admin_accounts import manager as admin_manager
from .app_config import load_config
from .record_store import get_or_download_record as _get_or_download_record
from .account_pool import fetch_record_with_failover
from .paipu_performance import measure as _measure, performance as _performance


# Parsed MJAI is shared across seat-specific reviews. The cache is process-local,
# content-addressed, LRU-bounded, and invalidated by the local protocol identity.
_PREPROCESS_CACHE_MAX_BYTES = 64 * 1024 * 1024
_PREPROCESS_CACHE_MAX_ITEMS = 8
_PREPROCESS_MAX_ACTIVE = 2
_PREPROCESS_MAX_WAITERS = 4
_PREPROCESS_QUEUE_TIMEOUT_SECONDS = 90.0
_preprocess_cache: OrderedDict[str, tuple[list[dict], dict, int, int]] = OrderedDict()
_preprocess_cache_bytes = 0
_preprocess_cache_lock = threading.RLock()
_preprocess_inflight: dict[str, dict[str, Any]] = {}
_preprocess_semaphore = asyncio.Semaphore(_PREPROCESS_MAX_ACTIVE)
_preprocess_waiters = 0
_demo_cache_lock = threading.Lock()
_demo_cache: tuple[tuple[int, int], list[dict], int] | None = None


def _load_demo_events() -> tuple[list[dict], int]:
    global _demo_cache
    stat = DEMO_PATH.stat()
    identity = (stat.st_size, stat.st_mtime_ns)
    with _demo_cache_lock:
        if _demo_cache is not None and _demo_cache[0] == identity:
            return _demo_cache[1], _demo_cache[2]
    text = DEMO_PATH.read_text(encoding="utf8")
    with _measure("JSON解析"):
        events = json.loads(text)
    with _measure("MJAI数据大小计算"):
        size_bytes = len(json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
    with _demo_cache_lock:
        _demo_cache = (identity, events, size_bytes)
    return events, size_bytes


def _decode_convert_raw_record(stored: RawRecord, game_uuid: str, cache_key: str):
    global _preprocess_cache_bytes

    with _preprocess_cache_lock:
        cached = _preprocess_cache.get(cache_key)
        if cached is not None:
            _preprocess_cache.move_to_end(cache_key)
            return cached[:3]

    with _measure("Protobuf解码"):
        record = ms_client.decode_record(stored, game_uuid)
    with _measure("MJAI转换"):
        events, meta = to_mjai(record), game_meta(record)
    with _measure("MJAI数据大小计算"):
        events_size = len(json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
        meta_size = len(json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf8"))

    # Python dict/list/string overhead exceeds compact JSON size. Charge a
    # conservative 3x estimate plus the immutable raw bytes to bound RSS.
    estimated_bytes = len(stored.response) + len(stored.detail) + 3 * (events_size + meta_size)
    if estimated_bytes <= _PREPROCESS_CACHE_MAX_BYTES:
        with _preprocess_cache_lock:
            while _preprocess_cache and (
                len(_preprocess_cache) >= _PREPROCESS_CACHE_MAX_ITEMS
                or _preprocess_cache_bytes + estimated_bytes > _PREPROCESS_CACHE_MAX_BYTES
            ):
                _, evicted = _preprocess_cache.popitem(last=False)
                _preprocess_cache_bytes -= evicted[3]
            _preprocess_cache[cache_key] = (events, meta, events_size, estimated_bytes)
            _preprocess_cache_bytes += estimated_bytes

    # The review pipeline treats MJAI events and metadata as read-only inputs.
    return events, meta, events_size


async def _prepare_raw_record_owner(stored: RawRecord, game_uuid: str, cache_key: str,
                                    entry: dict[str, Any]):
    global _preprocess_waiters
    semaphore = _preprocess_semaphore

    async def acquire_permit() -> None:
        await semaphore.acquire()
        # Publish ownership in the acquiring coroutine, before wait_for resumes
        # its caller, so disconnects cannot mistake an acquired job for a waiter.
        entry["acquired"] = True

    try:
        if semaphore.locked():
            if _preprocess_waiters >= _PREPROCESS_MAX_WAITERS:
                raise HTTPException(status_code=429, detail="牌谱预处理队列已满，请稍后重试。")
            _preprocess_waiters += 1
            try:
                with _measure("牌谱预处理排队等待"):
                    try:
                        await asyncio.wait_for(
                            acquire_permit(),
                            timeout=_PREPROCESS_QUEUE_TIMEOUT_SECONDS,
                        )
                    except TimeoutError as exc:
                        raise HTTPException(status_code=503, detail="牌谱预处理等待超时，请稍后重试。") from exc
            finally:
                _preprocess_waiters -= 1
        else:
            with _measure("牌谱预处理排队等待"):
                await acquire_permit()

        with _measure("牌谱预处理"):
            # An executor future is not cancelled with the wrapper task. Retain
            # the permit until the actual thread finishes, preserving to_thread's
            # context propagation for request-scoped performance measurements.
            future = asyncio.get_running_loop().run_in_executor(
                None, copy_context().run, _decode_convert_raw_record, stored, game_uuid, cache_key,
            )
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(future)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    if future.done():
                        result = future.result()
                        break
            if cancelled:
                raise asyncio.CancelledError
            return result
    finally:
        if entry["acquired"]:
            semaphore.release()


async def _prepare_raw_record(stored: RawRecord, game_uuid: str, source_digest: str):
    cache_key = f"{LOCAL_PROTOCOL_SHA256}:{game_uuid}:{source_digest}"
    with _preprocess_cache_lock:
        cached = _preprocess_cache.get(cache_key)
        if cached is not None:
            _preprocess_cache.move_to_end(cache_key)
            return cached[:3]

    entry = _preprocess_inflight.get(cache_key)
    if entry is not None and (entry["task"].cancelled() or entry["task"].cancelling()):
        # A cancelled wrapper can still be draining its thread. New callers
        # must queue behind its permit rather than inherit its cancellation.
        _preprocess_inflight.pop(cache_key, None)
        entry = None
    if entry is None:
        entry = {"subscribers": 0, "acquired": False}
        task = asyncio.create_task(_prepare_raw_record_owner(stored, game_uuid, cache_key, entry))
        entry["task"] = task
        _preprocess_inflight[cache_key] = entry

        def forget_finished(done: asyncio.Task):
            # Includes cancellation before the owner's first coroutine step.
            if _preprocess_inflight.get(cache_key) is entry:
                _preprocess_inflight.pop(cache_key, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(forget_finished)

    task = entry["task"]
    entry["subscribers"] += 1
    try:
        # A seat switch or concurrent request shares one decode/conversion task.
        return await asyncio.shield(task)
    finally:
        entry["subscribers"] -= 1
        if entry["subscribers"] == 0 and not entry["acquired"] and not task.done():
            # Cancel only unstarted work, and detach before another caller can
            # join the doomed owner. Acquired jobs finish and populate the LRU.
            if _preprocess_inflight.get(cache_key) is entry:
                _preprocess_inflight.pop(cache_key, None)
            task.cancel()



async def _record_mjai_size(events: list[dict], size_bytes: int | None = None) -> None:
    stats = _performance.get()
    if stats is not None:
        if type(size_bytes) is int and size_bytes >= 0:
            stats["MJAI数据大小"] = size_bytes
            return
        try:
            stats["MJAI数据大小"] = await asyncio.to_thread(
                lambda: len(json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
            )
        except Exception:  # noqa: BLE001
            pass


def select_game_seat(meta: dict, target_actor: int | None, url_acc_id: int | None = None) -> int:
    if target_actor is not None:
        return target_actor if 0 <= target_actor <= 3 else 0
    acc_ids = meta.get("account_ids", [])
    # A randomly assigned downloader account must not determine the reviewed player's seat.
    candidates = (url_acc_id,) if admin_manager() else (url_acc_id, ms_client.account_id, load_config().get("account_id"))
    for account_id in candidates:
        if account_id and account_id in acc_ids:
            return acc_ids.index(account_id)
    return 0


async def get_or_download_record(game_uuid: str) -> RawRecord | dict:
    try:
        return await _get_or_download_record(
            game_uuid, RECORDS_DIR, fetch_record_with_failover, legacy_dirs=LEGACY_RECORDS_DIRS
        )
    except RecordDownloadBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except RecordStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="无法访问原始牌谱存储目录。") from exc


async def resolve_game_data(
    paipu: str | None = None, content: Any | None = None, target_actor: int | None = None,
) -> tuple[str, list[dict], dict, int]:
    raw_input = (paipu or "").strip()
    if content is None and (not raw_input or raw_input == "demo" or raw_input.startswith("demo-")):
        if not DEMO_PATH.is_file():
            raise HTTPException(status_code=404, detail="内置演示牌谱文件不存在")
        events, demo_size = await asyncio.to_thread(_load_demo_events)
        meta = {"uuid": "demo-241024-majsoul-4p", "names": ["一姬 (演示)", "二阶堂美树", "八木唯", "斋藤治"],
                "room": "玉之间·四人南 (内置演示)", "start_time": 1729760000}
        await _record_mjai_size(events, demo_size)
        return "demo", events, meta, select_game_seat(meta, target_actor)

    if content is not None:
        if isinstance(content, str):
            text = content.strip()
            try:
                with _measure("JSON解析"):
                    content = await asyncio.to_thread(json.loads, text)
            except json.JSONDecodeError:
                try:
                    with _measure("JSON解析"):
                        content = await asyncio.to_thread(
                            lambda: [json.loads(line) for line in text.splitlines() if line.strip()]
                        )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="无法解析 JSON / JSONL 格式。") from exc
        try:
            if isinstance(content, dict) and "head" in content and "actions" in content:
                source_uuid = content.get("head", {}).get("uuid")
                if source_uuid and (not isinstance(source_uuid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", source_uuid)):
                    raise HTTPException(status_code=400, detail="牌谱 UUID 格式无效")
                with _measure("MJAI转换"):
                    events, meta = await asyncio.to_thread(lambda: (to_mjai(content), game_meta(content)))
                game_id = "upload-" + uuid.uuid4().hex
            elif isinstance(content, list) and content and isinstance(content[0], dict) and "type" in content[0]:
                events = content
                names = next((list(event["names"]) for event in events if event.get("type") == "start_game" and event.get("names")), ["东家", "南家", "西家", "北家"])
                game_id = "mjai-" + uuid.uuid4().hex
                meta = {"uuid": game_id, "names": names, "start_time": None}
            else:
                raise HTTPException(status_code=400, detail="不支持的 JSON 结构：请上传雀魂原始牌谱 JSON 或 mjai 事件数组/JSONL。")
        except (ConvertError, ValueError, TypeError, KeyError, IndexError) as exc:
            raise HTTPException(status_code=400, detail="牌谱数据无法解析，请检查文件。") from exc
        await _record_mjai_size(events)
        return game_id, events, meta, select_game_seat(meta, target_actor)

    try:
        with _measure("UUID解析"):
            game_uuid, url_acc_id = parse_paipu(raw_input)
    except (MajsoulError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="无法识别的牌谱 ID，请检查输入。") from exc
    stats = _performance.get()
    if stats is not None:
        stats["牌谱获取结果"] = "进行中"
    try:
        # The public-review fast path already verified and read immutable raw
        # bytes. Reuse them on a cache miss rather than hitting disk a second time.
        prefetched = stats.get("_prefetched_raw_digest") if isinstance(stats, dict) else None
        if (isinstance(prefetched, tuple) and len(prefetched) == 3
                and prefetched[0] == game_uuid and isinstance(prefetched[1], RawRecord)):
            stored = prefetched[1]
            stats["是否命中本地记录"] = True
            stats["牌谱获取方式"] = "本地直接读取"
        else:
            with _measure("原始牌谱获取总耗时"):
                stored = await get_or_download_record(game_uuid)
    except asyncio.CancelledError:
        if stats is not None:
            stats["牌谱获取结果"] = "已取消"
        raise
    except HTTPException as exc:
        if stats is not None:
            stats["牌谱获取结果"] = f"失败(HTTP{exc.status_code})"
        raise
    except Exception:
        if stats is not None:
            stats["牌谱获取结果"] = "失败"
        raise
    else:
        if stats is not None:
            stats["牌谱获取结果"] = "成功"
    try:
        mjai_size = None
        if isinstance(stored, RawRecord):
            stats = _performance.get()
            if stats is not None:
                stats["原始数据大小"] = len(stored.response) + len(stored.detail)
            # Reuse the exact digest computed by the public-review fast path when
            # available; otherwise hash the immutable bytes once for this cache key.
            prefetched = stats.pop("_prefetched_raw_digest", None) if isinstance(stats, dict) else None
            if (
                isinstance(prefetched, tuple)
                and len(prefetched) == 3
                and prefetched[0] == game_uuid
                and prefetched[1] == stored
            ):
                digest = prefetched[2]
            else:
                from .public_review_cache import _public_raw_source_digest
                digest = await asyncio.to_thread(_public_raw_source_digest, stored)
            if isinstance(stats, dict):
                stats["_public_raw_source"] = (game_uuid, digest)
            events, meta, mjai_size = await _prepare_raw_record(stored, game_uuid, digest)
        else:
            record = stored
            with _measure("MJAI转换"):
                events, meta = await asyncio.to_thread(lambda: (to_mjai(record), game_meta(record)))
    except (CorruptRecordError, MajsoulError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (ConvertError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="原始牌谱无法转换，请检查牌谱或协议版本。") from exc
    await _record_mjai_size(events, mjai_size)
    url_seat = parse_paipu_seat(raw_input)
    seat = target_actor if target_actor is not None else url_seat
    return game_uuid, events, meta, select_game_seat(meta, seat, url_acc_id if url_seat is None else None)


