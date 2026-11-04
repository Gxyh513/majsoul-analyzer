"""Core review + replay builder for Mahjong Soul 4-player logs.

Uses Mortal S42 ONNX for action Q values and replay decisions.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import libriichi.state as lstate  # noqa: E402

from ..paipu_performance import add_elapsed as _add_elapsed, increment as _increment, measure as _measure  # noqa: E402
from ..logger import log_debug
from ..paipu_performance import review_inference as _review_inference, set_value as _set_performance_value  # noqa: E402
from .onnx_mortal import ACTION_SPACE, OnnxMortal  # noqa: E402
from .model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, REVIEW_SCHEMA, normalize_model_id  # noqa: E402
from .tiles import (  # noqa: E402
    action_idx_of_tile,
    deaka,
    is_aka,
    sort_key,
    tid,
    tile_cn,
    tile_of_action_idx,
    tname,
)

log = logging.getLogger(__name__)

from .review_labels import BAKAZE_CN, SEAT_WIND_CN, MAJSOUL_YAKU_NAMES_CN, _hora_score_description


# ---------------------------------------------------------------------------
# Single-model manager
# ---------------------------------------------------------------------------
class ModelInferenceError(RuntimeError):
    pass


from .model_manager import EngineManager


# ---------------------------------------------------------------------------
# Softmax & action helpers (ported from mjai-reviewer/src/review/mortal.rs)
# ---------------------------------------------------------------------------
from .actions import softmax, event_to_label, next_player_action, label_to_action_dict, format_action_cn
from .review_scoring import finalize_review
from .review_replay import ReplayInferenceError, build_replay


# ---------------------------------------------------------------------------
# Full Game Reviewer + Replay Builder
# ---------------------------------------------------------------------------
def review_game(
    mjai_events: list[dict],
    target_actor: int,
    engine_mgr: EngineManager,
    meta: dict | None = None,
    temperature: float = 0.1,
    force_engine: str = "auto",
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    _set_performance_value("ONNX推理调用次数", 0)
    _set_performance_value("ONNX实际推理耗时", 0.0)
    if not isinstance(target_actor, int) or isinstance(target_actor, bool) or not 0 <= target_actor <= 3:
        raise ValueError("target_actor 必须为 0..3 的整数")
    mortal, resolved_id, resolved_pth = engine_mgr.get_mortal(force_engine)
    obs_version = mortal.version

    try:
        token = _review_inference.set(True)
        try:
            with _measure("回放构建耗时"):
                kyokus, pending_decisions, mortal_q_batch, names = build_replay(
                    mjai_events, target_actor, obs_version, mortal, meta, progress_callback,
                )
        finally:
            _review_inference.reset(token)
    except ReplayInferenceError as exc:
        cause = exc.__cause__ or exc
        engine_mgr.invalidate(mortal, cause)
        raise ModelInferenceError(f"Mortal S42 ONNX 推理失败，已终止分析: {cause}") from exc

    if mortal is not None:
        if progress_callback:
            progress_callback(84.5, f"已完成 Mortal S42 ONNX 分批推理（{len(pending_decisions)} 处待评决策）...")
        try:
            with _measure("ONNX结果校验耗时"):
                if (not isinstance(mortal_q_batch, np.ndarray)
                        or mortal_q_batch.shape != (len(pending_decisions), ACTION_SPACE)
                        or mortal_q_batch.dtype != np.float32):
                    raise RuntimeError("Mortal S42 ONNX 输出与待评决策数不匹配")
                if pending_decisions:
                    legal_masks = np.stack([item["mask"] for item in pending_decisions])
                    if not np.isfinite(mortal_q_batch[legal_masks]).all():
                        raise RuntimeError("Mortal S42 ONNX 合法动作输出无效")
        except Exception as exc:
            engine_mgr.invalidate(mortal, exc)
            raise ModelInferenceError(f"Mortal S42 ONNX 推理失败，已终止分析: {exc}") from exc
    else:
        mortal_q_batch = None

    return finalize_review(
        pending_decisions=pending_decisions,
        mortal_q_batch=mortal_q_batch,
        kyokus=kyokus,
        target_actor=target_actor,
        temperature=temperature,
        progress_callback=progress_callback,
        names=names,
        meta=meta,
        mortal=mortal,
        resolved_id=resolved_id,
        resolved_pth=resolved_pth,
    )
