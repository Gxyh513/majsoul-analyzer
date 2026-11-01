"""Small, explicit localhost-only AI review load baseline (no user records stored)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from urllib.parse import urlsplit

import httpx


def _percentile(values: list[float], portion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * portion
    index = int(position)
    return round(ordered[index] + (ordered[min(len(ordered) - 1, index + 1)] - ordered[index])
                 * (position - index), 2)


def _validate(base_url: str, total: int, concurrency: int):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("只允许本机 HTTP 服务，防止对公开网站执行压测")
    if not (1 <= total <= 32 and 1 <= concurrency <= 4 and concurrency <= total):
        raise ValueError("最多32次请求，最多4个并发，且并发数不能超过请求数")


async def benchmark(base_url: str, total: int, concurrency: int, *, token: str = "",
                    client: httpx.AsyncClient | None = None) -> dict:
    _validate(base_url, total, concurrency)
    sem = asyncio.Semaphore(concurrency)
    records: list[tuple[float, int | None]] = []

    async def run_one(http: httpx.AsyncClient):
        async with sem:
            started = time.perf_counter()
            status = None
            try:
                response = await http.post("/api/analyze/demo", json={"target_actor": 0},
                                           headers={"X-Api-Token": token} if token else {})
                status = response.status_code
            except httpx.RequestError:
                pass
            records.append(((time.perf_counter() - started) * 1000, status))

    started = time.perf_counter()
    if client is not None:
        await asyncio.gather(*(run_one(client) for _ in range(total)))
    else:
        async with httpx.AsyncClient(base_url=base_url, timeout=180.0, trust_env=False,
                                     limits=httpx.Limits(max_connections=concurrency)) as http:
            await asyncio.gather(*(run_one(http) for _ in range(total)))
    elapsed = time.perf_counter() - started
    successes = [ms for ms, status in records if status is not None and 200 <= status < 300]
    return {"requests": total, "concurrency": concurrency,
            "completed_2xx": len(successes), "failed": total - len(successes),
            "duration_s": round(elapsed, 3),
            "throughput_rps": round(total / elapsed, 3) if elapsed else None,
            "latency_ms": {"p50": _percentile(successes, 0.5),
                           "p95": _percentile(successes, 0.95)},
            "note": "内置演示测试不包含牌谱下载，不代表生产公开牌谱链路的吞吐量"}


def main():
    parser = argparse.ArgumentParser(description="仅对本机服务做有上限的演示牌谱性能基准")
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.url, args.requests, args.concurrency,
                                   token=os.environ.get("MAJSOUL_API_TOKEN", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failed"]:
        raise SystemExit("本机演示牌谱基准存在失败请求")


if __name__ == "__main__":
    main()
