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
_SCALAR_LIST_FIELDS = frozenset(("drawn", "riichi", "scores", "dora_markers"))
_MISSING = object()


def _snapshot_signature(field: str, value: Any) -> Any:
    try:
        if field in _SCALAR_LIST_FIELDS and isinstance(value, list):
            key = tuple(value)
            hash(key)
            return key
        if field == "hands" and isinstance(value, list):
            key = tuple(tuple(hand) for hand in value)
            hash(key)
            return key
    except TypeError:
        pass
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compact_review_snapshots(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("transport_version") == 2:
        return result
    rounds: list[dict[str, Any]] = []
    for kyoku in result.get("kyokus", []):
        tables: dict[str, list[Any]] = {field: [] for field in SNAPSHOT_FIELDS}
        # Identity avoids repeated encoding of the same immutable Python object.
        # Content deduplication also works with cache entries loaded from JSON,
        # where identical snapshots no longer share Python object identities.
        identity: dict[str, dict[int, int]] = {field: {} for field in SNAPSHOT_FIELDS}
        signatures: dict[str, dict[Any, int]] = {field: {} for field in SNAPSHOT_FIELDS}
        last_values: dict[str, Any] = {field: _MISSING for field in SNAPSHOT_FIELDS}
        last_indices: dict[str, int] = {}
        steps = []
        for step in kyoku.get("steps", []):
            compact_step = dict(step)
            for field in SNAPSHOT_FIELDS:
                value = step[field]
                obj_id = id(value)
                index = identity[field].get(obj_id)
                if index is None:
                    if last_values[field] is not _MISSING and value == last_values[field]:
                        index = last_indices[field]
                    else:
                        signature = _snapshot_signature(field, value)
                        index = signatures[field].get(signature)
                        if index is None:
                            index = len(tables[field])
                            signatures[field][signature] = index
                            tables[field].append(value)
                    identity[field][obj_id] = index
                last_values[field] = value
                last_indices[field] = index
                compact_step[field] = index
            steps.append(compact_step)
        rounds.append({**kyoku, "steps": steps, "snapshot_tables": tables})
    return {**result, "kyokus": rounds, "transport_version": 2}
