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
