"""Optional CPU benchmark; does not change production ONNX Runtime settings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

import numpy as np
import onnxruntime as ort

from server.engine.model_policy import MODEL_SHA256, sha256_file
from server.engine.onnx_mortal import _real_state_sample


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
    events = json.loads((Path(__file__).resolve().parents[1] / "data" / "demo_mjai.json").read_text(encoding="utf8"))
    review_game(events, 0, manager)
    if not captured:
        raise ValueError("演示牌谱未产生可供基准测试的真实决策")
    return captured


def benchmark(model: Path, threads: list[int], batches: list[int], runs: int,
              *, real_demo: bool = False) -> list[dict]:
    if sha256_file(model) != MODEL_SHA256:
        raise ValueError("模型 SHA-256 不一致，拒绝性能基准")
    if runs < 1 or any(x < 1 for x in [*threads, *batches]):
        raise ValueError("线程、批量、重复次数必须为正整数")
    if real_demo:
        samples = _sample_demo_states(model)
    else:
        obs, mask = _real_state_sample()
        samples = [(obs, mask)]
    reference_by_batch = {}
    report = []
    for nthreads in threads:
        options = ort.SessionOptions()
        options.intra_op_num_threads = nthreads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(model.read_bytes(), sess_options=options,
                                       providers=["CPUExecutionProvider"])
        for batch in batches:
            obs_batch = np.stack([samples[i % len(samples)][0] for i in range(batch)])
            mask_batch = np.stack([samples[i % len(samples)][1] for i in range(batch)])
            outputs = []
            session.run(["q"], {"obs": obs_batch, "mask": mask_batch})  # warm up
            for _ in range(runs):
                t0 = time.perf_counter()
                pred = session.run(["q"], {"obs": obs_batch, "mask": mask_batch})[0]
                outputs.append((time.perf_counter() - t0) * 1000)
                if batch not in reference_by_batch:
                    reference_by_batch[batch] = pred.copy()
                np.testing.assert_allclose(pred, reference_by_batch[batch], atol=1e-4, rtol=1e-4)
            report.append({"threads": nthreads, "batch": batch, "real_demo": real_demo,
                           "median_ms": round(statistics.median(outputs), 2),
                           "ms_per_decision": round(statistics.median(outputs) / batch, 3)})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="比较 ONNX CPU 线程数与批量，无生产改动")
    parser.add_argument("--model", type=Path, default=Path("data/models/model.onnx"))
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--batches", type=int, nargs="+", default=[16, 64, 128])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--real-demo", action="store_true", help="使用内置演示牌谱采集真实 MJAI 决策状态")
    args = parser.parse_args()
    print(json.dumps(benchmark(args.model, args.threads, args.batches, args.runs,
                               real_demo=args.real_demo), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
