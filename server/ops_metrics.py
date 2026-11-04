"""Bounded, process-local latency summaries for authenticated diagnostics.

No UUIDs, IP addresses, player names, input text or timestamps are retained.
"""
from __future__ import annotations

from collections import deque
import math
import os
from pathlib import Path
import threading
import time
from typing import Any

RECENT_LIMIT = 256
_samples: deque[dict[str, Any]] = deque(maxlen=RECENT_LIMIT)
_lock = threading.Lock()
_cpu_lock = threading.Lock()
_cpu_previous: tuple[float, int, int] | None = None


def _number(value):
    return float(value) if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None


def _counter(value):
    return value if type(value) is int and value >= 0 else 0


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
    counts = report.get("计数")
    count_values = counts if isinstance(counts, dict) else {}
    status = report.get("状态")
    status_values = status if isinstance(status, dict) else {}
    stages = {
        name: value for name, raw in timing_values.items()
        if name != "雀魂下载总耗时" and (value := _number(raw)) is not None
    }
    counters = {
        name: _counter(value) for name, value in count_values.items()
        if type(value) is int and value >= 0
    }
    sample = {
        "total": total,
        "queue": _number(timing_values.get("AI推理排队等待")),
        "onnx": _number(timing_values.get("ONNX实际推理耗时")),
        "fetch": _number(timing_values.get("原始牌谱获取总耗时")),
        "convert": _number(timing_values.get("MJAI转换")),
        "serialize": _number(timing_values.get("完成结果序列化耗时")),
        "succeeded": succeeded if type(succeeded) is bool else None,
        "result_kb": _number(size_values.get("原始检讨JSON大小")),
        "stages": stages,
        "counters": counters,
        "fetch_mode": status_values.get("牌谱获取方式"),
        "cache_result": status_values.get("分析结果缓存"),
        "local_record_hit": status_values.get("是否命中本地记录"),
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


def _counter_total(rows: list[dict[str, Any]], name: str) -> int:
    return sum(_counter(row.get("counters", {}).get(name)) for row in rows)


def _rate(hits: int, checks: int) -> float | None:
    return round(hits / checks, 4) if checks > 0 else None


def memory_usage() -> dict:
    """Return bounded process memory and CPU samples without external dependencies.

    CPU percentages are averaged between consecutive trusted /api/status polls.
    process_cpu_cores_percent uses one logical CPU as 100%; it may exceed 100.
    process_cpu_machine_percent is normalized to total machine capacity.
    """
    current = None
    peak = None
    cpu_cores_pct = None
    cpu_machine_pct = None
    cpu_window_ms = None

    try:
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        current = resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        pass

    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                peak = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass

    global _cpu_previous
    try:
        stat_line = Path("/proc/self/stat").read_text(encoding="ascii")
        close_paren = stat_line.rfind(")")
        fields = stat_line[close_paren + 2:].split()
        if close_paren < 0 or len(fields) <= 12:
            raise ValueError("invalid process stat")
        process_ticks = int(fields[11]) + int(fields[12])

        cpu_line = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()
        if cpu_line[0] != "cpu" or len(cpu_line) < 5:
            raise ValueError("invalid system cpu stat")
        # user, nice, system, idle, iowait, irq, softirq and steal.
        total_ticks = sum(int(value) for value in cpu_line[1:9])
        now = time.monotonic()
        with _cpu_lock:
            previous = _cpu_previous
            _cpu_previous = (now, process_ticks, total_ticks)
            if previous is not None:
                elapsed = now - previous[0]
                delta_process = process_ticks - previous[1]
                delta_total = total_ticks - previous[2]
                if elapsed > 0 and delta_process >= 0 and delta_total > 0:
                    cpu_count = max(1, os.cpu_count() or 1)
                    cpu_cores_pct = round(delta_process / delta_total * cpu_count * 100, 2)
                    cpu_machine_pct = round(delta_process / delta_total * 100, 2)
                    cpu_window_ms = round(elapsed * 1000, 1)
    except (OSError, ValueError, IndexError, ZeroDivisionError):
        pass

    return {
        "rss_bytes": current,
        "rss_peak_bytes": peak,
        "process_cpu_cores_percent": cpu_cores_pct,
        "process_cpu_machine_percent": cpu_machine_pct,
        "cpu_sample_window_ms": cpu_window_ms,
        "cpu_sample_scope": "自上一次可信 /api/status 采样以来；首次采样为null",
    }


def summary():
    with _lock:
        rows = list(_samples)

    stage_names = sorted({name for row in rows for name in row.get("stages", {})})
    stage_ms = {}
    for name in stage_names:
        values = [row.get("stages", {}).get(name) for row in rows]
        valid = [value for value in values if value is not None]
        stage_ms[name] = {
            "sample_count": len(valid),
            "p50": _percentile(valid, 0.50),
            "p95": _percentile(valid, 0.95),
        }

    public_checks = _counter_total(rows, "公开牌谱缓存检查次数")
    public_hits = _counter_total(rows, "公开牌谱缓存命中次数")
    public_misses = _counter_total(rows, "公开牌谱缓存未命中次数")
    review_checks = _counter_total(rows, "检讨结果缓存检查次数")
    review_hits = _counter_total(rows, "检讨结果缓存命中次数")
    review_misses = _counter_total(rows, "检讨结果缓存未命中次数")
    local_hits = sum(row.get("local_record_hit") is True for row in rows)
    local_misses = sum(row.get("local_record_hit") is False for row in rows)

    return {
        "sample_count": len(rows),
        "sample_window": RECENT_LIMIT,
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
        "stage_ms": stage_ms,
        "acquisition": {
            "remote_downloads": sum(row.get("fetch_mode") == "雀魂下载" for row in rows),
            "remote_download_attempts": _counter_total(rows, "雀魂实际下载次数"),
            "local_reads": sum(row.get("fetch_mode") in (
                "本地直接读取", "进程内锁后读取本地", "文件锁后读取本地"
            ) for row in rows),
            "local_record_hits": local_hits,
            "local_record_misses": local_misses,
            "local_record_read_operations": _counter_total(rows, "本地牌谱命中次数"),
            "local_record_miss_checks": _counter_total(rows, "本地牌谱未命中次数"),
            "concurrent_download_waits": sum(row.get("fetch_mode") == "等待并发任务" for row in rows),
            "concurrent_download_reuses": _counter_total(rows, "牌谱并发下载复用次数"),
        },
        "cache": {
            "public_review": {
                "checks": public_checks, "hits": public_hits, "misses": public_misses,
                "hit_rate": _rate(public_hits, public_checks),
            },
            "review_result": {
                "checks": review_checks, "hits": review_hits, "misses": review_misses,
                "hit_rate": _rate(review_hits, review_checks),
            },
        },
        "account_sessions": {
            "login_attempts": _counter_total(rows, "雀魂登录请求次数"),
            "login_successes": _counter_total(rows, "雀魂登录成功次数"),
            "session_reuses": _counter_total(rows, "雀魂会话复用次数"),
            "websocket_connect_attempts": _counter_total(rows, "WebSocket连接尝试次数"),
        },
    }
