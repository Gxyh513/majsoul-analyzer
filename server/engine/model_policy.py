"""Single-model contract shared by inference, API status and review caches."""
from __future__ import annotations

import hashlib
from pathlib import Path

MODEL_FILE = "mortal_582500.pth"
# SHA-256 (Git LFS OID) of the published Yuchen1457/mortal-582500 checkpoint.
MODEL_SHA256 = "738e0d6e3c0ce9671629554ad39abd147d2ffbac676e80b194c83f2acc0fea20"
MODEL_ID = "akagi"  # Existing frontend/API identifier for this checkpoint.
REVIEW_SCHEMA = "mortal-582500-only-v3"


def normalize_model_id(model_id: str | None) -> str:
    if model_id not in (None, "", "auto", MODEL_ID, "mortal_582500"):
        raise ValueError("只允许 mortal_582500.pth，禁止使用内置启发式引擎或其他模型。")
    return MODEL_ID


def valid_review_source(review: object, weight_sha256: str) -> bool:
    if not isinstance(review, dict) or not weight_sha256:
        return False
    engine = review.get("engine")
    return isinstance(engine, dict) and all((
        engine.get("type") == "mortal",
        engine.get("id") == MODEL_ID,
        engine.get("weight_file") == MODEL_FILE,
        engine.get("weight_sha256") == weight_sha256,
        engine.get("version") == 4,
        engine.get("steps") == 582500,
        engine.get("q_source") == "neural_network",
        engine.get("review_schema") == REVIEW_SCHEMA,
    ))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
