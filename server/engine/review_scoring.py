"""Score Mortal Q-value decisions and assemble public review statistics."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from .actions import softmax, event_to_label, label_to_action_dict, format_action_cn
from .model_policy import MODEL_REVISION, REVIEW_SCHEMA


def finalize_review(
    *,
    pending_decisions: list[dict[str, Any]],
    mortal_q_batch: Any,
    kyokus: list[dict[str, Any]],
    target_actor: int,
    temperature: float,
    progress_callback: Callable[[float, str], None] | None,
    names: list[str],
    meta: dict | None,
    mortal: Any,
    resolved_id: str,
    resolved_pth: Path,
) -> dict[str, Any]:
    # Finalize all decisions & compute summary statistics
    total_reviewed = 0
    total_matches = 0
    blunders = 0
    inaccuracies = 0
    raw_rating_sum = 0.0

    for d_idx, item in enumerate(pending_decisions):
        if progress_callback and (d_idx % 20 == 0 or d_idx == len(pending_decisions) - 1) and pending_decisions:
            pct = 85.0 + ((d_idx + 1) / len(pending_decisions)) * 14.0
            progress_callback(pct, f"正在生成决策建议 ({d_idx + 1}/{len(pending_decisions)})...")
        mask = item["mask"]
        legal_labels = np.flatnonzero(mask)
        q_full = mortal_q_batch[d_idx]

        q_legal = q_full[legal_labels]
        probs = softmax(q_legal, temperature=temperature)

        candidates = []
        for idx_in_legal, label in enumerate(legal_labels):
            label = int(label)
            act_dict = label_to_action_dict(
                label,
                target_actor,
                item["last_actor"],
                item["context_tile"],
                item["akas_in_hand"],
                item["ankan_cands"],
                item["kakan_cands"],
            )
            raw_q = float(q_legal[idx_in_legal])
            candidates.append(
                {
                    "label": int(label),
                    "action": act_dict,
                    "action_cn": format_action_cn(act_dict),
                    "pai": act_dict.get("pai"),
                    "raw_q": raw_q,
                    "q_value": round(raw_q, 3),
                    "prob": round(float(probs[idx_in_legal]) * 100.0, 2),
                }
            )

        # Primary sort strictly determined by raw unrounded Q-value descending
        candidates.sort(key=lambda c: c["raw_q"], reverse=True)
        for rank_i, c in enumerate(candidates):
            c["rank"] = rank_i + 1

        actual_label = item.get("actual_label")
        if actual_label is None:
            actual_label = event_to_label(item["actual_ev"])
        actual_cand = next((c for c in candidates if c["label"] == actual_label), candidates[0])
        best_cand = candidates[0]

        ambiguous_kan = bool(best_cand["label"] == 42 and actual_cand["label"] == 42 and best_cand["action"].get("ambiguous"))
        is_equal = None if ambiguous_kan else actual_cand["label"] == best_cand["label"]
        q_loss = max(0.0, best_cand["raw_q"] - actual_cand["raw_q"])
        rel_score = max(0.0, 1.0 - q_loss)
        # Verdict thresholds use the original candidate-range scale.
        q_min = min(c["raw_q"] for c in candidates)
        verdict_score = 1.0 if is_equal else (actual_cand["raw_q"] - q_min) / max(best_cand["raw_q"] - q_min, 1e-6)
        actual_prob = actual_cand["prob"]

        # Verdict classification (NAGA style)
        if ambiguous_kan:
            verdict = "unknown"
            verdict_cn = "杠牌未判定"
        elif is_equal or (actual_prob >= 35.0 and q_loss < 0.08):
            verdict = "best"
            verdict_cn = "一致"
        elif actual_prob >= 15.0 or verdict_score >= 0.72 or q_loss < 0.25:
            verdict = "ok"
            verdict_cn = "微差"
            inaccuracies += 1
        elif actual_prob >= 5.0 or verdict_score >= 0.42 or q_loss < 0.65:
            verdict = "question"
            verdict_cn = "疑问手"
            inaccuracies += 1
        else:
            verdict = "blunder"
            verdict_cn = "恶手"
            blunders += 1

        if not ambiguous_kan:
            total_reviewed += 1
            if is_equal:
                total_matches += 1
            raw_rating_sum += rel_score

        # Build tile -> probability map (0..36) for hand overlay bars
        tile_bars: dict[str, dict[str, Any]] = {}
        for c in candidates:
            if 0 <= c["label"] <= 36 and c["pai"]:
                tile_bars[c["pai"]] = {
                    "prob": c["prob"],
                }

        kyoku_obj = kyokus[item["kyoku_idx"]]
        dec_idx_in_kyoku = len(kyoku_obj["decisions"])
        decision_entry = {
            "decision_idx": dec_idx_in_kyoku,
            "global_idx": d_idx,
            "kyoku_idx": item["kyoku_idx"],
            "kyoku_label": kyoku_obj["label"],
            "step_idx": item["step_idx"],
            "junme": item["junme"],
            "tiles_left": item["tiles_left"],
            "at_furiten": item["at_furiten"],
            "decision_type": item["decision_type"],
            "context_tile": item["context_tile"],
            "expected": best_cand["action"],
            "expected_cn": best_cand["action_cn"],
            "actual": item["actual_ev"],
            "actual_cn": format_action_cn(item["actual_ev"]),
            "actual_rank": actual_cand["rank"],
            "actual_prob": actual_prob,
            "best_prob": best_cand["prob"],
            "q_loss": round(q_loss, 3),
            "rel_score": None if ambiguous_kan else round(rel_score * 100, 1),
            "is_equal": is_equal,
            "verdict": verdict,
            "verdict_cn": verdict_cn,
            "candidates": candidates,
            "tile_bars": tile_bars,
        }
        kyoku_obj["decisions"].append(decision_entry)

        # Attach decision_idx to the decision step only (tsumo or call response).
        # Do not attach to the following dahai step to avoid duplicate decision triggering after discard.
        step_idx = item["step_idx"]
        if 0 <= step_idx < len(kyoku_obj["steps"]):
            if kyoku_obj["steps"][step_idx]["decision_idx"] is None:
                kyoku_obj["steps"][step_idx]["decision_idx"] = dec_idx_in_kyoku

    # Compute overall rating & per-kyoku stats
    overall_rating = ((raw_rating_sum / total_reviewed) ** 2) * 100.0 if total_reviewed > 0 else 100.0
    overall_match_rate = (total_matches / total_reviewed) * 100.0 if total_reviewed > 0 else 100.0
    blunder_rate = (blunders / total_reviewed) * 100.0 if total_reviewed > 0 else 0.0

    if overall_rating >= 92:
        grade = "S+"
    elif overall_rating >= 87:
        grade = "S"
    elif overall_rating >= 82:
        grade = "A+"
    elif overall_rating >= 76:
        grade = "A"
    elif overall_rating >= 70:
        grade = "B+"
    elif overall_rating >= 62:
        grade = "B"
    else:
        grade = "C"

    for k in kyokus:
        decs = [d for d in k["decisions"] if d["is_equal"] is not None]
        k["unscored_count"] = len(k["decisions"]) - len(decs)
        k_tot = len(decs)
        k_match = sum(1 for d in decs if d["is_equal"])
        k_blunder = sum(1 for d in decs if d["verdict"] == "blunder")
        k_raw = sum(d["rel_score"] / 100.0 for d in decs)
        match_rate = round((k_match / k_tot) * 100.0, 1) if k_tot else None
        rating = round(((k_raw / k_tot) ** 2) * 100.0, 1) if k_tot else (None if k["unscored_count"] else 100.0)

        # Discard decisions vs Call response decisions breakdown
        discard_decs = [d for d in decs if d.get("decision_type") in ("self_turn", "discard_after_call", "riichi_discard")]
        call_decs = [d for d in decs if d.get("decision_type") == "call_response"]
        d_tot = len(discard_decs)
        d_match = sum(1 for d in discard_decs if d["is_equal"])
        c_tot = len(call_decs)
        c_match = sum(1 for d in call_decs if d["is_equal"])

        k["decision_count"] = k_tot
        k["match_count"] = k_match
        k["match_rate"] = match_rate
        k["blunder_count"] = k_blunder
        k["points_delta"] = k["score_deltas"][target_actor] if len(k["score_deltas"]) > target_actor else 0
        k["discard_count"] = d_tot
        k["discard_matches"] = d_match
        k["call_count"] = c_tot
        k["call_matches"] = c_match
        k["stats"] = {
            "total": k_tot,
            "matches": k_match,
            "match_rate": match_rate if match_rate is not None else (None if k["unscored_count"] else 100.0),
            "blunders": k_blunder,
            "rating": rating,
            "discard_total": d_tot,
            "discard_matches": d_match,
            "call_total": c_tot,
            "call_matches": c_match,
        }

    unscored_kan = sum(k["unscored_count"] for k in kyokus)
    no_scored_kan_only = total_reviewed == 0 and unscored_kan > 0
    if progress_callback:
        progress_callback(100.0, "牌谱分析全部完成")

    return {
        "target_actor": target_actor,
        "player_name": names[target_actor],
        "names": names,
        "player_names": names,
        "meta": meta or {},
        "engine": {
            "type": "mortal",
            "id": resolved_id,
            "tag": mortal.tag,
            "model_path": str(resolved_pth) if resolved_pth else None,
            "weight_file": resolved_pth.name,
            "weight_sha256": mortal.weight_sha256,
            "version": mortal.version,
            "steps": mortal.steps,
            "revision": MODEL_REVISION,
            "q_source": "neural_network",
            "review_schema": REVIEW_SCHEMA,
            "device": str(mortal.device).upper() if mortal is not None else "CPU",
        },
        "summary": {
            "total_reviewed": total_reviewed,
            "total_matches": total_matches,
            "match_rate": None if no_scored_kan_only else round(overall_match_rate, 1),
            "rating": None if no_scored_kan_only else round(overall_rating, 1),
            "grade": None if no_scored_kan_only else grade,
            "blunders": blunders,
            "blunder_rate": round(blunder_rate, 1),
            "inaccuracies": inaccuracies,
            "unscored_kan": unscored_kan,
        },
        "match_rate": None if no_scored_kan_only else round(overall_match_rate, 1),
        "blunders": blunders,
        "kyokus": kyokus,
    }
