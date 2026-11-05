"""Compact, opt-in SSE representation of immutable replay snapshots.

The canonical review and non-stream API keep their previous
schema at the API boundary. A compact result replaces repeated snapshot fields
with references to per-round tables, also used by the versioned disk cache.
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


def validate_compact_review(result: dict[str, Any]) -> None:
    """Validate all references before a cached V2 result can reach a client."""
    if type(result.get("transport_version")) is not int or result["transport_version"] != 2:
        raise ValueError("unsupported compact review")
    rounds = result.get("kyokus")
    if not isinstance(rounds, list):
        raise ValueError("invalid compact rounds")
    for kyoku in rounds:
        if not isinstance(kyoku, dict) or not isinstance(kyoku.get("steps"), list):
            raise ValueError("invalid compact round")
        tables = kyoku.get("snapshot_tables")
        if not isinstance(tables, dict):
            raise ValueError("missing snapshot tables")
        for field in SNAPSHOT_FIELDS:
            table = tables.get(field)
            if not isinstance(table, list) or any(not isinstance(value, list) for value in table):
                raise ValueError("invalid snapshot table")
            for step in kyoku["steps"]:
                index = step.get(field) if isinstance(step, dict) else None
                if type(index) is not int or not 0 <= index < len(table):
                    raise ValueError("invalid snapshot index")


def expand_review_snapshots(result: dict[str, Any]) -> dict[str, Any]:
    """Restore the legacy API shape, sharing arrays without mutating the cache."""
    if "transport_version" not in result:
        return result
    validate_compact_review(result)
    rounds = []
    for kyoku in result["kyokus"]:
        tables = kyoku["snapshot_tables"]
        expanded = {key: value for key, value in kyoku.items() if key != "snapshot_tables"}
        expanded["steps"] = [
            {**step, **{field: tables[field][step[field]] for field in SNAPSHOT_FIELDS}}
            for step in kyoku["steps"]
        ]
        rounds.append(expanded)
    return {**{key: value for key, value in result.items() if key != "transport_version"},
            "kyokus": rounds}
