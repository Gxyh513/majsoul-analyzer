"""Identity and review provenance for the single supported ONNX model."""
from __future__ import annotations

import hashlib
from pathlib import Path

MODEL_FILE = "model.onnx"
MODEL_SHA256 = "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4"
MODEL_SIZE = 43673425
MODEL_REVISION = "2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a"
MODEL_ID = "mortal-s42-onnx"
MODEL_NAME = "Mortal S42 ONNX"
MODEL_REPOSITORY = "xzhe121/Mortal-S42-onnx"
MODEL_FORMAT = "onnx"
MODEL_VERSION = 4
# The ONNX artifact does not establish a checkpoint training step count.
MODEL_STEPS = None
REVIEW_SCHEMA = "mortal-onnx-2f7ad48-v1"


def normalize_model_id(model_id: str | None) -> str:
    if model_id not in (None, "", "auto", MODEL_ID):
        raise ValueError("只允许指定的 ONNX 模型，禁止使用其他模型或启发式引擎。")
    return MODEL_ID


def valid_review_source(review: object, weight_sha256: str) -> bool:
    if not isinstance(review, dict) or weight_sha256 != MODEL_SHA256:
        return False
    engine = review.get("engine")
    return isinstance(engine, dict) and all((
        engine.get("type") == "mortal",
        engine.get("id") == MODEL_ID,
        engine.get("weight_file") == MODEL_FILE,
        engine.get("weight_sha256") == MODEL_SHA256,
        engine.get("revision") == MODEL_REVISION,
        engine.get("version") == MODEL_VERSION,
        "steps" in engine and engine["steps"] == MODEL_STEPS,
        engine.get("q_source") == "neural_network",
        engine.get("review_schema") == REVIEW_SCHEMA,
    ))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
