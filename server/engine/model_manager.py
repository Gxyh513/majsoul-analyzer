"""Verified Mortal model lifecycle with thread-safe reuse and invalidation."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from ..logger import log_debug
from ..paipu_performance import add_elapsed as _add_elapsed, increment as _increment, measure as _measure, set_value as _set_performance_value
from .model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, REVIEW_SCHEMA, normalize_model_id
from .onnx_mortal import OnnxMortal


class EngineManager:
    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._model = None
        self._identity = None
        self._load_error = None
        self._inference_failed = False

    @staticmethod
    def _file_identity(path: Path):
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def get_mortal(self, model_id: str | None = None):
        started = time.perf_counter()
        with _measure("AI模型准备耗时"):
            normalize_model_id(model_id)
            path = self.models_dir / MODEL_FILE
            lock_started = time.perf_counter()
            with self._lock:
                _add_elapsed("模型锁等待耗时", lock_started)
                if self._inference_failed:
                    raise RuntimeError(f'Mortal S42 ONNX 推理失败: {self._load_error}')
                try:
                    identity = self._file_identity(path)
                    if self._model is not None and self._identity == identity:
                        _increment("AI模型复用次数")
                        _add_elapsed("获取已加载模型耗时", started)
                        return self._model, MODEL_ID, path
                    self._model = None
                    self._identity = None
                    _increment("AI模型加载次数")
                    load_started = time.perf_counter()
                    try:
                        with _measure("模型加载及初始化耗时"):
                            model = OnnxMortal(path)
                    finally:
                        log_debug(
                            事件="AI模型准备",
                            来源="模型获取",
                            耗时毫秒=round((time.perf_counter() - load_started) * 1000, 1),
                        )
                    if identity != self._file_identity(path):
                        raise RuntimeError('模型权重在加载时发生变化')
                    self._model, self._identity, self._load_error = model, identity, None
                    return model, MODEL_ID, path
                except Exception as exc:
                    self._model = None
                    self._identity = None
                    self._load_error = str(exc)
                    raise RuntimeError(f'Mortal S42 ONNX 不可用: {exc}') from exc

    def install_model(self, temporary_path: Path):
        path = self.models_dir / MODEL_FILE
        with self._lock:
            identity = self._file_identity(temporary_path)
            load_started = time.perf_counter()
            _set_performance_value("_model_init_started", load_started)
            try:
                with _measure("模型加载及初始化耗时"):
                    model = OnnxMortal(temporary_path)
            finally:
                load_finished = time.perf_counter()
                _set_performance_value("_model_init_finished", load_finished)
                log_debug(
                    事件="AI模型准备",
                    来源="后台下载任务",
                    耗时毫秒=round((load_finished - load_started) * 1000, 1),
                )
            if identity != self._file_identity(temporary_path):
                raise RuntimeError('模型权重在校验时发生变化')
            os.replace(temporary_path, path)
            model.model_path = path
            self._model = model
            self._identity = self._file_identity(path)
            if self._identity[0] != identity[0]:
                self._model = None
                self._identity = None
                raise RuntimeError('模型文件替换失败')
            self._load_error = None
            self._inference_failed = False
            return model

    def invalidate(self, model, error: Exception):
        with self._lock:
            if self._model is model:
                self._model = None
                self._identity = None
                self._load_error = str(error)
                self._inference_failed = True

    def status(self) -> dict[str, Any]:
        path = self.models_dir / MODEL_FILE
        try:
            model, _, _ = self.get_mortal()
        except Exception:
            model = None
        return {
            'model_ready': model is not None,
            'has_mortal_weights': model is not None,
            'active_engine': 'mortal' if model is not None else 'unavailable',
            'model_name': 'Mortal S42 ONNX',
            'model_id': MODEL_ID,
            'model_tag': model.tag if model else None,
            'model_revision': MODEL_REVISION,
            'weight_sha256': model.weight_sha256 if model else None,
            'review_schema': REVIEW_SCHEMA,
            'model_path': str(path),
            'models_dir': str(self.models_dir),
            'device': str(model.device).upper() if model else 'N/A',
            'load_error': self._load_error,
        }


