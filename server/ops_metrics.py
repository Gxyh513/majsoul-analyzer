"""Bounded, process-local latency summaries for authenticated diagnostics.

No UUIDs, IP addresses, player names, input text or timestamps are retained.
"""
from __future__ import annotations

from collections import deque
import math
import os
from pathlib import Path
import threading

RECENT_LIMIT = 256
_samples: deque[dict[str, float | None]] = deque(maxlen=RECENT_LIMIT)
_lock = threading.Lock()


def _number(value):
    return float(value) if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None


def record(report: dict | None, *, succeeded: bool | None = None):
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
        "fetch": _number(timing_values.get("原始牌谱获取总耗时")),
        "convert": _number(timing_values.get("MJAI转换")),
        "serialize": _number(timing_values.get("完成结果序列化耗时")),
        "succeeded": succeeded if type(succeeded) is bool else None,
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


def memory_usage() -> dict:
    """Linux /proc process memory only; never expose pid, paths or user inputs."""
    try:
        # statm: total, resident, shared, text, lib, data, dirty (pages).
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        current = resident_pages * os.sysconf("SC_PAGE_SIZE")
        if current < 0:
            raise ValueError("invalid RSS")
    except (OSError, ValueError, IndexError):
        current = None
    return {"rss_bytes": current}


def summary():
    with _lock:
        rows = list(_samples)
    return {
        "sample_count": len(rows),
        "sample_window": _samples.maxlen or RECENT_LIMIT,
        "successful": sum(r.get("succeeded") is True for r in rows),
        "failed": sum(r.get("succeeded") is False for r in rows),
        "memory": memory_usage(),
        "total_ms": {"p50": _percentile((r["total"] for r in rows), 0.50),
                     "p95": _percentile((r["total"] for r in rows), 0.95)},
        "queue_ms": {"p50": _percentile((r["queue"] for r in rows), 0.50),
                     "p95": _percentile((r["queue"] for r in rows), 0.95)},
        "onnx_ms": {"p50": _percentile((r["onnx"] for r in rows), 0.50),
                    "p95": _percentile((r["onnx"] for r in rows), 0.95)},
        "fetch_ms": {"p50": _percentile((r["fetch"] for r in rows), 0.50),
                     "p95": _percentile((r["fetch"] for r in rows), 0.95)},
        "convert_ms": {"p50": _percentile((r["convert"] for r in rows), 0.50),
                       "p95": _percentile((r["convert"] for r in rows), 0.95)},
        "serialize_ms": {"p50": _percentile((r["serialize"] for r in rows), 0.50),
                         "p95": _percentile((r["serialize"] for r in rows), 0.95)},
        "json_kb": {"p50": _percentile((r["result_kb"] for r in rows), 0.50),
                    "p95": _percentile((r["result_kb"] for r in rows), 0.95)},
    }
