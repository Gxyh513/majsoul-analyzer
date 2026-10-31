import asyncio
import codecs
import gzip
import json
import zlib

import pytest

from server import app as server_app
from server.paipu_performance import performance


STREAM_PATH = "/api/analyze/stream"


def decode_event(frame):
    return json.loads(frame.removeprefix("data: ").strip())


def event_frame(event):
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf8")


def capture_stream(frames, accept_encoding=None, *, status=200, content_type=b"text/event-stream", encoding=b"identity"):
    async def scenario():
        sent = []

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", content_type), (b"content-encoding", encoding),
                (b"content-length", b"999"), (b"vary", b"Origin"),
            ]})
            for frame in frames:
                await send({"type": "http.response.body", "body": frame, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def send(message):
            sent.append(message)

        headers = [] if accept_encoding is None else [(b"accept-encoding", accept_encoding.encode("ascii"))]
        scope = {"type": "http", "method": "POST", "path": STREAM_PATH, "headers": headers}
        await server_app._SSESendMetricsMiddleware(inner)(scope, None, send)
        return sent

    return asyncio.run(scenario())


@pytest.mark.parametrize("text", ["小结果", "牌谱" * 40000])
def test_complete_event_is_plain_json_and_preserves_small_and_large_results(text):
    result = {"game_id": "example", "kyokus": [{"text": text}]}
    stats = {}
    token = performance.set(stats)
    try:
        frame = server_app._complete_review_event(result)
        assert decode_event(frame) == {"type": "complete", "result": result}
        assert stats["原始complete事件字节数"] == len(frame.encode("utf8"))
        assert stats["原始检讨JSON字节数"] == len(json.dumps(result, ensure_ascii=False).encode("utf8"))
        assert "Base64编码后字节数" not in stats
    finally:
        performance.reset(token)


@pytest.mark.parametrize(("accept_encoding", "compressed"), [
    (None, False),
    ("identity", False),
    ("br, deflate", False),
    ("gzip", True),
    ("br, GZip;q=0.3", True),
    ("*;q=0.5", True),
    ("gzip;q=0", False),
    ("gzip;q=0, *;q=1", False),
    ("gzip;q=invalid", False),
    ("gzip;q=1.5", False),
])
@pytest.mark.parametrize("text", ["小结果", "牌谱" * 4000])
def test_http_gzip_negotiation_keeps_complete_result(accept_encoding, compressed, text):
    result = {"text": text, "nested": {"values": [1, 2, 3]}}
    frames = [event_frame({"type": "progress", "percent": 100, "message": "完成"}),
              event_frame({"type": "complete", "result": result})]
    sent = capture_stream(frames, accept_encoding)
    headers = sent[0]["headers"]
    encoding = next(value for key, value in headers if key.lower() == b"content-encoding")
    assert encoding == (b"gzip" if compressed else b"identity")
    assert any(key.lower() == b"vary" and b"accept-encoding" in value.lower() for key, value in headers)
    wire = b"".join(message["body"] for message in sent[1:])
    if compressed:
        assert not any(key.lower() == b"content-length" for key, _ in headers)
        decoded = gzip.decompress(wire)
    else:
        decoded = wire
    assert decoded == b"".join(frames)
    assert [decode_event(frame) for frame in decoded.decode("utf8").split("\n\n") if frame] == [
        {"type": "progress", "percent": 100, "message": "完成"},
        {"type": "complete", "result": result},
    ]


def test_gzip_flushes_each_utf8_event_before_the_stream_closes():
    async def scenario():
        result = {"text": "牌谱和🀄" * 8000, "nested": {"values": [1, 2, 3]}}
        expected = [
            {"type": "progress", "percent": 5, "message": "准备"},
            {"type": "progress", "percent": 100, "message": "完成"},
            {"type": "complete", "result": result},
        ]
        frames = [event_frame(event) for event in expected]
        outgoing = asyncio.Queue()
        acknowledged = asyncio.Queue()

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            for frame in frames:
                await send({"type": "http.response.body", "body": frame, "more_body": True})
                await acknowledged.get()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def send(message):
            await outgoing.put(message)

        scope = {"type": "http", "method": "POST", "path": STREAM_PATH,
                 "headers": [(b"accept-encoding", b"gzip")]}
        task = asyncio.create_task(server_app._SSESendMetricsMiddleware(inner)(scope, None, send))
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoder = codecs.getincrementaldecoder("utf8")()
        decoded_text = ""
        try:
            start = await asyncio.wait_for(outgoing.get(), 2)
            assert (b"content-encoding", b"gzip") in start["headers"]
            for index, frame in enumerate(frames):
                # Large complete events are themselves sent in independently flushed 64 KiB slices.
                slices = [frame[i:i + 65536] for i in range(0, len(frame), 65536)] if index == 2 else [frame]
                if index == 2:
                    assert len(slices) > 1
                for raw_slice in slices:
                    message = await asyncio.wait_for(outgoing.get(), 2)
                    assert message["more_body"] is True
                    decoded = b"".join(inflater.decompress(message["body"][i:i + 1])
                                       for i in range(len(message["body"])))
                    assert decoded == raw_slice, "every ASGI body must be flushed independently"
                    # Split UTF-8 at every byte as well, including inside multibyte characters.
                    decoded_text += "".join(decoder.decode(decoded[i:i + 1], final=False)
                                            for i in range(len(decoded)))
                    assert not inflater.eof and not task.done()
                assert [decode_event(item) for item in decoded_text.split("\n\n") if item] == expected[:index + 1]
                # In particular, the complete result is already available while the stream remains open.
                if index == len(frames) - 1:
                    assert decode_event(decoded_text.split("\n\n")[-2]) == {"type": "complete", "result": result}
                await acknowledged.put(None)
            final = await asyncio.wait_for(outgoing.get(), 2)
            assert final.get("more_body") is False
            decoded_text += decoder.decode(inflater.decompress(final["body"]), final=True)
            assert inflater.eof and not inflater.unused_data
            assert decoded_text == b"".join(frames).decode("utf8")
            await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_existing_gzip_is_not_encoded_twice():
    frame = event_frame({"type": "complete", "result": {"text": "牌谱"}})
    already_encoded = gzip.compress(frame)
    sent = capture_stream([already_encoded], "gzip", encoding=b"gzip")
    assert (b"content-encoding", b"gzip") in sent[0]["headers"]
    assert b"".join(message["body"] for message in sent[1:]) == already_encoded
    assert gzip.decompress(already_encoded) == frame


@pytest.mark.parametrize(("status", "content_type"), [(503, b"text/event-stream"), (200, b"application/json")])
def test_non_success_or_non_sse_response_is_not_compressed(status, content_type):
    body = b'{"detail":"failure"}'
    sent = capture_stream([body], "gzip", status=status, content_type=content_type)
    assert (b"content-encoding", b"identity") in sent[0]["headers"]
    assert b"".join(message["body"] for message in sent[1:]) == body


def test_compression_failure_does_not_send_a_false_or_uncompressed_complete(monkeypatch):
    factory = zlib.compressobj

    class FailingCompressor:
        def __init__(self, *args, **kwargs):
            self.inner = factory(*args, **kwargs)

        def compress(self, data):
            if data.startswith(b'data: {"type": "complete",'):
                raise OSError("compression failed")
            return self.inner.compress(data)

        def flush(self, mode):
            return self.inner.flush(mode)

    monkeypatch.setattr(server_app.zlib, "compressobj", FailingCompressor)
    progress = event_frame({"type": "progress", "percent": 100, "message": "完成"})
    complete = event_frame({"type": "complete", "result": {"text": "牌谱"}})
    sent = []

    async def scenario():
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            for body in (progress, complete):
                await send({"type": "http.response.body", "body": body, "more_body": True})

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": STREAM_PATH,
                 "headers": [(b"accept-encoding", b"gzip")]}
        await server_app._SSESendMetricsMiddleware(inner)(scope, None, send)

    with pytest.raises(OSError, match="compression failed"):
        asyncio.run(scenario())
    assert (b"content-encoding", b"gzip") in sent[0]["headers"]
    assert len(sent) == 2
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    assert inflater.decompress(sent[1]["body"]) == progress
    assert not inflater.eof
