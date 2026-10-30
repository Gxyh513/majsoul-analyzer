"""Request-scoped performance measurements for paipu processing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time
from typing import Any


performance: ContextVar[dict[str, Any] | None] = ContextVar("paipu_performance", default=None)


@contextmanager
def measure(stage: str):
    stats = performance.get()
    if stats is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        stats[stage] = stats.get(stage, 0.0) + (time.perf_counter() - started) * 1000
