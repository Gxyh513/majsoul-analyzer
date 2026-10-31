import asyncio
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import zlib

import pytest

from server import app as server_app


@pytest.mark.parametrize("accept_encoding", ["identity", "gzip"])
def test_asgi_send_uses_one_safe_request_id_and_logs_actual_body_bytes(caplog, accept_encoding):
    request_id = "1234567890abcdef1234567890abcdef"
    progress = 'data: {"type": "progress", "percent": 100, "message": "完成"}\n\n'.encode()
    complete = ("data: " + json.dumps({"type": "complete", "result": {"text": "牌谱" * 30000}},
                                     ensure_ascii=False) + "\n\n").encode()
    sent = []
    stats = {}

    async def inner(scope, receive, send):
        assert scope["state"]["sse_request_id"] == request_id
        scope["state"]["sse_performance_stats"] = stats
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": progress, "more_body": True})
        await send({"type": "http.response.body", "body": complete, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        await asyncio.sleep(0)
        sent.append(message)

    middleware = server_app._SSESendMetricsMiddleware(inner)
    scope = {"type": "http", "method": "POST", "path": "/api/analyze/stream",
             "headers": [(b"x-request-id", request_id.encode()),
                         (b"accept-encoding", accept_encoding.encode())]}
    with caplog.at_level(logging.INFO, logger="majsoul-analyzer"):
        asyncio.run(middleware(scope, None, send))

    assert (b"x-request-id", request_id.encode()) in sent[0]["headers"]
    assert len(sent[2:-1]) > 1, "large complete frames should be sent in slices"
    bodies = [message["body"] for message in sent[1:]]
    if accept_encoding == "gzip":
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = [inflater.decompress(body) for body in bodies]
        assert inflater.eof and not inflater.unused_data
    else:
        decoded = bodies
    assert decoded[0] == progress
    assert b"".join(decoded[1:]) == complete
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("[Paipu SSE Backend] ")]
    logs = [json.loads(line.removeprefix("[Paipu SSE Backend] ")) for line in lines]
    assert len(logs) == 2
    assert [entry["事件"] for entry in logs] == ["100%", "complete"]
    assert all(entry["请求ID"] == request_id and entry["状态"] == "完成" for entry in logs)
    assert logs[0]["传输大小_KB"] == round(len(bodies[0]) / 1024, 2)
    assert logs[1]["传输大小_KB"] == round(sum(map(len, bodies[1:-1])) / 1024, 2)
    assert stats["complete传输字节数"] == sum(map(len, bodies[1:-1]))
    assert stats["complete ASGI发送开始时间"] == logs[1]["ASGI发送开始时间_ms"]
    assert stats["complete ASGI发送完成时间"] == logs[1]["ASGI发送完成时间_ms"]
    assert stats["complete ASGI发送等待耗时"] == logs[1]["ASGI发送等待_ms"]
    assert 0 <= logs[1]["ASGI发送开始时间_ms"] <= logs[1]["ASGI发送完成时间_ms"]
    assert logs[1]["100%发送完成至complete发送开始间隔_ms"] >= 0
    assert "牌谱牌谱" not in " ".join(lines)


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


@pytest.mark.parametrize("filename", ["test_sse_telemetry.mjs", "test_sse_browser_performance.mjs"])
def test_sse_node_suites(filename):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed")
    proc = subprocess.run([node, "--test", str(Path(__file__).with_name(filename))],
                          capture_output=True, text=True, encoding="utf8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
