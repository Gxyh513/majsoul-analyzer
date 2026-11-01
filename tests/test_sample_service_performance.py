"""Authenticated status sampling is read-only, bounded and never prints secrets."""
import json

import httpx
import pytest

from scripts.sample_service_performance import collect_performance, STAGES


def _status(n=3, rss=4096, waiting=1):
    return {
        "ok": True, "user": "private-nickname",
        "engine": {"model_ready": True, "token": "private-password"},
        "storage": {"low_disk_space": False, "bytes": 1024, "path": "/private/path"},
        "performance_recent": {
            "sample_count": n, "successful": n, "failed": 0,
            "memory": {"rss_bytes": rss, "account": "private"},
            "queue": {"waiting": waiting, "inference_busy": False},
            **{name: {"p50": 20, "p95": 40, "private": "sensitive"} for name in STAGES},
        },
    }


def test_three_bounded_snapshots_do_not_leak_identity_or_credentials():
    requests = []
    sleeps = []
    def response(request):
        assert request.method == "GET" and request.url.path == "/api/status"
        assert request.headers["X-Api-Token"] == "private-api-token"
        requests.append(request)
        return httpx.Response(200, json=_status(len(requests), len(requests) * 4096, len(requests)))

    with httpx.Client(base_url="https://example.org", transport=httpx.MockTransport(response)) as client:
        code, report = collect_performance(
            "https://example.org", "private-api-token", samples=3,
            interval_seconds=5, client=client, sleep=sleeps.append,
        )
    assert code == 0 and report["observations"] == 3
    assert report["latest"]["latency_ms"]["total_ms"] == {"p50": 20, "p95": 40}
    assert report["max_observed_rss_bytes"] == 3 * 4096
    assert report["max_observed_queue_waiting"] == 3
    assert sleeps == [5, 5] and len(requests) == 3
    serialized = json.dumps(report)
    assert "private" not in serialized and "sensitive" not in serialized
    assert "password" not in serialized and "token" not in serialized
    assert "example.org" not in serialized


def test_reject_missing_trusted_metrics_and_no_recent_requests():
    with httpx.Client(base_url="https://example.org", transport=httpx.MockTransport(
        lambda _req: httpx.Response(200, json={"ok": True, "engine": {"model_ready": True}}),
    )) as client:
        code, report = collect_performance("https://example.org", "secret", client=client)
    assert (code, report) == (1, {"status": "untrusted_or_missing_metrics"})

    with httpx.Client(base_url="https://example.org", transport=httpx.MockTransport(
        lambda _req: httpx.Response(200, json=_status(n=0, rss=None, waiting=0)),
    )) as client:
        code, report = collect_performance("https://example.org", "secret", client=client)
    assert code == 0 and report["status"] == "no_recent_requests"
    assert report["max_observed_rss_bytes"] is None
    assert report["latest"]["recent_request_count"] == 0


@pytest.mark.parametrize("samples,interval", [(0, 30), (13, 30), (2, 1), (2, -1), (1, float("nan"))])
def test_restrict_sampling_cost(samples, interval):
    with pytest.raises(ValueError):
        collect_performance("https://example.org", "secret", samples=samples, interval_seconds=interval)


def test_prevent_insecure_remote_token_transport():
    with pytest.raises(ValueError):
        collect_performance("http://example.org", "secret")
    with pytest.raises(ValueError):
        collect_performance("https://example.org?token=secret", "secret")


def test_do_not_echo_upstream_error_body():
    with httpx.Client(base_url="https://example.org", transport=httpx.MockTransport(
        lambda _req: httpx.Response(503, json={"detail": "private-token"}),
    )) as client:
        assert collect_performance("https://example.org", "secret", client=client) == (
            1, {"status": "unavailable"},
        )
