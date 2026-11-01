"""Process-local telemetry keeps only numeric, bounded percentiles."""
from collections import deque

from server import ops_metrics


def test_latency_percentiles_are_bounded_and_numeric(monkeypatch):
    monkeypatch.setattr(ops_metrics, "_samples", deque(maxlen=3))
    for t in [10, 20, 30, 40]:
        ops_metrics.record({
            "请求ID": "sensitive", "总耗时_ms": t,
            "耗时_ms": {"AI推理排队等待": t / 2, "ONNX实际推理耗时": t / 4},
            "数据大小_KB": {"原始检讨JSON大小": t * 2},
        })
    summary = ops_metrics.summary()
    assert summary["sample_count"] == 3
    assert summary["total_ms"]["p50"] == 30
    assert summary["total_ms"]["p95"] == 39
    assert summary["queue_ms"]["p50"] == 15
    assert summary["json_kb"]["p50"] == 60
    assert "sensitive" not in str(summary)


def test_invalid_and_missing_values_do_not_poison_metrics(monkeypatch):
    monkeypatch.setattr(ops_metrics, "_samples", deque(maxlen=5))
    ops_metrics.record(None)
    ops_metrics.record({"总耗时_ms": float("nan")})
    ops_metrics.record({"总耗时_ms": 3, "耗时_ms": {"AI推理排队等待": -1}})
    snap = ops_metrics.summary()
    assert snap["sample_count"] == 1
    assert snap["queue_ms"]["p95"] is None


def test_success_counts_and_stage_metrics_are_bounded(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(ops_metrics, "_samples", deque(maxlen=2))
    monkeypatch.setattr(ops_metrics, "memory_usage", lambda: {"rss_bytes": 4096})
    for ok, n in ((True, 10), (False, 20), (True, 30)):
        ops_metrics.record({
            "总耗时_ms": n,
            "请求ID": "secret-user-id",
            "耗时_ms": {"原始牌谱获取总耗时": n / 2,
                        "MJAI转换": n / 4,
                        "完成结果序列化耗时": n / 5},
        }, succeeded=ok)
    snap = ops_metrics.summary()
    assert snap["sample_count"] == 2
    assert (snap["successful"], snap["failed"]) == (1, 1)
    assert snap["fetch_ms"] == {"p50": 12.5, "p95": 14.75}
    assert snap["convert_ms"]["p50"] == 6.25
    assert snap["memory"] == {"rss_bytes": 4096}
    assert "secret-user-id" not in str(snap)


def test_rss_failure_is_nullable_without_sensitive_details(monkeypatch):
    from pathlib import Path
    def unavailable(self, *args, **kwargs):
        raise OSError("secret path")
    monkeypatch.setattr(Path, "read_text", unavailable)
    assert ops_metrics.memory_usage() == {"rss_bytes": None}
