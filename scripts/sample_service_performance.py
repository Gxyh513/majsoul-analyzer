"""Read-only, bounded snapshots of authenticated production performance telemetry.

Never sends review requests, prints credentials, or stores private user data.
Reported percentiles are server process-local rolling samples, not all-time SLAs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time

import httpx

from scripts.check_service_health import validate_url


STAGES = ("total_ms", "queue_ms", "onnx_ms", "fetch_ms", "convert_ms", "serialize_ms")


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _snapshot(body):
    if not isinstance(body, dict) or body.get("ok") is not True:
        return None
    engine, storage, recent = body.get("engine"), body.get("storage"), body.get("performance_recent")
    if not all(isinstance(item, dict) for item in (engine, storage, recent)):
        return None  # Anonymous status does not contain trusted performance metrics.
    ready, low = engine.get("model_ready"), storage.get("low_disk_space")
    if type(ready) is not bool or type(low) is not bool:
        return None
    size = _count(recent.get("sample_count"))
    if size is None:
        return None
    queue, memory = recent.get("queue"), recent.get("memory")
    if not isinstance(queue, dict) or not isinstance(memory, dict):
        return None
    if type(queue.get("inference_busy")) is not bool:
        return None
    stages = {}
    for name in STAGES:
        values = recent.get(name)
        if not isinstance(values, dict):
            return None
        stages[name] = {key: _number(values.get(key)) for key in ("p50", "p95")}
    return {
        "recent_request_count": size,
        "recent_successful": _count(recent.get("successful")),
        "recent_failed": _count(recent.get("failed")),
        "latency_ms": stages,
        "queue_waiting": _count(queue.get("waiting")),
        "inference_busy": queue["inference_busy"],
        "rss_bytes": _number(memory.get("rss_bytes")),
        "cache_bytes": _number(storage.get("bytes")),
        "low_disk_space": low,
        "model_ready": ready,
    }


def collect_performance(base_url: str, token: str, *, samples: int = 1,
                        interval_seconds: float = 30, client: httpx.Client | None = None,
                        sleep=time.sleep):
    """Return only an allowlisted aggregate report, never the raw HTTP payload."""
    validate_url(base_url)
    if (type(samples) is not int or not 1 <= samples <= 12
            or type(interval_seconds) not in (float, int)
            or not math.isfinite(interval_seconds)
            or interval_seconds < (5 if samples > 1 else 0)
            or interval_seconds > 600):
        raise ValueError("采样次数或间隔超出限制")

    owned = client is None
    if owned:
        client = httpx.Client(base_url=base_url, timeout=10, trust_env=False)
    readings = []
    try:
        for index in range(samples):
            if index:
                sleep(interval_seconds)
            response = client.get("/api/status", headers={"X-Api-Token": token} if token else {})
            if response.status_code != 200:
                return 1, {"status": "unavailable"}
            snapshot = _snapshot(response.json())
            if snapshot is None:
                return 1, {"status": "untrusted_or_missing_metrics"}
            readings.append(snapshot)
    except (httpx.HTTPError, ValueError):
        return 1, {"status": "unavailable"}
    finally:
        if owned:
            client.close()

    latest = readings[-1]
    rss = [item["rss_bytes"] for item in readings if item["rss_bytes"] is not None]
    waiters = [item["queue_waiting"] for item in readings if item["queue_waiting"] is not None]
    return 0, {
        "status": "ok" if latest["recent_request_count"] else "no_recent_requests",
        "observations": len(readings),
        "latest": latest,
        "max_observed_rss_bytes": max(rss) if rss else None,
        "max_observed_queue_waiting": max(waiters) if waiters else None,
        "scope": "每次读取当前进程最近最多256个请求的滚动指标；采样RSS不是内存峰值；不含CPU峰值。",
    }


def main():
    parser = argparse.ArgumentParser(description="只读采样生产后端滚动性能指标（不发起推理）")
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=30)
    args = parser.parse_args()
    try:
        code, report = collect_performance(
            args.url, os.environ.get("MAJSOUL_API_TOKEN", ""),
            samples=args.samples, interval_seconds=args.interval_seconds,
        )
    except ValueError:
        code, report = 1, {"status": "invalid_parameters"}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
