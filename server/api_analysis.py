"""Non-stream analysis and on-demand model management routes."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from .api_schemas import AnalyzePaipuReq, AnalyzeDemoReq
from .analysis_service import _wait_for_model_download, _run_nonstream_until_disconnect
from .public_review_cache import _try_fast_public_review
from .game_data import resolve_game_data
from .review_jobs import _run_review
from .paipu_performance import set_value as _set_performance_value
from .model_download import trigger_model_download
from .engine.model_policy import MODEL_ID

router = APIRouter()


@router.post("/api/analyze/paipu")
async def api_analyze_paipu(req: AnalyzePaipuReq, request: Request, response: Response):
    async def work():
        await _wait_for_model_download(req.engine)
        cached = await _try_fast_public_review(req.paipu, req.target_actor, req.engine, req.temperature)
        if cached is not None:
            return cached
        game_id, mjai_events, meta, seat = await resolve_game_data(paipu=req.paipu, target_actor=req.target_actor)
        _set_performance_value("mode", "按需分析")
        return await _run_review(game_id, mjai_events, meta, seat, req.engine, req.temperature,
                                 cache_public=(game_id != "demo"))
    return await _run_nonstream_until_disconnect(request, response, work())


@router.post("/api/analyze/demo")
async def api_analyze_demo(req: AnalyzeDemoReq, request: Request, response: Response):
    async def work():
        await _wait_for_model_download(req.engine)
        game_id, mjai_events, meta, seat = await resolve_game_data(paipu="demo", target_actor=req.target_actor)
        _set_performance_value("mode", "按需分析")
        return await _run_review(game_id, mjai_events, meta, seat, req.engine, req.temperature)
    return await _run_nonstream_until_disconnect(request, response, work())



@router.post("/api/engine/download")
async def api_download_mortal_model():
    trigger_model_download(MODEL_ID)
    return {"status": "started", "message": "已在后台启动 Mortal S42 ONNX 模型下载"}


