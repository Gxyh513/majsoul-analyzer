"""In-flight review coalescing, CPU semaphore ownership and disk writeback."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastapi import HTTPException

from . import review_cache
from .logger import log_error, log_warning
from .runtime_state import REVIEWS_DIR
from .public_review_cache import _review_fingerprint, _public_raw_review_key, _public_review_result
from .error_context import _safe_error_context
from .paipu_performance import increment as _increment, performance as _performance, measure as _measure, set_value as _set_performance_value
from .model_download import engine_mgr
from .engine.reviewer import review_game, ModelInferenceError
from .engine.model_policy import normalize_model_id, valid_review_source

def _bounded_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


# The single inference lane stays fixed; only queue back-pressure is tunable.
review_semaphore = asyncio.Semaphore(1)
_review_tasks: set[asyncio.Task] = set()
REVIEW_MAX_WAITERS = _bounded_setting("MAJSOUL_REVIEW_MAX_WAITERS", 4, 1, 32)
REVIEW_QUEUE_TIMEOUT_SECONDS = _bounded_setting("MAJSOUL_REVIEW_QUEUE_TIMEOUT_SECONDS", 90, 15, 300)
_review_waiters = 0
# Coalesce simultaneous jobs; completed results persist only for public paipu links.
# Internal JSON and demonstration inputs must never enter the disk review cache.
_review_inflight: dict[str, dict[str, Any]] = {}


async def _start_review_job(*, game_id: str, mjai_events: list[dict], meta: dict, seat: int,
                            engine: str, temperature: float, progress_callback=None,
                            cache_public: bool = False) -> asyncio.Task:
    # A persistent cache is allowed only for URL-based paipu analysis, never
    # for demo or internal JSON conversion.
    key = _review_fingerprint(game_id, mjai_events, meta, seat, engine, temperature)
    disk_key = key
    if cache_public:
        # An actual API request captures this fingerprint from the same raw
        # bytes it decoded. Other internal/test callers retain the original
        # events-based cache key; no second raw file read or TOCTOU gap.
        stats = _performance.get()
        source_info = stats.get("_public_raw_source") if isinstance(stats, dict) else None
        if isinstance(source_info, tuple) and len(source_info) == 2 and source_info[0] == game_id:
            disk_key = _public_raw_review_key(game_id, source_info[1], seat, engine, temperature)
        _increment("检讨结果缓存检查次数")
        cached = await asyncio.to_thread(review_cache.load, REVIEWS_DIR, disk_key, game_id, seat)
        if cached is None and disk_key != key:
            # A review saved before the raw-key optimization can still be
            # reused on the first revisit. Rewrite on demand under the new key
            # so later requests skip conversion; normal eviction bounds space.
            cached = await asyncio.to_thread(review_cache.load, REVIEWS_DIR, key, game_id, seat)
            if cached is not None:
                await asyncio.to_thread(review_cache.save, REVIEWS_DIR, disk_key, cached)
        if cached is not None:
            _increment("检讨结果缓存命中次数")
            _set_performance_value("分析结果缓存", "命中")
            if progress_callback is not None:
                progress_callback(99.0, "已复用服务器缓存的牌谱检讨结果")
            return asyncio.create_task(asyncio.sleep(0, result=cached))
        _increment("检讨结果缓存未命中次数")
        _set_performance_value("分析结果缓存", "未命中")
    entry = _review_inflight.get(key)
    if entry is not None:
        # A directly cancelled shared job remains alive until its inference
        # thread exits and releases the CPU permit. Do not attach a new caller
        # to that doomed task: it would inherit CancelledError. Launch a fresh
        # request instead, which safely queues behind the original permit.
        start = entry["start"]
        if start.done() and not start.cancelled() and start.exception() is None:
            owned = start.result()
            if owned.cancelled() or owned.cancelling():
                if _review_inflight.get(key) is entry:
                    _review_inflight.pop(key, None)
                entry = None
    if entry is None:
        callbacks = []
        entry = {"callbacks": callbacks, "starters": 0}
        _review_inflight[key] = entry

        def emit_progress(percent, message):
            for callback in tuple(callbacks):
                try:
                    callback(percent, message)
                except Exception:
                    # One disconnected SSE listener cannot fail the shared review.
                    pass

        async def launch():
            return await _start_review_job_owned(
                game_id=game_id, mjai_events=mjai_events, meta=meta, seat=seat,
                engine=engine, temperature=temperature, progress_callback=emit_progress,
                cache_key=disk_key if cache_public else None,
            )

        started = asyncio.create_task(launch())
        entry["start"] = started

        def start_finished(done):
            if done.cancelled() or done.exception() is not None:
                if _review_inflight.get(key) is entry:
                    _review_inflight.pop(key, None)
                return
            owned = done.result()
            def review_finished(_):
                if _review_inflight.get(key) is entry:
                    _review_inflight.pop(key, None)
                callbacks.clear()
            owned.add_done_callback(review_finished)

        started.add_done_callback(start_finished)

    if progress_callback is not None:
        entry["callbacks"].append(progress_callback)
    entry["starters"] += 1
    try:
        return await asyncio.shield(entry["start"])
    finally:
        entry["starters"] -= 1
        # A queued launch must not survive if every request disconnected.
        if entry["starters"] == 0 and not entry["start"].done():
            entry["start"].cancel()


async def _start_review_job_owned(*, game_id: str, mjai_events: list[dict], meta: dict, seat: int,
                                  engine: str, temperature: float, progress_callback=None,
                                  cache_key: str | None = None) -> asyncio.Task:
    global _review_waiters
    semaphore = review_semaphore
    if semaphore.locked():
        if _review_waiters >= REVIEW_MAX_WAITERS:
            raise HTTPException(status_code=429, detail="AI分析排队已满，请稍后重试。")
        _review_waiters += 1
        try:
            with _measure("AI推理排队等待"):
                try:
                    await asyncio.wait_for(semaphore.acquire(), timeout=REVIEW_QUEUE_TIMEOUT_SECONDS)
                except TimeoutError as exc:
                    raise HTTPException(status_code=503, detail="AI分析等待超时，请稍后重试。") from exc
        finally:
            _review_waiters -= 1
    else:
        with _measure("AI推理排队等待"):
            await semaphore.acquire()
    permit_acquired = time.perf_counter()
    stats = _performance.get()
    if stats is not None and "_started" in stats:
        stats["AI推理许可获得时间"] = (permit_acquired - stats["_started"]) * 1000
    released = False

    def release_once() -> None:
        nonlocal released
        if not released:
            released = True
            semaphore.release()

    def worker() -> dict[str, Any]:
        try:
            normalize_model_id(engine)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            mortal, _, _ = engine_mgr.get_mortal(engine)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI 模型不可用，请检查或重新下载模型。") from exc
        kwargs = {"meta": meta, "temperature": temperature, "force_engine": engine}
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        try:
            with _measure("AI检讨流程耗时"):
                result = review_game(mjai_events, seat, engine_mgr, **kwargs)
        except ModelInferenceError as exc:
            raise HTTPException(status_code=503, detail="AI 模型推理失败，请重新下载模型。") from exc
        if not valid_review_source(result, mortal.weight_sha256):
            raise RuntimeError("模型权重或检讨结果来源不匹配，请重试。")
        if result.get("target_actor") != seat:
            raise RuntimeError("检讨结果的玩家座位不匹配，请重试。")
        result["game_id"] = game_id
        return result

    def measured_worker() -> dict[str, Any]:
        token = _performance.set(stats)
        try:
            return worker()
        finally:
            _performance.reset(token)

    async def owned_job():
        try:
            future = asyncio.get_running_loop().run_in_executor(None, measured_worker)
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(future)
                    break
                except asyncio.CancelledError:
                    if future.done():
                        result = future.result()
                        cancelled = True
                        break
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            # Free the CPU lane before performing the optional disk write.
            release_once()
            if cache_key is not None:
                try:
                    await asyncio.to_thread(review_cache.save, REVIEWS_DIR, cache_key,
                                            _public_review_result(result))
                except Exception:
                    log_warning(事件="检讨结果缓存写入失败", 原因="缓存目录不可用")
            return result
        finally:
            release_once()

    try:
        task = asyncio.create_task(owned_job())
    except BaseException:
        release_once()
        raise
    _review_tasks.add(task)

    def job_done(done: asyncio.Task) -> None:
        release_once()
        _review_tasks.discard(done)
        if not done.cancelled():
            error = done.exception()
            if error is not None:
                log_error(
                    事件="AI检讨失败",
                    失败阶段="后台任务执行",
                    异常信息=_safe_error_context(error),
                )

    task.add_done_callback(job_done)
    return task


async def _run_review(game_id: str, mjai_events: list[dict], meta: dict, seat: int,
                      engine: str, temperature: float, *, cache_public: bool = False) -> dict[str, Any]:
    task = await _start_review_job(game_id=game_id, mjai_events=mjai_events, meta=meta, seat=seat,
                                   engine=engine, temperature=temperature, cache_public=cache_public)
    return await asyncio.shield(task)


