"""Analysis request lifecycle independent of the FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException, Request, Response

from . import model_download
from .analysis_logging import _log_performance
from .engine.model_policy import MODEL_ID, normalize_model_id
from .model_download import engine_mgr, model_download_state
from .paipu_performance import performance as _performance, measure as _measure, snapshot as _performance_snapshot, set_value as _set_performance_value
from .public_review_cache import _public_review_result


async def _wait_for_model_download(model_id: str | None = None, timeout_s: float = 300.0) -> None:
    target_id = model_id or MODEL_ID
    try:
        normalize_model_id(target_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    st = model_download_state
    if not st:
        return
    wait_started = time.perf_counter()
    download_metrics = model_download._model_download_metrics if st["status"] == "downloading" else None
    try:
        with _measure("AI模型准备耗时"), _measure("模型下载等待耗时"):
            if st["status"] == "downloading":
                t0 = time.time()
                while st["status"] == "downloading":
                    if time.time() - t0 > timeout_s:
                        raise HTTPException(status_code=503, detail=f"AI 模型 ({target_id}) 正在首次下载中，请求等待超时，请稍后重试")
                    await asyncio.sleep(0.5)
    finally:
        wait_finished = time.perf_counter()
        if download_metrics is not None and download_metrics is model_download._model_download_metrics:
            background, _ = _performance_snapshot(download_metrics)
            init_started = background.get("_model_init_started")
            if init_started is not None:
                init_finished = background.get("_model_init_finished", wait_finished)
                overlap = max(0.0, min(wait_finished, init_finished) - max(wait_started, init_started))
                _set_performance_value("后台模型加载等待耗时", overlap * 1000)
    if st["status"] == "error":
        # A failed replacement must not make an already verified model unusable.
        try:
            await asyncio.to_thread(engine_mgr.get_mortal, target_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"AI 模型 ({target_id}) 下载失败，请稍后重试。") from exc


async def _run_nonstream_until_disconnect(request: Request, response: Response, work) -> dict[str, Any]:
    """Stop waiting or preparing after a real ASGI disconnect.

    A started review owns its permit independently until its worker finishes.
    """
    async def watch_disconnect() -> None:
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    stats: dict[str, Any] = {"mode": "准备中", "_succeeded": False, "AI模型加载次数": 0, "AI模型复用次数": 0}
    started = time.perf_counter()
    stats["_started"] = started
    token = _performance.set(stats)
    work_task = asyncio.create_task(work)
    disconnect_task = asyncio.create_task(watch_disconnect())
    try:
        done, _ = await asyncio.wait((work_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED)
        if disconnect_task in done:
            # Propagate receive errors too; don't leave the analysis running.
            disconnect_task.result()
            stats["_failed"] = True
            stats.setdefault("失败阶段", "客户端连接中断")
            stats.setdefault("错误类型", "连接取消")
            raise asyncio.CancelledError
        result = await work_task
        with _measure("结果返回"):
            public_result = _public_review_result(result)
        stats["_succeeded"] = True
        return public_result
    except HTTPException as exc:
        stats["_failed"] = True
        stats.setdefault("失败阶段", stats.get("最后失败阶段") or "按需分析")
        stats.setdefault("错误类型", f"HTTP{exc.status_code}")
        raise
    except Exception:
        stats["_failed"] = True
        stats.setdefault("失败阶段", stats.get("最后失败阶段") or "按需分析")
        stats.setdefault("错误类型", "服务内部异常")
        raise
    finally:
        for task in (work_task, disconnect_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(work_task, disconnect_task, return_exceptions=True)
        _log_performance(stats, started)
        _performance.reset(token)


