"""Trusted versus anonymous operational status, with identity-safe public output."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Request

from .runtime import REVIEWS_DIR, ms_client
from .model_download import engine_mgr, MODEL_REVISION, MODEL_SIZE, model_download_state
from .engine.model_policy import MODEL_ID, REVIEW_SCHEMA
from .admin_accounts import manager as admin_manager
from .account_credentials import get_all_configured_accounts
from . import review_cache, ops_metrics
from . import review_jobs as _review_jobs
from .review_jobs import review_semaphore, REVIEW_MAX_WAITERS, REVIEW_CONCURRENCY

router = APIRouter()


def _build_api_status(*, trusted: bool) -> dict[str, Any]:
    eng_status = engine_mgr.status()
    eng_status["model_revision"] = MODEL_REVISION
    state = model_download_state
    if state and state.get("status") == "idle" and eng_status.get("has_mortal_weights"):
        state.update(status="ready", progress_pct=100.0, downloaded_bytes=MODEL_SIZE,
                     total_bytes=MODEL_SIZE, error_msg=None)
    if not trusted:
        # Public status carries only the fixed model identity and attestation
        # needed by the frontend, never account state, paths or raw errors.
        has_weights = bool(eng_status.get("has_mortal_weights"))
        sha = eng_status.get("weight_sha256")
        schema = eng_status.get("review_schema")
        device = eng_status.get("device")
        return {
            "ok": True,
            "engine": {
                "model_ready": has_weights,
                "has_mortal_weights": has_weights,
                "active_engine": "mortal" if has_weights and eng_status.get("active_engine") == "mortal" else "unavailable",
                "model_name": "Mortal S42 ONNX" if has_weights else "Mortal S42 ONNX (不可用)",
                "model_id": MODEL_ID,
                "model_revision": MODEL_REVISION,
                "weight_sha256": sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) else None,
                "review_schema": schema if schema == REVIEW_SCHEMA else None,
                "device": device if isinstance(device, str) and re.fullmatch(r"(?:CPU|CUDA(?::[0-9]+)?|MPS|N/A|CPUEXECUTIONPROVIDER|CUDAEXECUTIONPROVIDER)", device) else "N/A",
            },
        }

    def safe_download_state(state: dict[str, Any]) -> dict[str, Any]:
        result = dict(state)
        if result.get("error_msg"):
            result["error_msg"] = "模型下载失败，请查看服务端日志。"
        return result

    # Even trusted status responses should not reflect arbitrary exception text,
    # which can contain credentials or signed network URLs.
    if eng_status.get("load_error"):
        eng_status["load_error"] = "模型权重不可用，请查看服务端日志。"
    cur_dl = safe_download_state(model_download_state)
    eng_status["model_download"] = dict(cur_dl)
    return {
        "ok": True,
        "storage": review_cache.storage_metrics(REVIEWS_DIR),
        "majsoul": {
            "logged_in": ms_client.logged_in,
            "account_pool_count": admin_manager().overview()["total"] if admin_manager() else len(get_all_configured_accounts()),
        },
        "engine": eng_status,
        "model_download": dict(cur_dl),
        "performance_recent": {**ops_metrics.summary(),
                               "queue": {"waiting": _review_jobs._review_waiters,
                                         "inference_busy": review_semaphore.locked(),
                                         "max_waiters": REVIEW_MAX_WAITERS,
                                          "max_concurrent_reviews": REVIEW_CONCURRENCY}},
    }


@router.get("/api/status")
async def api_status(request: Request):
    return await asyncio.to_thread(_build_api_status, trusted=request.state.trusted_api)


