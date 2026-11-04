"""Validated ONNX settings, read once from the environment at process startup."""
from __future__ import annotations

import os


def _bounded_integer(name: str, value: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _environment_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        # Do not expose the supplied value, even in a chained exception.
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}") from None
    return _bounded_integer(name, value, minimum, maximum)


ONNX_INTRA_OP_THREADS = _environment_integer("MAJSOUL_ONNX_INTRA_OP_THREADS", 0, 0, 64)
ONNX_INTER_OP_THREADS = _environment_integer("MAJSOUL_ONNX_INTER_OP_THREADS", 0, 0, 64)
ONNX_BATCH_SIZE = _environment_integer("MAJSOUL_ONNX_BATCH_SIZE", 128, 1, 128)


def create_session_options(*, intra_op_threads: int = ONNX_INTRA_OP_THREADS,
                           inter_op_threads: int = ONNX_INTER_OP_THREADS):
    """Override only requested thread counts; zero leaves ORT automatic.

    Return None for 0/0 so InferenceSession follows the original, entirely
    unmodified ORT-default options path. Explicit overrides support benchmarks
    without changing the process-wide production configuration.
    """
    _bounded_integer("intra_op_threads", intra_op_threads, 0, 64)
    _bounded_integer("inter_op_threads", inter_op_threads, 0, 64)
    if intra_op_threads == 0 and inter_op_threads == 0:
        return None

    import onnxruntime as ort

    options = ort.SessionOptions()
    if intra_op_threads:
        options.intra_op_num_threads = intra_op_threads
    if inter_op_threads:
        options.inter_op_num_threads = inter_op_threads
    return options
