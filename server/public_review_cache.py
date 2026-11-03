"""Content-addressed public paipu review lookup and response sanitization."""
from __future__ import annotations

import asyncio
from functools import lru_cache
import hashlib
import json
from typing import Any

from . import review_cache
from .engine.model_policy import MODEL_SHA256, REVIEW_SCHEMA
from .majsoul.client import LOCAL_PROTOCOL_SHA256, MajsoulError, parse_paipu, parse_paipu_seat
from .paipu_performance import performance as _performance, set_value as _set_performance_value
from .record_store import RawRecord, RecordStorageError, read_record
from .runtime import ROOT_DIR, REVIEWS_DIR, RECORDS_DIR, LEGACY_RECORDS_DIRS
from .game_data import select_game_seat
from .admin_accounts import manager as admin_manager


def _public_review_result(result: dict[str, Any]) -> dict[str, Any]:
    engine = result.get("engine")
    if not isinstance(engine, dict):
        return dict(result)
    public_engine = {
        key: engine[key]
        for key in ("type", "id", "tag", "weight_file", "weight_sha256", "revision", "version", "steps", "q_source", "review_schema", "device")
        if key in engine
    }
    return {**result, "engine": public_engine}


def _review_fingerprint(game_id: str, events: list[dict], meta: dict,
                        seat: int, engine: str, temperature: float) -> str:
    # Include source events and metadata, not just the public game UUID; uploaded
    # JSON with the same UUID must never reuse a different player's result.
    packed = json.dumps([MODEL_SHA256, REVIEW_SCHEMA, game_id, events, meta, seat, engine, temperature],
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(packed.encode("utf8")).hexdigest()


@lru_cache(maxsize=1)
def _public_review_pipeline_identity() -> str:
    """Invalidate raw-input keys when decoding, conversion or review rules change."""
    digest = hashlib.sha256()
    for name in (
        "public_review_cache.py", "review_jobs.py", "game_data.py",
        "majsoul/client.py", "majsoul/proto.py", "majsoul/convert.py",
        "majsoul/record_codec.py", "majsoul/protocol_loader.py", "majsoul/paipu_links.py",
        "engine/reviewer.py", "engine/tiles.py", "engine/actions.py",
        "engine/review_replay.py", "engine/review_labels.py",
        "engine/review_scoring.py", "engine/model_manager.py",
        "engine/onnx_mortal.py", "engine/model_policy.py",
    ):
        digest.update(name.encode("ascii"))
        digest.update((ROOT_DIR / "server" / name).read_bytes())
    return digest.hexdigest()


def _public_raw_source_digest(source: RawRecord) -> str:
    """Hash the actual immutable raw bytes used for decoding, not a filename."""
    raw = hashlib.sha256()
    for part in (source.protocol_version.encode("ascii"), source.response, source.detail):
        raw.update(len(part).to_bytes(8, "big"))
        raw.update(part)
    return raw.hexdigest()


def _public_raw_review_key(game_id: str, source_digest: str, seat: int,
                           engine: str, temperature: float) -> str:
    """Bind the result to the exact public source, seat and review pipeline."""
    packed = json.dumps(
        ["public-raw-v1", _public_review_pipeline_identity(), LOCAL_PROTOCOL_SHA256,
         MODEL_SHA256, REVIEW_SCHEMA, game_id, source_digest, seat, engine, temperature],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(packed.encode("utf8")).hexdigest()


def _load_fast_public_review(paipu: str | None, target_actor: int | None,
                             engine: str, temperature: float) -> dict | None:
    """Try an existing public record without decoding protobuf or converting MJAI.

    All data comes from the permanent raw-record store, never private JSON. A
    missing/corrupt cache is a normal miss and takes the existing analysis path.
    """
    if not paipu or paipu == "demo" or paipu.startswith("demo-"):
        return None
    if not REVIEWS_DIR.is_dir():
        return None
    try:
        game_id, url_account_id = parse_paipu(paipu)
        stored = read_record(RECORDS_DIR, game_id, LEGACY_RECORDS_DIRS)
        if not isinstance(stored, RawRecord):
            return None  # Legacy JSON needs the existing conversion path.
        url_seat = parse_paipu_seat(paipu)
        requested_seat = target_actor if target_actor is not None else url_seat
        if requested_seat in range(4):
            candidates = (requested_seat,)
        elif url_account_id is None and admin_manager() is not None:
            candidates = (0,)
        else:
            candidates = range(4)
        source_digest = _public_raw_source_digest(stored)
        stats = _performance.get()
        if isinstance(stats, dict):
            stats["_prefetched_raw_digest"] = (game_id, stored, source_digest)
        for seat in candidates:
            key = _public_raw_review_key(game_id, source_digest, seat, engine, temperature)
            if not (REVIEWS_DIR / f"{key}.json").is_file():
                continue
            cached = review_cache.load(REVIEWS_DIR, key, game_id, seat)
            if not isinstance(cached, dict) or not isinstance(cached.get("meta"), dict):
                continue
            selected = select_game_seat(
                cached["meta"], requested_seat, url_account_id if url_seat is None else None
            )
            if selected == seat:
                return cached
    except (MajsoulError, RecordStorageError, OSError, ValueError, TypeError):
        return None
    return None


async def _try_fast_public_review(paipu: str | None, target_actor: int | None,
                                  engine: str, temperature: float) -> dict | None:
    cached = await asyncio.to_thread(_load_fast_public_review, paipu, target_actor, engine, temperature)
    if cached is not None:
        _set_performance_value("mode", "公开牌谱缓存快速命中")
        _set_performance_value("牌谱获取方式", "本地直接读取")
        _set_performance_value("牌谱获取结果", "成功")
        _set_performance_value("分析结果缓存", "原始牌谱快速命中")
    return cached


