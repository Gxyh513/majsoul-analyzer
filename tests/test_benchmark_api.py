"""Local benchmark is bounded and never accepts an external target."""
import asyncio

import httpx
import pytest

from scripts.benchmark_api import _validate, _percentile, benchmark


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:7860", "http://example.com", "http://127.0.0.1:7860/api",
    "http://localhost:7860/?token=secret", "http://user:pass@localhost:7860",
])
def test_refuse_external_or_sensitive_urls(url):
    with pytest.raises(ValueError):
        _validate(url, 4, 1)


def test_limits_and_interpolated_percentile():
    with pytest.raises(ValueError):
        _validate("http://127.0.0.1:7860", 50, 5)
    assert _percentile([10, 20, 30, 40], 0.95) == 38.5


def test_benchmark_records_only_numeric_aggregates():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        assert request.url.path == "/api/analyze/demo"
        assert request.headers["X-Api-Token"] == "test-token"
        return httpx.Response(200 if count % 2 else 503)

    async def run():
        async with httpx.AsyncClient(base_url="http://127.0.0.1:7860",
                                     transport=httpx.MockTransport(handler)) as client:
            return await benchmark("http://127.0.0.1:7860", 4, 1,
                                   token="test-token", client=client)

    report = asyncio.run(run())
    assert report["requests"] == 4 and report["concurrency"] == 1
    assert report["completed_2xx"] == report["failed"] == 2
    assert report["latency_ms"]["p95"] is not None
    assert "test-token" not in str(report)
