"""Pinned Mortal ONNX model download with atomic install and verification."""
from __future__ import annotations

import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any

from .engine.reviewer import EngineManager
from .engine.model_policy import MODEL_ID, MODEL_FILE, MODEL_SHA256, sha256_file
from .error_context import _safe_error_context
from .logger import log_debug, log_warning
from .paipu_performance import performance as _performance

MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models"


engine_mgr = EngineManager(MODELS_DIR)


# One immutable, independently verified model; never accept a user-supplied URL.
MODEL_SIZE = 43_673_425
MODEL_REVISION = "2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a"
MODEL_URL = f"https://huggingface.co/xzhe121/Mortal-S42-onnx/resolve/{MODEL_REVISION}/model.onnx"
model_download_state = {"status": "idle", "progress_pct": 0.0, "downloaded_bytes": 0,
                        "total_bytes": MODEL_SIZE, "error_msg": None}
_model_download_lock = threading.Lock()
_model_download_metrics: dict[str, Any] = {}


def _download_model_sync(model_id: str) -> None:
    global _model_download_metrics
    if model_id != MODEL_ID:
        return
    state = model_download_state
    with _model_download_lock:
        if state["status"] == "downloading":
            return
        metrics: dict[str, Any] = {}
        _model_download_metrics = metrics
        state.update(status="downloading", progress_pct=0.0, downloaded_bytes=0,
                     total_bytes=MODEL_SIZE, error_msg=None)

    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        # Use a unique directory on the same filesystem so replacement is atomic.
        with tempfile.TemporaryDirectory(prefix=".model-download-", dir=MODELS_DIR) as tmp_dir:
            tmp_path = Path(tmp_dir) / MODEL_FILE
            req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as resp, tmp_path.open("wb") as output:
                downloaded = 0
                while chunk := resp.read(512 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MODEL_SIZE:
                        raise ValueError("下载模型大小超过预期")
                    output.write(chunk)
                    state.update(downloaded_bytes=downloaded,
                                 progress_pct=min(99.0, round(downloaded / MODEL_SIZE * 100, 1)))
            if downloaded != MODEL_SIZE or sha256_file(tmp_path) != MODEL_SHA256:
                raise ValueError("下载模型大小或 SHA-256 不匹配")
            # The manager validates with OnnxMortal and atomically installs the model.
            token = _performance.set(metrics)
            try:
                engine_mgr.install_model(tmp_path)
            finally:
                _performance.reset(token)
        state.update(status="ready", progress_pct=100.0, downloaded_bytes=MODEL_SIZE,
                     total_bytes=MODEL_SIZE, error_msg=None)
        log_debug(事件="AI模型就绪", 模型="Mortal S42 ONNX", 状态="校验通过并启用")
    except Exception as exc:
        log_warning(事件="AI模型下载失败", 异常信息=_safe_error_context(exc))
        state.update(status="error", error_msg="模型下载或校验失败，请查看服务端日志。")


def trigger_model_download(model_id: str) -> bool:
    if model_id != MODEL_ID:
        return False
    threading.Thread(target=_download_model_sync, args=(MODEL_ID,), daemon=True).start()
    return True


