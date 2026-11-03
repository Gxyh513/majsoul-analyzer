"""Real raw-record fast cache: public-only, no protobuf decode on a hit."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server import record_store, review_cache
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA


UUID = "241026-11111111-2222-3333-4444-555555555555"
META = {"uuid": UUID, "account_ids": [11, 22, 33, 44], "names": ["A", "B", "C", "D"]}
RECORD = record_store.RawRecord("0.11.252.w", b"raw response bytes", b"detail bytes")


def review(seat=2):
    return {
        "game_id": UUID,
        "target_actor": seat,
        "meta": copy.deepcopy(META),
        "engine": {
            "type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
            "weight_sha256": MODEL_SHA256, "revision": MODEL_REVISION,
            "version": 4, "steps": None, "q_source": "neural_network",
            "review_schema": REVIEW_SCHEMA,
        },
        "summary": {"rating": 95},
        "kyokus": [],
    }


@pytest.fixture
def public_record(monkeypatch, tmp_path):
    records = tmp_path / "records"
    reviews = tmp_path / "reviews"
    record_store.save_record(records, UUID, RECORD)
    monkeypatch.setattr(server_app, "RECORDS_DIR", records)
    monkeypatch.setattr(server_app, "LEGACY_RECORDS_DIRS", ())
    monkeypatch.setattr(server_app, "REVIEWS_DIR", reviews)
    monkeypatch.setattr(server_app, "admin_manager", lambda: object())
    return records, reviews


def store_review(reviews: Path, seat=2, *, source=RECORD, engine="auto", temperature=0.1):
    digest = server_app._public_raw_source_digest(source)
    key = server_app._public_raw_review_key(UUID, digest, seat, engine, temperature)
    assert review_cache.save(reviews, key, review(seat))
    return key


def test_raw_cache_key_includes_source_seat_model_parameters_and_pipeline():
    digest = server_app._public_raw_source_digest(RECORD)
    key = server_app._public_raw_review_key(UUID, digest, 2, "auto", 0.1)
    assert len(key) == 64
    assert key != server_app._public_raw_review_key(UUID, digest, 1, "auto", 0.1)
    assert key != server_app._public_raw_review_key(UUID, digest, 2, MODEL_ID, 0.1)
    assert key != server_app._public_raw_review_key(UUID, digest, 2, "auto", 0.2)
    assert key != server_app._public_raw_review_key("different", digest, 2, "auto", 0.1)
    altered = record_store.RawRecord(RECORD.protocol_version, RECORD.response, b"changed detail")
    assert key != server_app._public_raw_review_key(
        UUID, server_app._public_raw_source_digest(altered), 2, "auto", 0.1
    )
    assert key != server_app._public_raw_review_key(
        UUID, server_app._public_raw_source_digest(record_store.RawRecord(
            "another-version", RECORD.response, RECORD.detail
        )), 2, "auto", 0.1
    )


def test_fast_hit_uses_url_account_seat_and_checks_result_meta(public_record, monkeypatch):
    _records, reviews = public_record
    store_review(reviews, seat=2)
    # URL suffix 33 identifies the third player. No request seat was supplied.
    assert server_app._load_fast_public_review(UUID + "_33", None, "auto", 0.1) == review()
    assert server_app._load_fast_public_review(UUID + "_44", None, "auto", 0.1) is None
    assert server_app._load_fast_public_review(UUID, 1, "auto", 0.1) is None
    assert server_app._load_fast_public_review(UUID, 2, "auto", 0.2) is None

    # A forged cached metadata seat selection must not override the URL.
    key = server_app._public_raw_review_key(
        UUID, server_app._public_raw_source_digest(RECORD), 2, "auto", 0.1
    )
    wrong = review()
    wrong["meta"]["account_ids"] = [11, 33, 22, 44]
    assert review_cache.save(reviews, key, wrong)
    assert server_app._load_fast_public_review(UUID + "_33", None, "auto", 0.1) is None


def test_fast_cache_rejects_changed_raw_bytes_and_missing_or_legacy_sources(public_record):
    records, reviews = public_record
    store_review(reviews, seat=2)
    assert server_app._load_fast_public_review(UUID, 2, "auto", 0.1) == review()
    changed = record_store.RawRecord(RECORD.protocol_version, b"updated response", RECORD.detail)
    (records / f"{UUID}.pb").write_bytes(record_store._encode(changed))
    assert server_app._load_fast_public_review(UUID, 2, "auto", 0.1) is None
    (records / f"{UUID}.pb").unlink()
    assert server_app._load_fast_public_review(UUID, 2, "auto", 0.1) is None
    assert server_app._load_fast_public_review("demo", 2, "auto", 0.1) is None
    assert server_app._load_fast_public_review("invalid", 2, "auto", 0.1) is None


@pytest.mark.parametrize("route", ["paipu", "stream"])
def test_public_api_fast_hit_skips_decode_convert_and_inference(public_record, monkeypatch, route):
    _records, reviews = public_record
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())
    monkeypatch.setattr(server_app, "_review_inflight", {})
    monkeypatch.setattr(server_app, "_review_waiters", 0)
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    monkeypatch.delenv("MAJSOUL_ALLOWED_ORIGINS", raising=False)

    fake_model = SimpleNamespace(weight_sha256=MODEL_SHA256)
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _model_id=None: (fake_model, MODEL_ID, Path("model.onnx")),
    ))
    decoded = Mock(return_value={"head": {"uuid": UUID}, "actions": []})
    converted = Mock(return_value=[{"type": "start_game", "names": ["A", "B", "C", "D"]}])
    meta = Mock(return_value=copy.deepcopy(META))
    inferred = Mock(side_effect=lambda events, seat, manager, **kwargs: review(seat))
    monkeypatch.setattr(server_app.ms_client, "decode_record", decoded)
    monkeypatch.setattr(server_app, "to_mjai", converted)
    monkeypatch.setattr(server_app, "game_meta", meta)
    monkeypatch.setattr(server_app, "review_game", inferred)

    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    payload = {"paipu": UUID + "_33", "target_actor": 2, "engine": "auto", "temperature": 0.1}

    def analyze():
        response = client.post("/api/analyze/" + route, json=payload, headers={"Accept-Encoding": "identity"})
        assert response.status_code == 200, response.text[:400]
        if route == "paipu":
            return response.json()
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert not [e for e in events if e.get("type") == "error"]
        completed = [e for e in events if e.get("type") == "complete"]
        assert len(completed) == 1
        perf = next(e["performance"] for e in events if e.get("type") == "performance")
        assert perf["状态"]["牌谱获取结果"] == "成功"
        assert perf["状态"]["牌谱获取方式"] == "本地直接读取"
        assert perf["状态"]["是否命中本地记录"] is True
        assert perf["数据大小_KB"]["原始数据大小"] is not None
        return completed[0]["result"]

    assert analyze() == review()
    assert decoded.call_count == converted.call_count == meta.call_count == inferred.call_count == 1
    assert review_cache.storage_metrics(reviews)["entries"] == 1

    # The second request reads the framed raw bytes and cache only. Do not call
    # the protobuf decoder, MJAI converter, inference, or downloader again.
    def forbid(*args, **kwargs):
        raise AssertionError("repeated protobuf conversion / inference / download")

    monkeypatch.setattr(server_app.ms_client, "decode_record", forbid)
    monkeypatch.setattr(server_app, "to_mjai", forbid)
    monkeypatch.setattr(server_app, "review_game", forbid)
    monkeypatch.setattr(server_app, "get_or_download_record", forbid)
    assert analyze() == review()
    assert review_cache.storage_metrics(reviews)["entries"] == 1


def test_no_fast_path_for_private_or_demo(public_record):
    _records, reviews = public_record
    store_review(reviews)
    assert server_app._load_fast_public_review(None, 2, "auto", 0.1) is None
    assert server_app._load_fast_public_review("demo-123", 2, "auto", 0.1) is None


def test_existing_event_key_cache_is_reused_and_promoted(public_record, monkeypatch):
    """Deploying the raw-key optimization must not waste a valid older review."""
    _records, reviews = public_record
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}]
    old_key = server_app._review_fingerprint(UUID, events, META, 2, "auto", 0.1)
    assert review_cache.save(reviews, old_key, review())
    raw_digest = server_app._public_raw_source_digest(RECORD)
    raw_key = server_app._public_raw_review_key(UUID, raw_digest, 2, "auto", 0.1)

    async def scenario():
        from server.paipu_performance import performance
        stats = {"_public_raw_source": (UUID, raw_digest)}
        token = performance.set(stats)
        try:
            task = await server_app._start_review_job(
                game_id=UUID, mjai_events=events, meta=META, seat=2,
                engine="auto", temperature=0.1, cache_public=True
            )
            assert await task == review()
        finally:
            performance.reset(token)

    monkeypatch.setattr(server_app, "review_game", lambda *_a, **_kw: pytest.fail("should not infer"))
    asyncio.run(scenario())
    assert review_cache.load(reviews, raw_key, UUID, 2) == review()
    assert server_app._load_fast_public_review(UUID, 2, "auto", 0.1) == review()
