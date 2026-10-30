import base64
import gzip
import json

from server import app as server_app
from server.paipu_performance import performance


def decode_event(frame):
    return json.loads(frame.removeprefix("data: ").strip())


def test_complete_compression_requires_capability_and_preserves_result():
    result = {"game_id": "example", "kyokus": [{"text": "牌谱" * 40000}]}
    stats = {}
    token = performance.set(stats)
    try:
        raw = server_app._complete_review_event(result)
        assert decode_event(raw) == {"type": "complete", "result": result}
        assert stats["complete压缩状态"] == "未协商"
        assert stats["原始complete事件字节数"] == len(raw.encode("utf8"))

        compressed = server_app._complete_review_event(result, True)
        event = decode_event(compressed)
        assert event["type"] == "complete"
        assert event["encoding"] == "gzip+base64"
        assert json.loads(gzip.decompress(base64.b64decode(event["data"]))) == result
        assert stats["complete压缩状态"] == "成功"
        assert stats["gzip压缩后字节数"] == len(base64.b64decode(event["data"]))
        assert stats["Base64编码后字节数"] == len(event["data"])
        assert stats["complete传输字节数"] == len(compressed.encode("utf8"))
        assert len(compressed.encode("utf8")) < len(raw.encode("utf8"))
    finally:
        performance.reset(token)


def test_small_results_and_failed_or_larger_compression_fall_back(monkeypatch):
    small = {"text": "小结果"}
    assert decode_event(server_app._complete_review_event(small, True)) == {"type": "complete", "result": small}
    result = {"text": "牌谱" * 40000}
    stats = {}
    token = performance.set(stats)
    try:
        def fail(*args, **kwargs):
            raise OSError("compression failed")
        monkeypatch.setattr(server_app.gzip, "compress", fail)
        assert decode_event(server_app._complete_review_event(result, True))["result"] == result
        assert stats["complete压缩状态"] == "压缩失败，回退原始"

        monkeypatch.setattr(server_app.gzip, "compress", lambda data, **kwargs: data)
        assert decode_event(server_app._complete_review_event(result, True))["result"] == result
        assert stats["complete压缩状态"] == "压缩后未变小，回退原始"
    finally:
        performance.reset(token)


def test_compression_is_opt_in_for_older_clients():
    assert server_app.AnalyzeStreamReq(paipu="demo").accept_compression is None
    assert server_app.AnalyzeStreamReq(paipu="demo", accept_compression="gzip+base64").accept_compression == "gzip+base64"
