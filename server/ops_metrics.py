"""Bounded, process-local latency summaries for authenticated diagnostics.

No UUIDs, IP addresses, player names, input text or timestamps are retained.
"""
from __future__ import annotations

from collections import deque
import math
import threading

RECENT_LIMIT = 256
_samples: deque[dict[str, float | None]] = deque(maxlen=RECENT_LIMIT)
_lock = threading.Lock()


def _number(value):
    return float(value) if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None


def record(report: dict | None):
    if not isinstance(report, dict):
        return
    total = _number(report.get("总耗时_ms"))
    if total is None:
        return
    timings = report.get("耗时_ms")
    timing_values = timings if isinstance(timings, dict) else {}
    sizes = report.get("数据大小_KB")
    size_values = sizes if isinstance(sizes, dict) else {}
    sample = {
        "total": total,
        "queue": _number(timing_values.get("AI推理排队等待")),
        "onnx": _number(timing_values.get("ONNX实际推理耗时")),
        "result_kb": _number(size_values.get("原始检讨JSON大小")),
    }
    with _lock:
        _samples.append(sample)


def _percentile(values, pct):
    nums = sorted(v for v in values if v is not None)
    if not nums:
        return None
    position = (len(nums) - 1) * pct
    left = int(position)
    right = min(len(nums) - 1, left + 1)
    return round(nums[left] + (nums[right] - nums[left]) * (position - left), 2)


def summary():
    with _lock:
        rows = list(_samples)
    return {
        "sample_count": len(rows),
        "sample_window": RECENT_LIMIT,
        "total_ms": {"p50": _percentile((r["total"] for r in rows), 0.50),
                     "p95": _percentile((r["total"] for r in rows), 0.95)},
        "queue_ms": {"p50": _percentile((r["queue"] for r in rows), 0.50),
                     "p95": _percentile((r["queue"] for r in rows), 0.95)},
        "onnx_ms": {"p50": _percentile((r["onnx"] for r in rows), 0.50),
                    "p95": _percentile((r["onnx"] for r in rows), 0.95)},
        "json_kb": {"p50": _percentile((r["result_kb"] for r in rows), 0.50),
                    "p95": _percentile((r["result_kb"] for r in rows), 0.95)},
    }
