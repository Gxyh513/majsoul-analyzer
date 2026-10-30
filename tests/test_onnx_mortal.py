"""ONNX artifact, signature, mask and end-to-end inference regressions."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.engine import onnx_mortal
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA
from server.engine.reviewer import EngineManager, review_game

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "data" / "models" / MODEL_FILE


@pytest.fixture(scope="module")
def real_model():
    if not MODEL.is_file():
        pytest.skip("published ONNX model not present")
    return onnx_mortal.OnnxMortal(MODEL)


def test_corrupt_equal_size_digest_rejected_before_runtime(tmp_path, monkeypatch):
    import onnxruntime
    data = b"bad model"
    path = tmp_path / MODEL_FILE
    path.write_bytes(data)
    monkeypatch.setattr(onnx_mortal, "MODEL_SIZE", len(data))
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: pytest.fail("unverified bytes parsed"))
    with pytest.raises(ValueError, match="SHA-256"):
        onnx_mortal.OnnxMortal(path)
    with pytest.raises(FileNotFoundError):
        onnx_mortal.OnnxMortal(tmp_path / "missing.onnx")


def tensor(name, dtype, shape):
    return SimpleNamespace(name=name, type=dtype, shape=shape)


def signature():
    return ([tensor("obs", "tensor(float)", ["batch", 1012, 34]),
             tensor("mask", "tensor(bool)", ["batch", 46])],
            [tensor("q", "tensor(float)", ["batch", 46])])


@pytest.mark.parametrize("side,index,field,value", [
    ("inputs", 0, "name", "bad"), ("inputs", 1, "type", "tensor(float)"),
    ("inputs", 0, "shape", [1, 1012, 34]),
    ("inputs", 0, "shape", ["batch", 1011, 34]),
    ("inputs", 1, "shape", ["batch", 45]),
    ("outputs", 0, "name", "bad"), ("outputs", 0, "type", "tensor(double)"),
    ("outputs", 0, "shape", ["batch", 47]),
])
def test_invalid_signature_rejected(side, index, field, value):
    inputs, outputs = signature()
    setattr({"inputs": inputs, "outputs": outputs}[side][index], field, value)
    session = SimpleNamespace(get_inputs=lambda: inputs, get_outputs=lambda: outputs)
    with pytest.raises(ValueError, match="ONNX"):
        onnx_mortal._check_signature(session)


def test_real_model_provenance_signature_and_batch_mask(real_model):
    assert hashlib.sha256(MODEL.read_bytes()).hexdigest() == MODEL_SHA256
    assert real_model.weight_sha256 == MODEL_SHA256
    assert real_model.revision == MODEL_REVISION and real_model.version == 4 and real_model.steps is None
    onnx_mortal._check_signature(real_model.session)
    obs, mask = onnx_mortal._real_state_sample()
    assert mask.sum() > 1
    masks = [mask.copy(), mask.copy(), mask.copy()]
    masks[1][np.flatnonzero(mask)[0]] = False
    observations = [obs.copy() for _ in masks]
    batch = real_model.eval_batch(observations, masks, chunk_size=2)
    assert batch.shape == (3, 46) and batch.dtype == np.float32
    for i, legal in enumerate(masks):
        assert np.isfinite(batch[i, legal]).all()
        assert np.isneginf(batch[i, ~legal]).all()
        np.testing.assert_allclose(batch[i, legal], real_model.eval_batch([obs], [legal])[0, legal], rtol=1e-4, atol=1e-4)
    assert real_model.eval_batch([], []).shape == (0, 46)


def test_invalid_inputs_never_reach_runtime():
    model = object.__new__(onnx_mortal.OnnxMortal)
    model.session = SimpleNamespace(run=lambda *a: pytest.fail("invalid inputs reached runtime"))
    obs = np.zeros((1012, 34), dtype=np.float32)
    mask = np.ones(46, dtype=bool)
    bad_obs = obs.copy(); bad_obs[0, 0] = np.nan
    cases = [([obs], []), ([obs.astype(np.float64)], [mask]), ([obs[:, :33]], [mask]),
             ([bad_obs], [mask]), ([obs], [mask.astype(np.int32)]),
             ([obs], [np.zeros(46, dtype=bool)]), ([obs], [mask[:45]])]
    for observations, masks in cases:
        with pytest.raises((TypeError, ValueError)):
            model.eval_batch(observations, masks)
    for chunk in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            model.eval_batch([obs], [mask], chunk_size=chunk)


@pytest.mark.parametrize("kind", ["count", "shape", "dtype", "nan", "illegal"])
def test_invalid_outputs_are_rejected(kind):
    model = object.__new__(onnx_mortal.OnnxMortal)
    obs = np.zeros((1012, 34), dtype=np.float32)
    mask = np.zeros(46, dtype=bool); mask[0] = True
    q = np.full((1, 46), -np.inf, dtype=np.float32); q[0, 0] = 1
    if kind == "shape": q = q[:, :45]
    if kind == "dtype": q = q.astype(np.float64)
    if kind == "nan": q[0, 0] = np.nan
    if kind == "illegal": q[0, 1] = 0
    model.session = SimpleNamespace(run=lambda *a: [] if kind == "count" else [q])
    with pytest.raises(RuntimeError):
        model.eval_batch([obs], [mask])


def test_demo_review_preserves_real_network_q_values(real_model, monkeypatch):
    manager = EngineManager(MODEL.parent)
    # Reuse the verified real session; capture exactly the outputs consumed by reviewer.
    monkeypatch.setattr(manager, "get_mortal", lambda _: (real_model, MODEL_ID, MODEL))
    original = real_model.eval_batch
    captures = []
    def capture(obs, masks, **kwargs):
        q = original(obs, masks, **kwargs)
        captures.append((q.copy(), [m.copy() for m in masks]))
        return q
    monkeypatch.setattr(real_model, "eval_batch", capture)
    events = json.loads((ROOT / "data" / "demo_mjai.json").read_text(encoding="utf8"))
    result = review_game(events, 0, manager, force_engine=MODEL_ID)
    assert result["decisions"] and len(captures) == 1
    q, masks = captures[0]
    assert len(q) == len(result["decisions"])
    for i, decision in enumerate(result["decisions"]):
        assert {c["label"] for c in decision["candidates"]} == set(np.flatnonzero(masks[i]))
        for candidate in decision["candidates"]:
            assert candidate["raw_q"] == pytest.approx(float(q[i, candidate["label"]]))
    assert result["engine"]["weight_sha256"] == MODEL_SHA256
    assert result["engine"]["revision"] == MODEL_REVISION
    assert result["engine"]["review_schema"] == REVIEW_SCHEMA
    assert result["engine"]["q_source"] == "neural_network"
