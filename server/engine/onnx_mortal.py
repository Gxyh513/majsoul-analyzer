"""Verified ONNX Mortal inference, with no alternative model or fallback."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from ..paipu_performance import increment as _increment, measure as _measure, review_inference as _review_inference
from .model_policy import MODEL_REVISION, MODEL_SHA256, MODEL_SIZE, MODEL_STEPS, MODEL_VERSION

OBS_SHAPE = (1012, 34)
ACTION_SPACE = 46


def _check_signature(session) -> None:
    inputs = {item.name: item for item in session.get_inputs()}
    outputs = {item.name: item for item in session.get_outputs()}
    if set(inputs) != {"obs", "mask"} or set(outputs) != {"q"}:
        raise ValueError("ONNX 输入/输出名称不匹配: 需要 obs、mask 和 q")
    for item, dtype, dimensions in (
        (inputs["obs"], "tensor(float)", OBS_SHAPE),
        (inputs["mask"], "tensor(bool)", (ACTION_SPACE,)),
        (outputs["q"], "tensor(float)", (ACTION_SPACE,)),
    ):
        shape = item.shape
        if (item.type != dtype or not isinstance(shape, (list, tuple))
                or len(shape) != len(dimensions) + 1
                or isinstance(shape[0], int)
                or tuple(shape[1:]) != dimensions):
            raise ValueError(f"ONNX 张量签名不匹配: {item.name} {item.type} {shape}")


def _real_state_sample() -> tuple[np.ndarray, np.ndarray]:
    # The same native state machine and v4 encoding used by the reviewer.
    engine_dir = str(Path(__file__).resolve().parent)
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    from libriichi.state import PlayerState

    state = PlayerState(0)
    events = [
        {"type": "start_game", "names": ["A", "B", "C", "D"]},
        {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0,
         "kyotaku": 0, "oya": 0, "scores": [25000] * 4, "dora_marker": "E",
         "tehais": [["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
                     "1p", "2p", "3p", "1s"]] + [["?"] * 13 for _ in range(3)]},
        {"type": "tsumo", "actor": 0, "pai": "2s"},
    ]
    for event in events:
        state.update(json.dumps(event))
    return state.encode_obs(MODEL_VERSION, False)


class OnnxMortal:
    """Load the exact published model and expose the reviewer's batch interface.

    The model already computes Q using the mean advantage over legal actions;
    this wrapper neither recomputes nor masks its results.
    """

    def __init__(self, model_path: str | Path, *, providers: list[str] | None = None):
        self.model_path = Path(model_path)
        # Verify and load the same bytes, avoiding replacement between verification
        # and ONNX Runtime's parser. External model data is not supported.
        data = self.model_path.read_bytes()
        self.file_size = len(data)
        if self.file_size != MODEL_SIZE:
            raise ValueError(f"ONNX 模型大小不匹配: {self.file_size}，期望 {MODEL_SIZE}")
        self.weight_sha256 = hashlib.sha256(data).hexdigest()
        if self.weight_sha256 != MODEL_SHA256:
            raise ValueError("ONNX 模型 SHA-256 不匹配")

        import onnxruntime as ort

        selected = ["CPUExecutionProvider"] if providers is None else providers
        if not isinstance(selected, list) or not selected or any(not isinstance(p, str) for p in selected):
            raise ValueError("providers 必须是非空的执行提供程序名称列表")
        if any(p not in ort.get_available_providers() for p in selected):
            raise ValueError(f"ONNX Runtime 执行提供程序不可用: {selected}")
        self.session = ort.InferenceSession(data, providers=selected)
        _check_signature(self.session)
        self.version = MODEL_VERSION
        self.steps = MODEL_STEPS
        self.revision = MODEL_REVISION
        self.tag = f"mortal-onnx-{MODEL_REVISION[:7]}"
        self.device = self.session.get_providers()[0]
        obs, mask = _real_state_sample()
        self.eval_batch([obs], [mask])

    def eval_batch(self, obs_list, mask_list, chunk_size: int = 128) -> np.ndarray:
        """Evaluate observations and legal-action masks, preserving input order."""
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
            raise ValueError("chunk_size 必须是正整数")
        if not isinstance(obs_list, (list, tuple, np.ndarray)) or not isinstance(mask_list, (list, tuple, np.ndarray)):
            raise TypeError("obs 和 mask 必须是批次数组或序列")
        if isinstance(obs_list, np.ndarray) and (obs_list.ndim != 3 or obs_list.shape[1:] != OBS_SHAPE or obs_list.dtype != np.float32):
            raise ValueError("obs 必须为 float32 [batch, 1012, 34]")
        if isinstance(mask_list, np.ndarray) and (mask_list.ndim != 2 or mask_list.shape[1:] != (ACTION_SPACE,) or mask_list.dtype != np.bool_):
            raise ValueError("mask 必须为 bool [batch, 46]")
        if len(obs_list) != len(mask_list):
            raise ValueError("obs 与 mask 的批次大小不一致")
        for obs, mask in zip(obs_list, mask_list):
            if not isinstance(obs, np.ndarray) or obs.shape != OBS_SHAPE or obs.dtype != np.float32:
                raise ValueError("每个 obs 必须为 float32 [1012, 34]")
            if not np.isfinite(obs).all():
                raise ValueError("obs 必须全部为有限值")
            if not isinstance(mask, np.ndarray) or mask.shape != (ACTION_SPACE,) or mask.dtype != np.bool_:
                raise ValueError("每个 mask 必须为 bool [46]")
            if not mask.any():
                raise ValueError("每个 mask 必须至少包含一个合法动作")
        if len(obs_list) == 0:
            return np.empty((0, ACTION_SPACE), dtype=np.float32)
        outputs = []
        for start in range(0, len(obs_list), chunk_size):
            obs = np.ascontiguousarray(np.stack(obs_list[start:start + chunk_size]))
            mask = np.ascontiguousarray(np.stack(mask_list[start:start + chunk_size]))
            if _review_inference.get():
                _increment("ONNX推理调用次数")
                with _measure("ONNX实际推理耗时"):
                    result = self.session.run(["q"], {"obs": obs, "mask": mask})
            else:
                result = self.session.run(["q"], {"obs": obs, "mask": mask})
            if not isinstance(result, (list, tuple)) or len(result) != 1:
                raise RuntimeError("ONNX 模型输出数量不匹配")
            q = result[0]
            if not isinstance(q, np.ndarray) or q.dtype != np.float32 or q.shape != mask.shape:
                raise RuntimeError("ONNX 模型输出必须为 float32 [batch, 46]")
            if not np.isfinite(q[mask]).all() or not np.isneginf(q[~mask]).all():
                raise RuntimeError("ONNX 模型输出无效: 合法动作必须有限且非法动作必须为 -inf")
            outputs.append(q)
        return np.concatenate(outputs, axis=0)
