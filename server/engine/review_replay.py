"""MJAI event replay, board snapshots and Mortal observation collection."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
import libriichi.state as lstate  # noqa: E402

from .actions import next_player_action, event_to_label
from .inference_batch import InferenceBatchBuffer
from .tiles import sort_key, deaka, tid
from .review_labels import BAKAZE_CN, _hora_score_description


INFERENCE_BATCH_SIZE = 128


class ReplayInferenceError(RuntimeError):
    """An ONNX batch failed while replay observations were being collected."""


def build_replay(
    mjai_events: list[dict], target_actor: int, obs_version: int,
    mortal: Any, meta: dict | None,
    progress_callback: Callable[[float, str], None] | None,
):
    names = (meta or {}).get("names") or ["东家", "南家", "西家", "北家"]
    ps = lstate.PlayerState(target_actor)

    kyokus: list[dict[str, Any]] = []
    cur_kyoku: dict[str, Any] | None = None

    # Board tracking state per kyoku
    hands: list[list[str]] = [[], [], [], []]
    drawn_tile: list[str | None] = [None, None, None, None]
    melds: list[list[dict]] = [[], [], [], []]
    rivers: list[list[dict]] = [[], [], [], []]
    riichi_declared: list[bool] = [False, False, False, False]
    scores: list[int] = [25000, 25000, 25000, 25000]
    dora_markers: list[str] = []
    kyotaku = 0
    honba = 0
    bakaze = "E"
    kyoku_num = 1
    oya = 0
    tiles_left = 70
    junme = 0
    pending_riichi_Discard: int | None = None
    last_actor = 0
    last_tile: str | None = None

    # Keep only one bounded observation batch in memory. Q outputs are much
    # smaller, so completed batches can be released immediately.
    pending_decisions: list[dict[str, Any]] = []
    # Estimate a useful batch capacity from event count. Allocate lazily on the
    # first decision and reuse the same contiguous buffers for subsequent batches.
    batch_capacity = min(INFERENCE_BATCH_SIZE, max(1, len(mjai_events) // 5))
    mortal_batch = InferenceBatchBuffer(batch_capacity) if mortal is not None else None
    mortal_q_chunks: list[np.ndarray] = []

    def flush_mortal_batch():
        if mortal_batch is None or mortal_batch.count == 0:
            return
        try:
            obs_batch, mask_batch = mortal_batch.view()
            q_batch = mortal.eval_batch(
                obs_batch, mask_batch, chunk_size=INFERENCE_BATCH_SIZE
            )
            if q_batch is None or len(q_batch) != mortal_batch.count:
                raise RuntimeError("ONNX 分批输出与当前观测数量不匹配")
        except Exception as exc:
            raise ReplayInferenceError(f"Mortal S42 ONNX 第 {len(mortal_q_chunks) + 1} 批推理失败") from exc

        mortal_q_chunks.append(q_batch)
        mortal_batch.clear()

    def queue_mortal_observation(obs: np.ndarray, mask: np.ndarray):
        if mortal_batch is not None and mortal_batch.append(obs, mask):
            flush_mortal_batch()
    # Snapshot containers are immutable after publication. Reuse them while
    # their source state is unchanged; JSON output remains byte-for-byte
    # equivalent while long replays allocate far fewer short-lived lists.
    sorted_hands: list[tuple[tuple[str, ...], list[str]] | None] = [None] * 4
    cached_hands_snapshot_key: tuple[tuple[str, ...], ...] | None = None
    cached_hands_snapshot: list[list[str]] | None = None
    cached_melds: list[tuple[int, list[dict]] | None] = [None] * 4
    cached_rivers: list[tuple[int, list[dict]] | None] = [None] * 4
    collection_snapshots: dict[str, tuple[tuple[int, ...], list[list[dict]]]] = {}
    state_list_snapshots: dict[str, tuple[tuple[Any, ...], list[Any]]] = {}
    meld_versions = [0] * 4
    river_versions = [0] * 4

    def frozen_state_list(name: str, source: list[Any]) -> list[Any]:
        state = tuple(source)
        saved = state_list_snapshots.get(name)
        if saved is None or saved[0] != state:
            saved = (state, list(source))
            state_list_snapshots[name] = saved
        return saved[1]

    def frozen_snapshot_lists(name: str, source: list[list[dict]],
                              versions: list[int], cache: list) -> list[list[dict]]:
        version_key = tuple(versions)
        saved_outer = collection_snapshots.get(name)
        if saved_outer is not None and saved_outer[0] == version_key:
            return saved_outer[1]
        snapshots = []
        for seat, items in enumerate(source):
            # Mutations increment a per-seat version, avoiding an O(n) tuple of
            # object identities for every board snapshot.
            version = versions[seat]
            saved = cache[seat]
            if saved is None or saved[0] != version:
                saved = (version, list(items))
                cache[seat] = saved
            snapshots.append(saved[1])
        collection_snapshots[name] = (version_key, snapshots)
        return snapshots

    def snapshot_board(ev: dict, event_idx: int) -> dict[str, Any]:
        nonlocal cached_hands_snapshot_key, cached_hands_snapshot
        for seat, hand in enumerate(hands):
            hand_state = tuple(hand)
            cached = sorted_hands[seat]
            if cached is None or cached[0] != hand_state:
                cached = (hand_state, sorted(hand, key=sort_key))
                sorted_hands[seat] = cached
        hand_key = tuple(cached[0] for cached in sorted_hands)
        if cached_hands_snapshot_key != hand_key:
            cached_hands_snapshot_key = hand_key
            cached_hands_snapshot = [cached[1] for cached in sorted_hands]
        return {
            "event": ev,
            # The same MJAI event keeps its identity across seat-specific snapshots.
            "event_idx": event_idx,
            "hands": cached_hands_snapshot,
            "drawn": frozen_state_list("drawn", drawn_tile),
            # Entries are replaced rather than mutated when calls or kans change them.
            "melds": frozen_snapshot_lists("melds", melds, meld_versions, cached_melds),
            "rivers": frozen_snapshot_lists("rivers", rivers, river_versions, cached_rivers),
            "riichi": frozen_state_list("riichi", riichi_declared),
            "scores": frozen_state_list("scores", scores),
            "dora_markers": frozen_state_list("dora_markers", dora_markers),
            "kyotaku": kyotaku,
            "honba": honba,
            "tiles_left": tiles_left,
            "junme": junme,
            "decision_idx": None,
        }

    def fold_drawn_into_hand(seat: int):
        if drawn_tile[seat] is not None:
            hands[seat].append(drawn_tile[seat])
            drawn_tile[seat] = None

    def remove_from_hand(seat: int, tile: str):
        fold_drawn_into_hand(seat)
        if tile in hands[seat]:
            hands[seat].remove(tile)
        else:
            base = deaka(tile)
            for idx, existing in enumerate(hands[seat]):
                if deaka(existing) == base:
                    del hands[seat][idx]
                    break

    for i, ev in enumerate(mjai_events):
        etype = ev["type"]
        if etype == "start_game" and ev.get("names"):
            names = list(ev["names"])
        ps.update(json.dumps(ev, separators=(",", ":")))

        if etype == "start_kyoku":
            bakaze = ev["bakaze"]
            kyoku_num = int(ev["kyoku"])
            honba = int(ev["honba"])
            kyotaku = int(ev["kyotaku"])
            oya = int(ev["oya"])
            scores = [int(x) for x in ev["scores"]]
            hands = [sorted(list(h), key=sort_key) for h in ev["tehais"]]
            drawn_tile = [None, None, None, None]
            melds = [[], [], [], []]
            rivers = [[], [], [], []]
            meld_versions = [version + 1 for version in meld_versions]
            river_versions = [version + 1 for version in river_versions]
            riichi_declared = [False, False, False, False]
            dora_markers = [ev["dora_marker"]]
            tiles_left = 70
            junme = 0
            pending_riichi_Discard = None
            last_actor = oya
            last_tile = None

            label = f"{BAKAZE_CN.get(bakaze, bakaze)}{kyoku_num}局"
            if honba > 0:
                label += f" {honba}本场"
            if progress_callback:
                pct = 15.0 + (i / max(1, len(mjai_events))) * 65.0
                progress_callback(pct, f"正在检讨 {label} (Mortal)...")
            cur_kyoku = {
                "kyoku_idx": len(kyokus),
                "bakaze": bakaze,
                "kyoku_num": kyoku_num,
                "honba": honba,
                "kyotaku": kyotaku,
                "oya": oya,
                "label": label,
                "init_scores": list(scores),
                "end_scores": list(scores),
                "score_deltas": [0, 0, 0, 0],
                "end_summaries": [],
                "steps": [],
                "decisions": [],
            }
            cur_kyoku["steps"].append(snapshot_board(ev, i))
            continue

        if cur_kyoku is None:
            continue

        if etype == "end_kyoku":
            cur_kyoku["end_scores"] = list(scores)
            cur_kyoku["score_deltas"] = [cur_kyoku["end_scores"][s] - cur_kyoku["init_scores"][s] for s in range(4)]
            kyokus.append(cur_kyoku)
            cur_kyoku = None
            continue

        if etype == "dora":
            dora_markers.append(ev["dora_marker"])
            if cur_kyoku["steps"]:
                cur_kyoku["steps"][-1]["dora_markers"] = frozen_state_list("dora_markers", dora_markers)
            continue

        if etype == "reach_accepted":
            r_seat = int(ev["actor"])
            scores[r_seat] -= 1000
            kyotaku += 1
            cur_kyoku["end_scores"] = list(scores)
            cur_kyoku["score_deltas"][r_seat] -= 1000
            if cur_kyoku["steps"]:
                cur_kyoku["steps"][-1]["scores"] = frozen_state_list("scores", scores)
                cur_kyoku["steps"][-1]["kyotaku"] = kyotaku
            continue

        if etype == "reach":
            r_seat = int(ev["actor"])
            riichi_declared[r_seat] = True
            pending_riichi_Discard = r_seat
            # We do not add a separate visual board step for `reach`, except we
            # still check if target_actor has multiple tenpai discard choices.
            if r_seat == target_actor and ps.last_cans.can_act:
                obs_4, mask_4 = ps.encode_obs(4, False)
                if int(mask_4.sum()) > 1:
                    actual_ev = next_player_action(mjai_events, target_actor, False, False, False, i + 1)
                    if actual_ev is not None and actual_ev.get("type") == "dahai":
                        obs_m = obs_4 if obs_version == 4 else ps.encode_obs(obs_version, False)[0]
                        # Separate the riichi declaration from its discard choice,
                        # so both decisions have an independently navigable step.
                        step_ref = len(cur_kyoku["steps"])
                        cur_kyoku["steps"].append(snapshot_board(ev, i))
                        pending_decisions.append(
                            {
                                "kyoku_idx": cur_kyoku["kyoku_idx"],
                                "step_idx": step_ref,
                                "junme": junme,
                                "tiles_left": tiles_left,
                                "at_furiten": bool(ps.at_furiten),
                                "decision_type": "riichi_discard",
                                "context_tile": last_tile,
                                "last_actor": last_actor,
                                "actual_ev": actual_ev,
                                "mask": mask_4,
                                "akas_in_hand": list(ps.akas_in_hand),
                                "ankan_cands": ps.ankan_candidates(),
                                "kakan_cands": ps.kakan_candidates(),
                            }
                        )
                        queue_mortal_observation(obs_m, mask_4)
            continue

        # Update board state for normal events
        if etype == "tsumo":
            seat = int(ev["actor"])
            fold_drawn_into_hand(seat)
            drawn_tile[seat] = ev["pai"]
            tiles_left = max(0, tiles_left - 1)
            if seat == target_actor:
                junme += 1
            last_actor = seat
            last_tile = ev["pai"]

        elif etype == "dahai":
            seat = int(ev["actor"])
            pai = ev["pai"]
            remove_from_hand(seat, pai)
            is_r = pending_riichi_Discard == seat
            if is_r:
                pending_riichi_Discard = None
            rivers[seat].append(
                {
                    "pai": pai,
                    "tsumogiri": bool(ev.get("tsumogiri")),
                    "riichi": is_r,
                    "called": False,
                    "step": len(cur_kyoku["steps"]),
                }
            )
            river_versions[seat] += 1
            last_actor = seat
            last_tile = pai

        elif etype in ("chi", "pon", "daiminkan"):
            seat = int(ev["actor"])
            target = int(ev["target"])
            pai = ev["pai"]
            consumed = list(ev["consumed"])
            for c in consumed:
                remove_from_hand(seat, c)
            if rivers[target]:
                # Bind the actual call to this discard at conversion time.
                # Identical tiles can be called repeatedly later in one round.
                rivers[target][-1] = {
                    **rivers[target][-1], "called": True,
                    "call_actor": seat, "call_type": etype,
                }
                river_versions[target] += 1
            melds[seat].append({"type": etype, "pai": pai, "consumed": consumed, "from": target, "tiles": [pai] + consumed})
            meld_versions[seat] += 1
            if seat == target_actor and etype in ("chi", "pon"):
                junme += 1
            last_actor = seat

        elif etype == "ankan":
            seat = int(ev["actor"])
            consumed = list(ev["consumed"])
            for c in consumed:
                remove_from_hand(seat, c)
            melds[seat].append({"type": "ankan", "pai": consumed[0], "consumed": consumed, "from": seat, "tiles": consumed})
            meld_versions[seat] += 1
            last_actor = seat
            last_tile = consumed[0]

        elif etype == "kakan":
            seat = int(ev["actor"])
            pai = ev["pai"]
            remove_from_hand(seat, pai)
            base = deaka(pai)
            for meld_idx, m in enumerate(melds[seat]):
                if m["type"] == "pon" and deaka(m["pai"]) == base:
                    melds[seat][meld_idx] = {**m, "type": "kakan", "added": pai, "tiles": [*m["tiles"], pai]}
                    meld_versions[seat] += 1
                    break
            last_actor = seat
            last_tile = pai

        elif etype == "hora":
            seat = int(ev["actor"])
            target = int(ev["target"])
            deltas = ev.get("deltas")
            if deltas:
                for s in range(4):
                    scores[s] += int(deltas[s])
                    cur_kyoku["score_deltas"][s] += int(deltas[s])
                cur_kyoku["end_scores"] = list(scores)
            who = names[seat]
            pts = f"{deltas[seat]:+d}" if deltas else ""
            score_desc = _hora_score_description(ev)
            if seat == target:
                cur_kyoku["end_summaries"].append(f"{who} 自摸 {score_desc}{pts}")
            else:
                cur_kyoku["end_summaries"].append(f"{who} 荣和 {names[target]} {score_desc}{pts}")

        elif etype == "ryukyoku":
            deltas = ev.get("deltas")
            if deltas:
                for s in range(4):
                    scores[s] += int(deltas[s])
                    cur_kyoku["score_deltas"][s] += int(deltas[s])
                cur_kyoku["end_scores"] = list(scores)
            reason = ev.get("majsoul", {}).get("reason")
            label = {
                "exhaustive": "荒牌流局",
                "nagashi_mangan": "流局满贯",
                "kyushu_kyuhai": "九种九牌",
                "suufon_renda": "四风连打",
                "suukan_sanra": "四杠散了",
                "suucha_riichi": "四家立直",
            }.get(reason, "流局")
            cur_kyoku["end_summaries"].append(label)

        step_idx = len(cur_kyoku["steps"])
        step_snap = snapshot_board(ev, i)
        cur_kyoku["steps"].append(step_snap)

        # Check if `target_actor` has a decision at this exact step
        cans = ps.last_cans
        if not cans.can_act:
            continue

        obs_4, mask_4 = ps.encode_obs(4, False)
        if int(mask_4.sum()) <= 1:
            continue

        can_pon_or_kan = bool(mask_4[41] or mask_4[42])
        can_agari = bool(mask_4[43])
        can_ryukyoku = bool(mask_4[44])
        actual_ev = next_player_action(mjai_events, target_actor, can_pon_or_kan, can_agari, can_ryukyoku, i + 1)
        if actual_ev is None:
            continue

        actual_label = event_to_label(actual_ev)
        if not mask_4[actual_label]:
            # Occasionallyaka vs non-aka discard mismatch if tile_of_action_idx differs; fallback to deaka
            if actual_ev.get("type") == "dahai":
                alt = tid(actual_ev["pai"])
                if mask_4[alt]:
                    actual_label = alt
                else:
                    continue
            else:
                continue

        obs_m = obs_4 if obs_version == 4 else ps.encode_obs(obs_version, False)[0]
        if cans.can_discard:
            dtype = "discard_after_call" if etype in ("chi", "pon") else "self_turn"
        else:
            dtype = "call_response"

        pending_decisions.append(
            {
                "kyoku_idx": cur_kyoku["kyoku_idx"],
                "step_idx": step_idx,
                "junme": junme,
                "tiles_left": tiles_left,
                "at_furiten": bool(ps.at_furiten),
                "decision_type": dtype,
                "context_tile": last_tile,
                "last_actor": last_actor,
                "actual_ev": actual_ev,
                "actual_label": actual_label,
                "mask": mask_4,
                "akas_in_hand": list(ps.akas_in_hand),
                "ankan_cands": ps.ankan_candidates(),
                "kakan_cands": ps.kakan_candidates(),
            }
        )
        queue_mortal_observation(obs_m, mask_4)

    if cur_kyoku is not None:
        cur_kyoku["end_scores"] = list(scores)
        cur_kyoku["score_deltas"] = [cur_kyoku["end_scores"][s] - cur_kyoku["init_scores"][s] for s in range(4)]
        kyokus.append(cur_kyoku)

    if mortal is not None:
        flush_mortal_batch()
        if not mortal_q_chunks:
            mortal_q_batch = np.empty((0, 46), dtype=np.float32)
        elif len(mortal_q_chunks) == 1:
            mortal_q_batch = mortal_q_chunks[0]
        else:
            mortal_q_batch = np.concatenate(mortal_q_chunks, axis=0)
    else:
        mortal_q_batch = None

    return kyokus, pending_decisions, mortal_q_batch, names
