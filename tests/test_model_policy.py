"""Regression tests for the single-checkpoint review contract."""
import asyncio
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA, valid_review_source
from server.engine.reviewer import EngineManager


def review(sha="a" * 64):
    return {"target_actor": 0, "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
        "weight_sha256": sha, "version": 4, "steps": 582500,
        "q_source": "neural_network", "review_schema": REVIEW_SCHEMA}}


def test_cache_source_requires_full_provenance():
    sha = "a" * 64
    assert valid_review_source(review(), sha)
    for key, value in (("type", "builtin"), ("weight_file", "akagi.pth"),
                       ("weight_sha256", "b" * 64), ("version", 3),
                       ("steps", 298000), ("q_source", "heuristic"),
                       ("review_schema", "old")):
        result = review()
        result["engine"][key] = value
        assert not valid_review_source(result, sha)
    assert not valid_review_source({"engine": {"type": "mortal", "id": MODEL_ID}}, sha)


def test_missing_checkpoint_cannot_be_bypassed_by_alias(tmp_path):
    (tmp_path / "akagi.pth").write_bytes(b"alias")
    mgr = EngineManager(tmp_path)
    mgr.custom_pth = tmp_path / "akagi.pth"
    with pytest.raises(FileNotFoundError):
        mgr.get_mortal("akagi")
    for forbidden in ("builtin", "mortal", "custom"):
        with pytest.raises(ValueError):
            mgr.get_mortal(forbidden)


def test_nonstream_rejects_stale_cache_inside_and_outside_lock(monkeypatch):
    sha = "a" * 64
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(get_mortal=lambda _id: (SimpleNamespace(weight_sha256=sha), MODEL_ID, None)))
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    stale = {"engine": {"id": MODEL_ID, "type": "builtin"}}
    monkeypatch.setattr(server_app, "get_cached_review", lambda _key: stale)
    calls = []
    monkeypatch.setattr(server_app, "review_game", lambda *args, **kwargs: calls.append(True) or review())
    monkeypatch.setattr(server_app, "save_cached_review", lambda *args: None)
    result = asyncio.run(server_app._run_review_with_cache("test", [], {}, 0, "akagi", 0.1))
    assert calls and valid_review_source(result, sha)


def test_stream_rejects_builtin_cache(monkeypatch):
    sha = "a" * 64
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(get_mortal=lambda _id: (SimpleNamespace(weight_sha256=sha), MODEL_ID, None)))
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    async def resolve(**kwargs):
        return "test", [], {}, 0
    monkeypatch.setattr(server_app, "resolve_game_data", resolve)
    monkeypatch.setattr(server_app, "get_cached_review", lambda _key: {"engine": {"id": MODEL_ID, "type": "builtin"}})
    calls = []
    monkeypatch.setattr(server_app, "review_game", lambda *args, **kwargs: calls.append(True) or review())
    monkeypatch.setattr(server_app, "save_cached_review", lambda *args: None)
    response = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)).post("/api/analyze/stream", json={"paipu": "test", "engine": "akagi"})
    assert response.status_code == 200
    assert calls and '"type": "complete"' in response.text
    assert '"type": "builtin"' not in response.text


def test_api_rejects_builtin_before_fetch(monkeypatch):
    async def unexpected(**kwargs):
        raise AssertionError("should not fetch a game for an invalid engine")
    monkeypatch.setattr(server_app, "resolve_game_data", unexpected)
    response = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)).post("/api/analyze/paipu", json={"paipu": "test", "engine": "builtin"})
    assert response.status_code == 400


def test_mismatched_sha_blocks_loading_before_deserialization(tmp_path, monkeypatch):
    from server.engine import reviewer
    (tmp_path / MODEL_FILE).write_bytes(b"not-the-published-model")
    def forbidden_load(*args, **kwargs):
        raise AssertionError("unverified checkpoint must not be deserialized")
    monkeypatch.setattr(reviewer, "LoadedMortal", forbidden_load)
    with pytest.raises(RuntimeError, match="SHA-256"):
        EngineManager(tmp_path).get_mortal("akagi")
