"""Request-scoped performance measurements for paipu processing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from typing import Any


performance: ContextVar[dict[str, Any] | None] = ContextVar("paipu_performance", default=None)


def _stats_lock(stats: dict[str, Any]):
    lock = stats.get("_lock")
    if lock is None:
        lock = stats.setdefault("_lock", threading.Lock())
    return lock


def set_value(name: str, value: Any) -> None:
    stats = performance.get()
    if stats is not None:
        with _stats_lock(stats):
            stats[name] = value


def get_value(name: str) -> Any:
    stats = performance.get()
    if stats is not None:
        with _stats_lock(stats):
            return stats.get(name)
    return None


def snapshot(stats: dict[str, Any]) -> tuple[dict[str, Any], float]:
    with _stats_lock(stats):
        now = time.perf_counter()
        result = stats.copy()
        result["_active_stages"] = {
            stage: tuple(starts) for stage, starts in stats.get("_active_stages", {}).items()
        }
    return result, now


@contextmanager
def measure(stage: str, *, missing_stage: str | None = None, failure_count: str | None = None):
    stats = performance.get()
    if stats is None:
        yield
        return
    lock = _stats_lock(stats)
    with lock:
        started = time.perf_counter()
        active = stats.setdefault("_active_stages", {})
        active.setdefault(stage, []).append(started)
        if failure_count is not None:
            stats.setdefault(failure_count, 0)
    recorded_stage = stage
    failed = False
    try:
        yield
    except FileNotFoundError:
        if missing_stage is not None:
            recorded_stage = missing_stage
        else:
            failed = True
        raise
    except Exception:
        failed = True
        raise
    finally:
        finished = time.perf_counter()
        with lock:
            stats[recorded_stage] = stats.get(recorded_stage, 0.0) + (finished - started) * 1000
            if failed and failure_count is not None:
                stats[failure_count] += 1
                stats["最后失败阶段"] = stage
            active[stage].remove(started)
            if not active[stage]:
                del active[stage]
