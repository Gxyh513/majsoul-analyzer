"""Compact, opt-in SSE representation of immutable replay snapshots.

The canonical review, on-disk cache, and non-stream API keep their previous
schema. A compact result replaces repeated snapshot fields with references to
per-round tables. Indexes are expanded by the browser into shared JS arrays.
"""
from __future__ import annotations

import json
from typing import Any

SNAPSHOT_FIELDS = (
    "hands", "drawn", "melds", "rivers", "riichi", "scores", "dora_markers",
)


def compact_review_snapshots(result: dict[str, Any]) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    for kyoku in result.get("kyokus", []):
        tables: dict[str, list[Any]] = {field: [] for field in SNAPSHOT_FIELDS}
        # Identity avoids repeated encoding of the same immutable Python object.
        # Content deduplication also works with cache entries loaded from JSON,
        # where identical snapshots no longer share Python object identities.
        identity: dict[str, dict[int, int]] = {field: {} for field in SNAPSHOT_FIELDS}
        signatures: dict[str, dict[str, int]] = {field: {} for field in SNAPSHOT_FIELDS}
        steps = []
        for step in kyoku.get("steps", []):
            compact_step = dict(step)
            for field in SNAPSHOT_FIELDS:
                value = step[field]
                obj_id = id(value)
                index = identity[field].get(obj_id)
                if index is None:
                    signature = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    index = signatures[field].get(signature)
                    if index is None:
                        index = len(tables[field])
                        signatures[field][signature] = index
                        tables[field].append(value)
                    identity[field][obj_id] = index
                compact_step[field] = index
            steps.append(compact_step)
        rounds.append({**kyoku, "steps": steps, "snapshot_tables": tables})
    return {**result, "kyokus": rounds, "transport_version": 2}
