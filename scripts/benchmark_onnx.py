"""Optional CPU benchmark; never tunes production ONNX settings.

Synthetic mode repeats one constructed starting-hand state, not real replay
traffic. Real-demo mode cycles through decisions captured from bundled MJAI.
Model verification, state collection, session initialization and per-batch
warmup are separate from steady model-only inference timings. Input assembly
and Q validation/equivalence checks are outside those inference timings.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

import numpy as np
import onnxruntime as ort

from server.engine.model_policy import MODEL_SHA256
from server.engine.onnx_mortal import (
    _check_output,
    _check_signature,
    _real_state_sample,
    load_verified_model_bytes,
)
from server.engine.runtime_config import (
    ONNX_BATCH_SIZE,
    ONNX_INTER_OP_THREADS,
    ONNX_INTRA_OP_THREADS,
    create_session_options,
)


def _sample_demo_states(model: Path):
    """Capture actual legal-action masks and observations from bundled MJAI."""
    from server.engine.onnx_mortal import OnnxMortal
    from server.engine.reviewer import EngineManager, review_game
    from server.engine.model_policy import MODEL_ID

    runtime = OnnxMortal(model)
    manager = EngineManager(model.parent)
    manager.get_mortal = lambda _: (runtime, MODEL_ID, model)
    captured = []
    original = runtime.eval_batch

    def take_samples(observations, masks, **kwargs):
        captured.extend((obs.copy(), mask.copy()) for obs, mask in zip(observations, masks))
        return original(observations, masks, **kwargs)

    runtime.eval_batch = take_samples
    try:
        events = json.loads((Path(__file__).resolve().parents[1] / "data" / "demo_mjai.json").read_text(encoding="utf8"))
        review_game(events, 0, manager)
    finally:
        # Break the wrapper -> bound method -> runtime cycle so its native
        # session/thread pools do not linger during the measured sweep.
        del runtime.eval_batch
    if not captured:
        raise ValueError("演示牌谱未产生可供基准测试的真实决策")
    return captured


def _check_policy_equivalence(pred: np.ndarray, reference: np.ndarray) -> None:
    np.testing.assert_allclose(pred, reference, atol=1e-4, rtol=1e-4)
    if not np.array_equal(pred.argmax(axis=1), reference.argmax(axis=1)):
        raise ValueError("线程或批次配置改变了模型首选动作，拒绝使用该性能结果")


def benchmark(model: Path, threads: list[int], batches: list[int], runs: int,
              *, real_demo: bool = False, inter_threads: int = 1) -> dict:
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise ValueError("runs must be a positive integer")
    if not batches or any(isinstance(x, bool) or not isinstance(x, int) or not 1 <= x <= 128 for x in batches):
        raise ValueError("batches must contain integers between 1 and 128")
    if any(isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 64 for x in threads):
        raise ValueError("threads must contain integers between 0 and 64")
    if isinstance(inter_threads, bool) or not isinstance(inter_threads, int) or not 0 <= inter_threads <= 64:
        raise ValueError("inter_threads must be an integer between 0 and 64")

    started = time.perf_counter()
    data = load_verified_model_bytes(model)
    model_read_verify_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    if real_demo:
        samples = _sample_demo_states(model)
    else:
        obs, mask = _real_state_sample()
        samples = [(obs, mask)]
    state_collection_ms = (time.perf_counter() - started) * 1000

    # Always retain the untouched ORT defaults, even when the environment
    # overrides production settings. Every configuration uses the same helper.
    configurations = [("production_default", 0, 0)]
    if (ONNX_INTRA_OP_THREADS, ONNX_INTER_OP_THREADS) != (0, 0):
        configurations.append(("production_configured", ONNX_INTRA_OP_THREADS, ONNX_INTER_OP_THREADS))
    configurations.extend(("explicit_threads", nthreads, inter_threads) for nthreads in threads)

    reference_by_batch = {}
    reference_by_state = {}
    sessions = []
    for configuration, intra_threads, inter_op_threads in configurations:
        options = create_session_options(intra_op_threads=intra_threads,
                                         inter_op_threads=inter_op_threads)
        started = time.perf_counter()
        # Reuse the same verified bytes, never reopen an unverified model path.
        session = ort.InferenceSession(data, sess_options=options,
                                       providers=["CPUExecutionProvider"])
        _check_signature(session)
        session_init_ms = (time.perf_counter() - started) * 1000
        batch_results = []
        for batch in batches:
            obs_batch = np.stack([samples[i % len(samples)][0] for i in range(batch)])
            mask_batch = np.stack([samples[i % len(samples)][1] for i in range(batch)])
            inputs = {"obs": obs_batch, "mask": mask_batch}
            started = time.perf_counter()
            result = session.run(["q"], inputs)
            warmup_ms = (time.perf_counter() - started) * 1000
            pred = _check_output(result, mask_batch)
            if configuration == "production_default":
                # Repeated states must keep their policy across batch sizes,
                # not only across thread configurations at one fixed shape.
                for index, row in enumerate(pred):
                    state_index = index % len(samples)
                    if state_index not in reference_by_state:
                        reference_by_state[state_index] = row.copy()
                reference_by_batch[batch] = np.stack([
                    reference_by_state[index % len(samples)] for index in range(batch)
                ])
            _check_policy_equivalence(pred, reference_by_batch[batch])
            durations = []
            for _ in range(runs):
                started = time.perf_counter()
                result = session.run(["q"], inputs)
                durations.append((time.perf_counter() - started) * 1000)
                pred = _check_output(result, mask_batch)
                _check_policy_equivalence(pred, reference_by_batch[batch])
            median_ms = statistics.median(durations)
            batch_results.append({"batch": batch,
                                  "warmup_ms": round(warmup_ms, 2),
                                  "median_ms": round(median_ms, 2),
                                  "ms_per_decision": round(median_ms / batch, 3)})
        sessions.append({"configuration": configuration,
                         "intra_op_threads": intra_threads,
                         "inter_op_threads": inter_op_threads,
                         "session_init_ms": round(session_init_ms, 2),
                         "batches": batch_results})
        del session
    return {"model_sha256": MODEL_SHA256,
            "provider": "CPUExecutionProvider",
            "state_source": "real_demo_mjai" if real_demo else "synthetic_start_hand",
            "source_state_count": len(samples),
            "batch_sampling": "cyclic_from_start",
            "runs_per_batch": runs,
            "q_tolerance": {"atol": 1e-4, "rtol": 1e-4},
            "policy_equivalence": "q_values_and_argmax_across_configurations_and_batches",
            "configured_production": {"intra_op_threads": ONNX_INTRA_OP_THREADS,
                                      "inter_op_threads": ONNX_INTER_OP_THREADS,
                                      "batch_size": ONNX_BATCH_SIZE},
            "model_read_verify_ms": round(model_read_verify_ms, 2),
            "state_collection_ms": round(state_collection_ms, 2),
            "sessions": sessions}


def main() -> None:
    parser = argparse.ArgumentParser(description="比较 ONNX CPU 默认配置、线程数与批量，无生产改动")
    parser.add_argument("--model", type=Path, default=Path("data/models/model.onnx"))
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4],
                        help="额外比较的 intra-op 线程数（0..64，0=ORT 自动）；始终包含生产默认基线")
    parser.add_argument("--inter-threads", type=int, default=1,
                        help="额外线程配置的 inter-op 线程数（0..64，默认 1，0=ORT 自动）")
    parser.add_argument("--batches", type=int, nargs="+", default=sorted({16, 64, 128, ONNX_BATCH_SIZE}),
                        help="推理批量（1..128），默认包含当前生产批量")
    parser.add_argument("--runs", type=int, default=3, help="每组预热一次后的稳态推理重复次数")
    parser.add_argument("--real-demo", action="store_true",
                        help="从内置演示 MJAI 采集真实决策；默认仅重复合成起手状态")
    args = parser.parse_args()
    try:
        report = benchmark(args.model, args.threads, args.batches, args.runs,
                           real_demo=args.real_demo, inter_threads=args.inter_threads)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
