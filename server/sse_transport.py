"""ASGI SSE compression, transfer timings and request correlation."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
import zlib

from .logger import is_debug_mode, log_debug


def _accepts_gzip(headers) -> bool:
    encodings: dict[str, float] = {}
    for key, value in headers:
        if key.lower() != b"accept-encoding":
            continue
        for entry in value.decode("latin1").split(","):
            parts = entry.strip().lower().split(";")
            quality = 1.0
            for option in parts[1:]:
                name, separator, value = option.partition("=")
                if name.strip() == "q":
                    try:
                        quality = float(value.strip()) if separator else 0.0
                    except ValueError:
                        quality = 0.0
            if parts[0].strip():
                encodings[parts[0].strip()] = quality if 0 <= quality <= 1 else 0.0
    return encodings.get("gzip", encodings.get("*", 0.0)) > 0


class _SSESendMetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/analyze/stream" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        raw_id = next((value for key, value in scope.get("headers", ()) if key.lower() == b"x-request-id"), b"")
        try:
            request_id = raw_id.decode("ascii")
        except UnicodeDecodeError:
            request_id = ""
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            request_id = uuid.uuid4().hex
        request_started = time.perf_counter()
        state = scope.setdefault("state", {})
        complete_sent = asyncio.Event()
        state.update(sse_request_id=request_id, sse_request_started=request_started, sse_complete_sent=complete_sent)
        progress_started = None
        progress_sent = None
        compressor = None

        async def measured_send(message):
            nonlocal progress_started, progress_sent, compressor
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", ()) if key.lower() != b"x-request-id"]
                content_type = next((value for key, value in headers if key.lower() == b"content-type"), b"")
                encoding = next((value for key, value in headers if key.lower() == b"content-encoding"), b"")
                streaming = message.get("status") == 200 and content_type.lower().startswith(b"text/event-stream")
                if streaming and encoding.lower() in (b"", b"identity") and _accepts_gzip(scope.get("headers", ())):
                    compressor = zlib.compressobj(level=3, wbits=16 + zlib.MAX_WBITS)
                    headers = [(key, value) for key, value in headers
                               if key.lower() not in (b"content-encoding", b"content-length")]
                    headers.append((b"content-encoding", b"gzip"))
                if streaming:
                    headers.append((b"vary", b"Accept-Encoding"))
                await send({**message, "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))]})
                return
            if message["type"] != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"")
            if body.startswith(b'data: {"type": "progress", "percent": 100,'):
                event = "100%"
            elif body.startswith(b'data: {"type": "complete",'):
                event = "complete"
            else:
                event = None
            stats = state.get("sse_performance_stats")
            started = None
            started_wall = None
            completed = False
            sent_bytes = 0
            compression_ms = 0.0
            send_wait_ms = 0.0
            try:
                # Group large complete frames into fewer sends and gzip flushes.
                # Consumers parse this event only after its terminating newline.
                complete_chunk_bytes = 256 * 1024
                parts = range(0, len(body), complete_chunk_bytes) if event == "complete" else (0,)
                for offset in parts:
                    raw = body[offset:offset + complete_chunk_bytes] if event == "complete" else body
                    last = event != "complete" or offset + complete_chunk_bytes >= len(body)
                    more = message.get("more_body", False) if last else True
                    if compressor is not None:
                        compression_started = time.perf_counter()
                        encoded = compressor.compress(raw)
                        encoded += compressor.flush(zlib.Z_SYNC_FLUSH if more else zlib.Z_FINISH)
                        compression_ms += (time.perf_counter() - compression_started) * 1000
                    else:
                        encoded = raw
                    send_started = time.perf_counter()
                    if started is None:
                        started = send_started
                        started_wall = time.time_ns() / 1_000_000
                    try:
                        await send({**message, "body": encoded, "more_body": more})
                    finally:
                        send_wait_ms += (time.perf_counter() - send_started) * 1000
                    sent_bytes += len(encoded)
                completed = True
            finally:
                finished = time.perf_counter()
                if event is not None and started is not None:
                    gap = (started - progress_sent) * 1000 if event == "complete" and progress_sent is not None else None
                    start_gap = (started - progress_started) * 1000 if event == "complete" and progress_started is not None else None
                    start_ms, finish_ms = (started - request_started) * 1000, (finished - request_started) * 1000
                    if event == "complete" and stats is not None:
                        if compressor is not None:
                            stats["complete压缩耗时"] = compression_ms
                            stats["结果返回"] = stats.get("结果返回", 0.0) + compression_ms
                            stats["gzip压缩后字节数"] = sent_bytes
                        stats["complete传输字节数"] = sent_bytes
                        stats["complete压缩状态"] = ("成功" if compressor is not None else "未启用") if completed else "中断"
                        stats["complete ASGI发送开始时间"] = start_ms
                        stats["complete ASGI发送完成时间"] = finish_ms if completed else None
                        stats["complete ASGI发送等待耗时"] = send_wait_ms
                        stats["complete ASGI发送结果"] = "完成" if completed else "中断"
                    if is_debug_mode():
                        send_detail = {
                            "事件": "SSE发送明细",
                            "请求编号": request_id,
                            "阶段": event,
                            "传输大小KB": round(sent_bytes / 1024, 2),
                            "ASGI发送开始毫秒": start_ms,
                            "ASGI发送完成毫秒": finish_ms if completed else None,
                            "ASGI发送开始Unix时间毫秒": started_wall,
                            "ASGI发送完成Unix时间毫秒": time.time_ns() / 1_000_000 if completed else None,
                            "ASGI发送等待毫秒": send_wait_ms,
                            "状态": "完成" if completed else "中断",
                            "时间基准": "ASGI请求到达",
                            "口径": "ASGI服务器接收，不代表浏览器收到",
                        }
                        if gap is not None:
                            send_detail["发送完成间隔毫秒"] = gap
                        if start_gap is not None:
                            send_detail["发送开始间隔毫秒"] = start_gap
                        log_debug(**send_detail)
                    if event == "100%" and completed:
                        progress_started, progress_sent = started, finished
                if event == "complete":
                    complete_sent.set()

        await self.app(scope, receive, measured_send)


