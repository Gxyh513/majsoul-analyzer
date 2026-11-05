"""Streaming paipu analysis routes and completion event framing."""
from __future__ import annotations

import asyncio
from contextlib import aclosing
import json
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .engine.model_policy import normalize_model_id
from .logger import log_debug, log_error
from .api_schemas import AnalyzeStreamReq
from .paipu_performance import measure as _measure, set_value as _set_performance_value, performance as _performance
from .analysis_logging import _log_performance
from .error_context import _safe_error_context
from .public_review_cache import _public_review_result, _try_fast_public_review
from .review_transport import compact_review_snapshots
from .analysis_service import _wait_for_model_download
from .game_data import resolve_game_data
from .model_download import engine_mgr
from .review_jobs import review_semaphore, _start_review_job

router = APIRouter()


class _ClosingStreamingResponse(StreamingResponse):
    async def stream_response(self, send) -> None:
        # Starlette can be cancelled while sending a yielded chunk, outside the
        # generator. Close it explicitly so its progress listener is detached.
        async with aclosing(self.body_iterator):
            await super().stream_response(send)

    async def __call__(self, scope, receive, send) -> None:
        stream_task = asyncio.create_task(self.stream_response(send))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(receive))
        try:
            done, _ = await asyncio.wait((stream_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED)
            if disconnect_task in done:
                disconnect_task.result()
            else:
                stream_task.result()
        finally:
            for task in (stream_task, disconnect_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream_task, disconnect_task, return_exceptions=True)
        if self.background is not None:
            await self.background()


def _complete_review_event(result: dict[str, Any], transport_version: int = 1) -> str:
    raw_prefix = 'data: {"type": "complete", "result": '
    with _measure("结果返回"):
        with _measure("完成结果序列化耗时"):
            public_result = _public_review_result(result)
            if transport_version == 2:
                public_result = compact_review_snapshots(public_result)
            result_json = json.dumps(public_result, ensure_ascii=False, separators=(",", ":"))
            result_bytes = result_json.encode("utf8")
        _set_performance_value("原始complete事件字节数", len(raw_prefix) + len(result_bytes) + len("}\n\n"))
        _set_performance_value("原始检讨JSON字节数", len(result_bytes))
        return f"{raw_prefix}{result_json}}}\n\n"


@router.post("/api/analyze/stream")
async def api_analyze_stream(req: AnalyzeStreamReq, request: Request = None):
    request_id = getattr(request.state, "sse_request_id", None) if request is not None else None
    request_started = getattr(request.state, "sse_request_started", None) if request is not None else None
    request_id = request_id or uuid.uuid4().hex
    if req.content is not None:
        raise HTTPException(status_code=400, detail="已停止支持 JSON 上传，请使用雀魂牌谱链接。")
    if type(req.transport_version) is not int or req.transport_version not in (1, 2):
        raise HTTPException(status_code=400, detail="不支持的回放传输版本。")
    try:
        normalize_model_id(req.engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def wait_for_phase(task: asyncio.Task, kind: str, percent: float, message: str):
        started = time.monotonic()
        try:
            while not task.done():
                done, _ = await asyncio.wait((task,), timeout=10)
                if not done:
                    elapsed = int(time.monotonic() - started)
                    event = {"type": kind, "percent": percent, "message": f"{message}（已等待 {elapsed} 秒）"}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def sse_generator(stats: dict[str, Any]):
        try:
            yield f"data: {json.dumps({'type': 'progress', 'percent': 5, 'message': '正在检查 AI 模型...'}, ensure_ascii=False)}\n\n"

            async def prepare_model():
                with _measure("初始模型准备阶段耗时"):
                    await _wait_for_model_download(req.engine)
                    try:
                        await asyncio.to_thread(engine_mgr.get_mortal, req.engine)
                    except Exception as exc:
                        raise HTTPException(status_code=503, detail="AI 模型不可用，请检查或重新下载模型。") from exc

            model_task = asyncio.create_task(prepare_model())
            async with aclosing(wait_for_phase(model_task, "progress", 5, "正在检查 AI 模型")) as updates:
                async for update in updates:
                    yield update
            model_task.result()

            # Reuse a verified raw-source cache entry without repeating protobuf
            # decode or MJAI conversion; the stream framing remains unchanged.
            cached = await _try_fast_public_review(req.paipu, req.target_actor, req.engine, req.temperature,
                                                   compact=req.transport_version == 2)
            if cached is not None:
                stats["mode"] = "公开牌谱缓存快速命中"
                completed_at = time.perf_counter()
                stats["100%事件生成时间"] = (completed_at - stats["_started"]) * 1000
                if request_started is not None:
                    stats["ASGI请求至100%事件生成"] = (completed_at - request_started) * 1000
                yield f"data: {json.dumps({'type': 'progress', 'percent': 100, 'message': '已复用公开牌谱缓存'}, ensure_ascii=False)}\n\n"
                event = await asyncio.to_thread(_complete_review_event, cached, req.transport_version)
                completion_at = time.perf_counter()
                stats["complete事件生成时间"] = (completion_at - stats["_started"]) * 1000
                if request_started is not None:
                    stats["ASGI请求至complete事件生成"] = (completion_at - request_started) * 1000
                stats["100%至complete事件生成间隔"] = (completion_at - completed_at) * 1000
                yield event
                return

            yield f"data: {json.dumps({'type': 'progress', 'percent': 5, 'message': '正在解析输入并准备牌谱数据...'}, ensure_ascii=False)}\n\n"
            try:
                resolve_task = asyncio.create_task(resolve_game_data(
                    paipu=req.paipu, target_actor=req.target_actor
                ))
                async with aclosing(wait_for_phase(resolve_task, "progress", 5, "正在准备牌谱数据")) as updates:
                    async for update in updates:
                        yield update
                game_id, mjai_events, meta, seat = resolve_task.result()
            except HTTPException as e:
                stats["_failed"] = True
                stats.setdefault("失败阶段", "原始牌谱获取总耗时" if stats.get("牌谱获取结果") else "UUID解析")
                stats.setdefault("错误类型", f"HTTP{e.status_code}")
                yield f"data: {json.dumps({'type': 'error', 'detail': e.detail}, ensure_ascii=False)}\n\n"
                return
            except Exception as e:
                stats["_failed"] = True
                stats.setdefault("失败阶段", "牌谱数据准备")
                stats.setdefault("错误类型", "牌谱解析异常")
                log_error(
                    事件="牌谱解析失败",
                    失败阶段="牌谱数据准备",
                    异常信息=_safe_error_context(e),
                )
                yield f"data: {json.dumps({'type': 'error', 'detail': '牌谱解析失败，请检查输入后重试。'}, ensure_ascii=False)}\n\n"
                return

            stats["mode"] = "按需分析"
            if review_semaphore.locked():
                log_debug(事件="AI推理排队", 牌谱编号=game_id, 玩家视角=seat, 提示="服务器正忙，当前正在排队中")
                yield f"data: {json.dumps({'type': 'queued', 'message': '服务器正忙，当前正在排队中...'}, ensure_ascii=False)}\n\n"

            # The request is cancellable while waiting for the permit. Once a
            # job starts it owns the permit and survives any SSE cancellation.
            loop = asyncio.get_running_loop()
            q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
            listening = threading.Event()
            listening.set()

            def enqueue_progress(data: dict[str, Any]) -> None:
                if not listening.is_set():
                    return
                if q.full():
                    q.get_nowait()
                q.put_nowait(data)

            def on_progress(pct: float, msg: str) -> None:
                if listening.is_set():
                    try:
                        loop.call_soon_threadsafe(enqueue_progress, {"percent": round(pct, 1), "message": msg})
                    except RuntimeError:
                        # A forcibly stopped event loop cannot receive progress.
                        listening.clear()

            try:
                start_task = asyncio.create_task(_start_review_job(
                    game_id=game_id, mjai_events=mjai_events, meta=meta, seat=seat,
                    engine=req.engine, temperature=req.temperature, progress_callback=on_progress,
                    cache_public=(req.content is None and game_id != "demo" and bool(req.paipu)),
                    compact_cache=req.transport_version == 2,
                ))
                async with aclosing(wait_for_phase(start_task, "queued", 5, "服务器正忙，当前正在排队中")) as updates:
                    async for update in updates:
                        yield update
                task = start_task.result()
                progress = {"percent": 15, "message": "牌谱准备就绪，启动 AI 检讨引擎..."}
                progress_started = last_update = time.monotonic()
                yield f"data: {json.dumps({'type': 'progress', **progress}, ensure_ascii=False)}\n\n"
                # Wake on progress or completion; no quarter-second polling.
                while not task.done():
                    next_progress = asyncio.create_task(q.get())
                    try:
                        heartbeat_after = max(0.0, 10.0 - (time.monotonic() - last_update))
                        done, _ = await asyncio.wait(
                            (task, next_progress),
                            timeout=heartbeat_after,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if next_progress in done:
                            progress = next_progress.result()
                            progress_started = last_update = time.monotonic()
                            yield f"data: {json.dumps({'type': 'progress', **progress}, ensure_ascii=False)}\n\n"
                        elif task not in done:
                            now = time.monotonic()
                            waiting = {**progress, "message": f"{progress['message']}（已等待 {int(now - progress_started)} 秒）"}
                            yield f"data: {json.dumps({'type': 'progress', **waiting}, ensure_ascii=False)}\n\n"
                            last_update = now
                    finally:
                        if not next_progress.done():
                            next_progress.cancel()
                            await asyncio.gather(next_progress, return_exceptions=True)

                while not q.empty():
                    progress = q.get_nowait()
                    yield f"data: {json.dumps({'type': 'progress', **progress}, ensure_ascii=False)}\n\n"
                res = await asyncio.shield(task)
                progress_event = f"data: {json.dumps({'type': 'progress', 'percent': 100, 'message': '检讨完成！'}, ensure_ascii=False)}\n\n"
                progress_generated = time.perf_counter()
                stats["100%事件生成时间"] = (progress_generated - stats["_started"]) * 1000
                if request_started is not None:
                    stats["ASGI请求至100%事件生成"] = (progress_generated - request_started) * 1000
                yield progress_event
                complete_event = await asyncio.to_thread(_complete_review_event, res, req.transport_version)
                complete_generated = time.perf_counter()
                stats["complete事件生成时间"] = (complete_generated - stats["_started"]) * 1000
                if request_started is not None:
                    stats["ASGI请求至complete事件生成"] = (complete_generated - request_started) * 1000
                stats["100%至complete事件生成间隔"] = (complete_generated - progress_generated) * 1000
                yield complete_event
            finally:
                # The worker may outlive this generator. Drop future progress
                # notifications instead of accumulating an unconsumed queue.
                listening.clear()

        except HTTPException as e:
            stats["_failed"] = True
            stats.setdefault("失败阶段", "AI检讨流程耗时" if "AI检讨流程耗时" in stats else ("AI推理排队等待" if "AI推理排队等待" in stats else "初始模型准备阶段耗时"))
            stats.setdefault("错误类型", f"HTTP{e.status_code}")
            log_error(事件="牌谱分析失败", 失败阶段="SSE流式分析", 异常信息=_safe_error_context(e))
            yield f"data: {json.dumps({'type': 'error', 'detail': e.detail}, ensure_ascii=False)}\n\n"
        except Exception as e:
            stats["_failed"] = True
            stats.setdefault("失败阶段", "SSE流式分析")
            stats.setdefault("错误类型", "服务内部异常")
            log_error(事件="牌谱分析失败", 失败阶段="SSE流式分析", 异常信息=_safe_error_context(e))
            yield f"data: {json.dumps({'type': 'error', 'detail': '分析暂时失败，请稍后重试。'}, ensure_ascii=False)}\n\n"
    async def measured_stream():
        stats: dict[str, Any] = {"mode": "准备中", "_succeeded": False, "request_id": request_id, "AI模型加载次数": 0, "AI模型复用次数": 0}
        started = time.perf_counter()
        stats["_started"] = started
        if request is not None:
            request.state.sse_performance_stats = stats
        generator = sse_generator(stats)
        logged = False
        complete_emitted = False
        try:
            while True:
                token = _performance.set(stats)
                try:
                    chunk = await anext(generator)
                except StopAsyncIteration:
                    break
                finally:
                    _performance.reset(token)
                yield chunk
                if chunk.startswith('data: {"type": "complete",'):
                    complete_emitted = True
                    stats["_succeeded"] = True
            sent = getattr(request.state, "sse_complete_sent", None) if request is not None else None
            if complete_emitted and sent is not None:
                await sent.wait()
            if not complete_emitted and not stats.get("_failed"):
                stats["_failed"] = True
                stats.setdefault("失败阶段", "客户端连接中断")
                stats.setdefault("错误类型", "连接取消")
            report = _log_performance(stats, started)
            logged = True
            if report is not None:
                payload = {"type": "performance", "performance": report}
                yield f"data: {json.dumps(payload, ensure_ascii=False, allow_nan=False)}\n\n"
        finally:
            await generator.aclose()
            if not logged:
                if not complete_emitted and not stats.get("_failed"):
                    stats["_failed"] = True
                    stats.setdefault("失败阶段", "客户端连接中断")
                    stats.setdefault("错误类型", "连接取消")
                _log_performance(stats, started)

    return _ClosingStreamingResponse(
        measured_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Content-Encoding": "identity",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )


