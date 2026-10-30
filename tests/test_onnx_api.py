"""Real-model API attestation and failure behavior."""
import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server import app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA
from server.engine.reviewer import EngineManager

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models"


@pytest.mark.parametrize("route", ["demo", "stream"])
@pytest.mark.parametrize("replacement_during_inference", [False, True])
def test_real_model_inference_failure_returns_503(monkeypatch, tmp_path, route, replacement_during_inference):
    if not (MODEL_DIR / MODEL_FILE).is_file():
        pytest.skip("published ONNX model not present")
    manager = EngineManager(MODEL_DIR)
    model, _, _ = manager.get_mortal(MODEL_ID)
    def fail(*args, **kwargs):
        if replacement_during_inference:
            # Simulate a concurrently installed model winning the manager lock.
            with manager._lock:
                manager._model = object()
        raise RuntimeError("private inference error")
    monkeypatch.setattr(model, "eval_batch", fail)
    monkeypatch.setattr(app, "engine_mgr", manager)
    monkeypatch.setattr(app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    payload = {"paipu": "demo", "target_actor": 0} if route == "stream" else {"target_actor": 0}
    response = client.post(f"/api/analyze/{route}", json=payload)
    assert response.status_code == 503, response.text
    assert "private inference error" not in response.text
    if replacement_during_inference:
        assert manager._model is not model and not manager._inference_failed
    else:
        assert manager.status()["active_engine"] == "unavailable"


@pytest.mark.parametrize("route", ["demo", "stream"])
def test_failed_replacement_keeps_verified_model_available(monkeypatch, route):
    if not (MODEL_DIR / MODEL_FILE).is_file():
        pytest.skip("published ONNX model not present")
    manager = EngineManager(MODEL_DIR)
    monkeypatch.setattr(app, "engine_mgr", manager)
    monkeypatch.setattr(app, "model_download_state", {"status": "error", "error_msg": "replacement failed"})
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    payload = {"paipu": "demo", "target_actor": 0} if route == "stream" else {"target_actor": 0}
    response = client.post(f"/api/analyze/{route}", json=payload)
    assert response.status_code == 200, response.text
    assert manager.status()["weight_sha256"] == MODEL_SHA256
    if route == "stream":
        assert '"type": "complete"' in response.text
    else:
        assert response.json()["engine"]["revision"] == MODEL_REVISION
