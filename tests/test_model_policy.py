"""Contract tests for the pinned ONNX model and review provenance."""
import asyncio
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server.engine import reviewer
from server.engine.model_policy import (
    MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, MODEL_STEPS,
    MODEL_VERSION, REVIEW_SCHEMA, normalize_model_id, valid_review_source,
)
from server.engine.reviewer import EngineManager


def review():
    return {"target_actor": 0, "engine": {
        "type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
        "weight_sha256": MODEL_SHA256, "revision": MODEL_REVISION,
        "version": MODEL_VERSION, "steps": MODEL_STEPS,
        "q_source": "neural_network", "review_schema": REVIEW_SCHEMA,
    }}


def test_pinned_model_identity():
    assert MODEL_ID == "mortal-s42-onnx"
    assert MODEL_FILE == "model.onnx"
    assert MODEL_REVISION == "2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a"
    assert MODEL_SHA256 == "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4"
    assert REVIEW_SCHEMA == "mortal-onnx-2f7ad48-v1"
    assert MODEL_VERSION == 4 and MODEL_STEPS is None
    for alias in (None, "", "auto", MODEL_ID):
        assert normalize_model_id(alias) == MODEL_ID
    for alias in ("akagi", "builtin", "mortal", "custom"):
        with pytest.raises(ValueError):
            normalize_model_id(alias)


def test_review_source_requires_full_provenance():
    assert valid_review_source(review(), MODEL_SHA256)
    for key, value in (("type", "builtin"), ("id", "akagi"), ("weight_file", "akagi.pth"),
                       ("weight_sha256", "b" * 64), ("revision", "wrong"),
                       ("version", 3), ("steps", 582500), ("q_source", "heuristic"),
                       ("review_schema", "old")):
        result = review()
        result["engine"][key] = value
        assert not valid_review_source(result, MODEL_SHA256)
    for key in review()["engine"]:
        result = review()
        del result["engine"][key]
        assert not valid_review_source(result, MODEL_SHA256)
    assert not valid_review_source(review(), "a" * 64)
    assert not valid_review_source(None, MODEL_SHA256)


def test_missing_model_never_falls_back(tmp_path):
    (tmp_path / "akagi.pth").write_bytes(b"old model")
    mgr = EngineManager(tmp_path)
    for alias in ("auto", MODEL_ID):
        with pytest.raises((FileNotFoundError, RuntimeError)):
            mgr.get_mortal(alias)
    for alias in ("akagi", "builtin", "mortal", "custom"):
        with pytest.raises(ValueError):
            mgr.get_mortal(alias)
    status = mgr.status()
    assert not status["has_mortal_weights"] and status["active_engine"] == "unavailable"


def test_corrupt_model_blocks_analysis_before_session(tmp_path, monkeypatch):
    import onnxruntime
    (tmp_path / MODEL_FILE).write_bytes(b"corrupt")
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: pytest.fail("unverified model parsed"))
    with pytest.raises((ValueError, RuntimeError)):
        EngineManager(tmp_path).get_mortal(MODEL_ID)


@pytest.mark.parametrize("invalid_field", ["type", "weight_sha256", "version", "target_actor"])
def test_nonstream_rejects_invalid_inference_and_releases_permit(monkeypatch, invalid_field):
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(get_mortal=lambda _id: (SimpleNamespace(weight_sha256=MODEL_SHA256), MODEL_ID, None)))
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())
    invalid = review()
    if invalid_field == "target_actor":
        invalid["target_actor"] = 1
    else:
        invalid["engine"][invalid_field] = {"type": "builtin", "weight_sha256": "b" * 64, "version": 3}[invalid_field]
    calls = []

    def run_review(*args, **kwargs):
        calls.append(True)
        return invalid if len(calls) == 1 else review()

    monkeypatch.setattr(server_app, "review_game", run_review)

    async def scenario():
        with pytest.raises(RuntimeError):
            await server_app._run_review("test", [], {}, 0, MODEL_ID, 0.1)
        assert not server_app.review_semaphore.locked()
        result = await server_app._run_review("test", [], {}, 0, MODEL_ID, 0.1)
        assert valid_review_source(result, MODEL_SHA256) and result["target_actor"] == 0
        assert calls == [True, True]
        assert not server_app._review_tasks

    asyncio.run(scenario())


def test_stream_rejects_builtin_inference(monkeypatch):
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(get_mortal=lambda _id: (SimpleNamespace(weight_sha256=MODEL_SHA256), MODEL_ID, None)))
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())

    async def resolve(**kwargs):
        return "test", [], {}, 0

    monkeypatch.setattr(server_app, "resolve_game_data", resolve)
    invalid = review()
    invalid["engine"]["type"] = "builtin"
    monkeypatch.setattr(server_app, "review_game", lambda *args, **kwargs: invalid)
    response = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)).post("/api/analyze/stream", json={"paipu": "test", "engine": MODEL_ID})
    assert response.status_code == 200
    assert '"type": "error"' in response.text
    assert '"type": "complete"' not in response.text
    assert '"type": "builtin"' not in response.text


def test_api_rejects_builtin_before_fetch(monkeypatch):
    async def unexpected(**kwargs):
        raise AssertionError("should not fetch a game for an invalid engine")
    monkeypatch.setattr(server_app, "resolve_game_data", unexpected)
    response = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)).post("/api/analyze/paipu", json={"paipu": "test", "engine": "builtin"})
    assert response.status_code == 400
