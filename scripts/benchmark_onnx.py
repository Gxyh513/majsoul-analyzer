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


def benchmark(model: Path, threads: list[int], batches: list[int], runs: int) -> list[dict]:
    if sha256_file(model) != MODEL_SHA256:
        raise ValueError("模型 SHA-256 不一致，拒绝性能基准")
    if runs < 1 or any(x < 1 for x in [*threads, *batches]):
        raise ValueError("线程、批量、重复次数必须为正整数")
    obs, mask = _real_state_sample()
    reference = None
    report = []
    for nthreads in threads:
        options = ort.SessionOptions()
        options.intra_op_num_threads = nthreads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(model.read_bytes(), sess_options=options,
                                       providers=["CPUExecutionProvider"])
        for batch in batches:
            obs_batch = np.broadcast_to(obs, (batch, *obs.shape)).copy()
            mask_batch = np.broadcast_to(mask, (batch, *mask.shape)).copy()
            outputs = []
            session.run(["q"], {"obs": obs_batch, "mask": mask_batch})  # warm up
            for _ in range(runs):
                t0 = time.perf_counter()
                pred = session.run(["q"], {"obs": obs_batch, "mask": mask_batch})[0]
                outputs.append((time.perf_counter() - t0) * 1000)
                if reference is None:
                    reference = pred[0]
                np.testing.assert_allclose(pred[0], reference, atol=1e-4, rtol=1e-4)
            report.append({"threads": nthreads, "batch": batch,
                           "median_ms": round(statistics.median(outputs), 2),
                           "ms_per_decision": round(statistics.median(outputs) / batch, 3)})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="比较 ONNX CPU 线程数与批量，无生产改动")
    parser.add_argument("--model", type=Path, default=Path("data/models/model.onnx"))
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--batches", type=int, nargs="+", default=[16, 64, 128])
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.model, args.threads, args.batches, args.runs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
