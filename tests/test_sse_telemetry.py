import asyncio
import logging
import re

from server import app as server_app


def test_asgi_send_uses_one_safe_request_id_and_logs_actual_body_bytes(caplog):
    request_id = "1234567890abcdef1234567890abcdef"
    progress = 'data: {"type": "progress", "percent": 100, "message": "完成"}\n\n'.encode()
    complete = 'data: {"type": "complete", "result": {"text": "牌谱"}}\n\n'.encode()
    sent = []

    async def inner(scope, receive, send):
        assert scope["state"]["sse_request_id"] == request_id
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": progress, "more_body": True})
        await send({"type": "http.response.body", "body": complete, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        await asyncio.sleep(0)
        sent.append(message)

    middleware = server_app._SSESendMetricsMiddleware(inner)
    scope = {"type": "http", "method": "POST", "path": "/api/analyze/stream",
             "headers": [(b"x-request-id", request_id.encode())]}
    with caplog.at_level(logging.INFO, logger="majsoul-analyzer"):
        asyncio.run(middleware(scope, None, send))

    assert (b"x-request-id", request_id.encode()) in sent[0]["headers"]
    assert sent[1]["body"] == progress
    assert sent[2]["body"] == complete
    lines = [record.getMessage() for record in caplog.records if "[Paipu SSE Backend]" in record.getMessage()]
    assert len(lines) == 2
    assert all(f"请求ID={request_id}" in line for line in lines)
    assert f"事件=100% 字节数={len(progress)}" in lines[0]
    assert f"事件=complete 字节数={len(complete)}" in lines[1]
    assert "牌谱" not in " ".join(lines)
    assert "100%发送完成至complete发送开始=N/A" not in lines[1]


def test_asgi_replaces_invalid_request_ids():
    ids = []

    async def inner(scope, receive, send):
        ids.append(scope["state"]["sse_request_id"])

    async def send(message):
        pass

    middleware = server_app._SSESendMetricsMiddleware(inner)
    for value in (b"unsafe\nsecret", b"ABCDEF", b"1234567890abcdef1234567890abcdef"):
        scope = {"type": "http", "method": "POST", "path": "/api/analyze/stream",
                 "headers": [(b"x-request-id", value)]}
        asyncio.run(middleware(scope, None, send))
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in ids)
    assert ids[0] != ids[1]
    assert ids[2] == "1234567890abcdef1234567890abcdef"
    assert server_app.app.user_middleware[0].cls is server_app._SSESendMetricsMiddleware
