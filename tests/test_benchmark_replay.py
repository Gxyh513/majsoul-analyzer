"""Snapshot allocation benchmark only reports aggregate numeric diagnostics."""
from scripts.benchmark_replay import measure_review


def test_payload_measurement_uses_complete_json_without_sensitive_fields():
    data = {"names": ["private-player"], "kyokus": [{"steps": [{"event": {"type": "dahai"}}]}]}
    report = measure_review(data, 4096, 1.23)
    assert report["steps"] == 1
    assert report["json_bytes"] >= report["gzip_bytes"] or report["json_bytes"] < 100
    assert report["peak_tracemalloc_bytes"] == 4096
    assert report["analysis_s"] == 1.23
    assert "private-player" not in str(report)
