"""Optional fixed-model replay payload and Python peak allocation baseline.

Reads only bundled demo MJAI. Does not access or persist user JSON.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import time
import tracemalloc

from server.engine.reviewer import EngineManager, review_game

ROOT = Path(__file__).resolve().parents[1]


def measure_review(result: dict, peak_bytes: int | None, elapsed_s: float) -> dict:
    raw = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    compressed = gzip.compress(raw, compresslevel=6)
    return {
        "steps": sum(len(k.get("steps", [])) for k in result.get("kyokus", [])),
        "json_bytes": len(raw),
        "gzip_bytes": len(compressed),
        "gzip_ratio": round(len(compressed) / max(1, len(raw)), 3),
        "peak_tracemalloc_bytes": peak_bytes,
        "analysis_s": round(elapsed_s, 3),
    }


def main():
    events = json.loads((ROOT / "data" / "demo_mjai.json").read_text(encoding="utf8"))
    model = EngineManager(ROOT / "data" / "models")
    tracemalloc.start()
    started = time.perf_counter()
    review = review_game(events, 0, model)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps(measure_review(review, peak, elapsed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
