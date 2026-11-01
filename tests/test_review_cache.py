"""Completed reviews use a bounded disk cache only for genuine paipu requests."""
import json
import os
import time

from server import review_cache
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA


def result(seat=0):
    return {
        "game_id": "241026-11111111-2222-3333-4444-555555555555",
        "target_actor": seat,
        "engine": {
            "type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
            "weight_sha256": MODEL_SHA256, "revision": MODEL_REVISION, "version": 4,
            "steps": None, "q_source": "neural_network", "review_schema": REVIEW_SCHEMA,
        },
        "kyokus": [{"label": "东1局", "steps": [{"event": {"type": "start_kyoku"}}]}],
    }


def test_cache_roundtrip_and_seat_identity(tmp_path):
    first = result()
    key = "a" * 64
    assert review_cache.save(tmp_path, key, first)
    assert review_cache.load(tmp_path, key, first["game_id"], 0) == first
    assert review_cache.load(tmp_path, key, first["game_id"], 1) is None
    assert review_cache.load(tmp_path, key, "different-game", 0) is None
    try:
        review_cache.load(tmp_path, "../different", first["game_id"], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid cache key accepted")
    snapshot = review_cache.storage_metrics(tmp_path)
    assert snapshot["entries"] == 1
    assert snapshot["bytes"] > 0
    assert snapshot["budget_bytes"] == review_cache.CACHE_BUDGET_BYTES
    assert "path" not in snapshot


def test_expiry_and_corrupt_entries_fail_closed(tmp_path, monkeypatch):
    key = "b" * 64
    assert review_cache.save(tmp_path, key, result())
    path = tmp_path / f"{key}.json"
    old = time.time() - review_cache.CACHE_TTL_SECONDS - 20
    os.utime(path, (old, old))
    assert review_cache.load(tmp_path, key, result()["game_id"], 0) is None
    path.write_text("{not-json", encoding="utf8")
    assert review_cache.load(tmp_path, key, result()["game_id"], 0) is None
    invalid = result()
    invalid["engine"]["weight_sha256"] = "0" * 64
    assert not review_cache.save(tmp_path, "c" * 64, invalid)


def test_global_cache_budget_evicts_oldest(tmp_path, monkeypatch):
    first = "d" * 64
    second = "e" * 64
    assert review_cache.save(tmp_path, first, result())
    path = tmp_path / f"{first}.json"
    size = path.stat().st_size
    monkeypatch.setattr(review_cache, "CACHE_BUDGET_BYTES", size + 10)
    past = time.time() - 5
    os.utime(path, (past, past))
    assert review_cache.save(tmp_path, second, result())
    assert not path.exists()
    assert (tmp_path / f"{second}.json").exists()


def test_symlink_does_not_leak_external_cache_data(tmp_path):
    external = tmp_path / "private"
    external.write_text(json.dumps({"version": 1, "result": result()}), encoding="utf8")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ("f" * 64 + ".json")).symlink_to(external)
    assert review_cache.load(cache_dir, "f" * 64, result()["game_id"], 0) is None
    assert review_cache.storage_metrics(cache_dir)["entries"] == 0
