"""Health-check output is aggregate-only and rejects plaintext remote token transport."""
import httpx
import pytest

from scripts.check_service_health import check_health, inspect_status, validate_url


@pytest.mark.parametrize("url", [
    "http://example.org", "http://example.org:7860", "https://alice:secret@example.org",
    "https://example.org/api/status", "https://example.org?token=secret",
])
def test_prevent_untrusted_health_monitor_url(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_readonly_health_states_and_privacy():
    for ready, low, code in [(True, False, 0), (True, True, 2), (False, False, 2)]:
        def response(request):
            assert request.headers["X-Api-Token"] == "private-token"
            assert request.method == "GET" and request.url.path == "/api/status"
            return httpx.Response(200, json={
                "ok": True, "storage": {"low_disk_space": low, "account": "private"},
                "engine": {"model_ready": ready, "credentials": "private"},
                "user": "private",
            })
        with httpx.Client(base_url="https://example.org",
                          transport=httpx.MockTransport(response)) as client:
            status, report = check_health("https://example.org", "private-token", client=client)
        assert status == code
        assert "private" not in str(report)
        assert report["low_disk_space"] is low
        assert report["model_ready"] is ready


def test_anonymous_or_bad_status_is_not_reported_as_healthy():
    assert inspect_status({"ok": True, "engine": {"model_ready": True}})[0] == 1
    assert inspect_status({"ok": True, "engine": {}, "storage": {}})[0] == 1
    with httpx.Client(base_url="https://example.org",
                      transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"token": "private"}))) as client:
        status, report = check_health("https://example.org", "private-token", client=client)
    assert status == 1 and report == {"status": "unavailable"}
