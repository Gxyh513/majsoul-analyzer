"""Core review + replay builder for Mahjong Soul 4-player logs.

Supports two backends out of the box:
1. **Mortal Neural Network** (`mortal.pth` via PyTorch + `libriichi` Rust state/obs encoder)
2. **Built-in SP+Defense Engine** (`libriichi` Rust `SPCalculator` exact multi-turn EV /
   win-prob / tenpai-prob + shanten/ukeire + push/fold defense & meld evaluator)

Both backends produce the exact same NAGA / Mortal style output schema, plus
full step-by-step 4-player board replay snapshots for the interactive web UI.
"""
from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import libriichi.state as lstate  # noqa: E402

from .mortal_model import LoadedMortal  # noqa: E402
from .shanten import shanten as py_shanten  # noqa: E402
from .shanten import ukeire as py_ukeire  # noqa: E402
from .tiles import (  # noqa: E402
    ACTION_NAMES_CN,
    HONOR_CN,
    action_idx_of_tile,
    action_label,
    deaka,
    dora_from_marker,
    is_aka,
    is_yaochu,
    sort_key,
    tid,
    tile_cn,
    tile_of_action_idx,
    tname,
)

log = logging.getLogger(__name__)

BAKAZE_CN = {"E": "东", "S": "南", "W": "西", "N": "北"}
SEAT_WIND_CN = ["东", "南", "西", "北"]


# ---------------------------------------------------------------------------
# Model Manager (hot-loads mortal.pth if present)
# ---------------------------------------------------------------------------
class EngineManager:
    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.custom_pth: Path | None = None
        self.selected_model: str = "auto"
        self._loaded: LoadedMortal | None = None
        self._loaded_mtime: float | None = None
        self._load_error: str | None = None

    def list_available_models(self) -> list[dict[str, str]]:
        models = []
        akagi = self.models_dir / "akagi.pth"
        if akagi.is_file():
            models.append({"id": "akagi", "name": "赤木 Akagi (v4 582k步 进攻强化)", "path": str(akagi)})
        mortal = self.models_dir / "mortal.pth"
        if mortal.is_file():
            models.append({"id": "mortal", "name": "Mortal 官方核心 (v3 天凤十段)", "path": str(mortal)})
        for p in sorted(self.models_dir.glob("*.pth")):
            if p.name not in ("akagi.pth", "mortal.pth") and "grp" not in p.name.lower():
                models.append({"id": p.stem, "name": f"自定义模型 ({p.name})", "path": str(p)})
        return models

    def find_pth(self) -> Path | None:
        if self.custom_pth and self.custom_pth.is_file():
            return self.custom_pth
        if self.selected_model == "akagi":
            p = self.models_dir / "akagi.pth"
            if p.is_file():
                return p
        elif self.selected_model == "mortal":
            p = self.models_dir / "mortal.pth"
            if p.is_file():
                return p
        akagi = self.models_dir / "akagi.pth"
        if akagi.is_file():
            return akagi
        mortal = self.models_dir / "mortal.pth"
        if mortal.is_file():
            return mortal
        pths = sorted(self.models_dir.glob("*.pth"))
        for p in pths:
            if "grp" not in p.name.lower():
                return p
        return None

    def get_mortal(self) -> LoadedMortal | None:
        pth = self.find_pth()
        if pth is None:
            self._loaded = None
            return None
        mtime = pth.stat().st_mtime
        if self._loaded is not None and self._loaded.pth_path == pth and self._loaded_mtime == mtime:
            return self._loaded
        try:
            self._loaded = LoadedMortal(pth)
            self._loaded_mtime = mtime
            self._load_error = None
            return self._loaded
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to load Mortal model %s: %s", pth, e)
            self._load_error = str(e)
            self._loaded = None
            return None

    def status(self) -> dict[str, Any]:
        pth = self.find_pth()
        mortal = self.get_mortal()
        model_name = "赤木 Akagi" if (pth and "akagi" in pth.name.lower()) else ("Mortal" if mortal is not None else "内置引擎")
        return {
            "has_mortal_weights": mortal is not None,
            "active_engine": "mortal" if mortal is not None else "builtin",
            "model_name": model_name,
            "model_tag": mortal.tag if mortal is not None else "Built-in SP+Defense (libriichi Rust)",
            "model_path": str(pth) if pth else str(self.models_dir / "mortal.pth"),
            "models_dir": str(self.models_dir),
            "device": str(mortal.device).upper() if mortal is not None else "CPU",
            "selected_model": self.selected_model,
            "available_models": self.list_available_models(),
            "load_error": self._load_error,
        }


# ---------------------------------------------------------------------------
# Softmax & action helpers (ported from mjai-reviewer/src/review/mortal.rs)
# ---------------------------------------------------------------------------
def softmax(q: np.ndarray, temperature: float = 0.1) -> np.ndarray:
    if len(q) == 0:
        return q
    t = max(float(temperature), 1e-4)
    x = (q.astype(np.float64) - float(np.max(q))) / t
    x = np.clip(x, -60.0, 0.0)
    e = np.exp(x)
    s = float(e.sum())
    return (e / s).astype(np.float32) if s > 0 else np.full_like(q, 1.0 / len(q))


def event_to_label(ev: dict) -> int:
    t = ev["type"]
    if t == "dahai":
        return action_idx_of_tile(ev["pai"])
    if t == "reach":
        return 37
    if t == "chi":
        c0, c1 = tid(ev["consumed"][0]), tid(ev["consumed"][1])
        mn, mx = min(c0, c1), max(c0, c1)
        x = tid(ev["pai"])
        if x < mn:
            return 38  # low (e.g. 3m with 4m5m)
        if x < mx:
            return 39  # mid (e.g. 4m with 3m5m)
        return 40  # high (e.g. 5m with 3m4m)
    if t == "pon":
        return 41
    if t in ("daiminkan", "ankan", "kakan"):
        return 42
    if t == "hora":
        return 43
    if t == "ryukyoku":
        return 44
    return 45  # none / pass


def next_player_action(
    events: list[dict],
    player_id: int,
    can_pon_or_daiminkan: bool,
    can_agari: bool,
    can_ryukyoku: bool,
) -> dict | None:
    """Port of `next_action` from `mjai-reviewer/src/review/mortal.rs`."""
    if not events:
        return None
    ev = events[0]
    t = ev["type"]
    if t in ("dora", "reach_accepted"):
        return next_player_action(events[1:], player_id, can_pon_or_daiminkan, can_agari, can_ryukyoku)
    if t in ("end_kyoku", "end_game", "start_kyoku"):
        return None
    if t == "tsumo":
        return {"type": "none"}
    if t == "hora":
        for a in events[:3]:
            if a["type"] == "hora" and a.get("actor") == player_id:
                return a
        return {"type": "none"} if can_agari else None
    if t == "ryukyoku":
        return ev if can_ryukyoku else None
    actor = ev.get("actor")
    if actor is not None and actor != player_id:
        if can_agari or can_pon_or_daiminkan:
            return {"type": "none"}
        return None
    return ev


def label_to_action_dict(
    label: int,
    actor: int,
    target: int,
    last_tile: str | None,
    akas_in_hand: list[bool],
    ankan_cands: list[str],
    kakan_cands: list[str],
) -> dict:
    if 0 <= label <= 36:
        pai = tile_of_action_idx(label)
        return {
            "type": "dahai",
            "actor": actor,
            "pai": pai,
            "tsumogiri": last_tile == pai,
        }
    if label == 37:
        return {"type": "reach", "actor": actor}
    if label in (38, 39, 40) and last_tile is not None:
        x = tid(last_tile)
        suit = x // 9
        has_aka = akas_in_hand[suit] if suit < 3 else False
        if label == 38:
            pair = [x + 1, x + 2]
        elif label == 39:
            pair = [x - 1, x + 1]
        else:
            pair = [x - 2, x - 1]
        consumed = []
        for idx34 in pair:
            base = tname(idx34)
            if has_aka and idx34 % 9 == 4:
                consumed.append(base + "r")
            else:
                consumed.append(base)
        return {"type": "chi", "actor": actor, "target": target, "pai": last_tile, "consumed": consumed}
    if label == 41 and last_tile is not None:
        x = tid(last_tile)
        suit = x // 9
        base = deaka(last_tile)
        has_aka = akas_in_hand[suit] if suit < 3 else False
        if has_aka and x % 9 == 4 and not is_aka(last_tile):
            consumed = [base + "r", base]
        else:
            consumed = [base, base]
        return {"type": "pon", "actor": actor, "target": target, "pai": last_tile, "consumed": consumed}
    if label == 42:
        if ankan_cands:
            c = ankan_cands[0]
            return {"type": "ankan", "actor": actor, "consumed": [c] * 4}
        if kakan_cands:
            c = kakan_cands[0]
            return {"type": "kakan", "actor": actor, "pai": c, "consumed": [c] * 3}
        base = deaka(last_tile or "1m")
        return {"type": "daiminkan", "actor": actor, "target": target, "pai": last_tile, "consumed": [base] * 3}
    if label == 43:
        return {"type": "hora", "actor": actor, "target": target}
    if label == 44:
        return {"type": "ryukyoku"}
    return {"type": "none"}


def format_action_cn(ev: dict) -> str:
    t = ev.get("type", "none")
    if t == "dahai":
        return f"切 {tile_cn(ev['pai'])}" + (" (摸切)" if ev.get("tsumogiri") else "")
    if t == "reach":
        return "立直"
    if t == "chi":
        c = "".join(tile_cn(x) for x in ev.get("consumed", []))
        return f"吃 {tile_cn(ev['pai'])} (用 {c})"
    if t == "pon":
        return f"碰 {tile_cn(ev['pai'])}"
    if t == "ankan":
        c = (ev.get("consumed") or ["?"])[0]
        return f"暗杠 {tile_cn(c)}"
    if t == "kakan":
        return f"加杠 {tile_cn(ev.get('pai', '?'))}"
    if t == "daiminkan":
        return f"大明杠 {tile_cn(ev.get('pai', '?'))}"
    if t == "hora":
        return "自摸" if ev.get("actor") == ev.get("target") else "荣和"
    if t == "ryukyoku":
        return "九种九牌流局"
    return "跳过 (Pass)"


def short_action_cn(ev: dict) -> str:
    t = ev.get("type", "none")
    if t == "dahai":
        return f"切{tile_cn(ev['pai'])}"
    if t == "reach":
        return "立直"
    if t == "chi":
        c = "".join(x[0] for x in ev.get("consumed", []))
        return f"吃({c})"
    if t == "pon":
        return f"碰{tile_cn(ev['pai'])}"
    if t in ("ankan", "kakan", "daiminkan"):
        p = ev.get("pai") or (ev.get("consumed") or ["?"])[0]
        return f"杠{tile_cn(p)}"
    if t == "hora":
        return "和了"
    if t == "ryukyoku":
        return "流局"
    return "跳过"


# ---------------------------------------------------------------------------
# Defense & Heuristic Evaluator (uses libriichi v4 obs + board state)
# ---------------------------------------------------------------------------
def compute_tile_danger(
    t34: int,
    is_red: bool,
    target_actor: int,
    rivers: list[list[dict]],
    riichi_step: list[int | None],
    melds: list[list[dict]],
    dora_T34s: list[int],
    tiles_seen: list[int],
) -> tuple[float, list[str]]:
    """Compute danger score [0.0, 1.0] and descriptive safety/danger tags for tile `t34`."""
    threatening_opps = []
    for seat in range(4):
        if seat == target_actor:
            continue
        if riichi_step[seat] is not None:
            threatening_opps.append((seat, 1.0, True))
        elif len(melds[seat]) >= 2 and len(rivers[seat]) >= 6:
            threatening_opps.append((seat, 0.45 * (len(melds[seat]) - 1), False))

    if not threatening_opps:
        return 0.0, []

    dora_cnt = sum(1 for d in dora_T34s if d == t34) + (1 if is_red else 0)
    max_danger = 0.0
    tags: list[str] = []

    for opp, weight, is_riichi in threatening_opps:
        # Build safe tiles (genbutsu) against `opp`
        safe: set[int] = set()
        for item in rivers[opp]:
            safe.add(tid(item["pai"]))
        if is_riichi and riichi_step[opp] is not None:
            r_step = riichi_step[opp]
            # Any tile discarded by any player after opp's riichi step is also genbutsu
            for s in range(4):
                for item in rivers[s]:
                    if item.get("step", 0) >= r_step:
                        safe.add(tid(item["pai"]))

        if t34 in safe:
            if is_riichi and "现物" not in tags:
                tags.append("现物")
            continue

        # Tile is not genbutsu against this threatening opponent
        if t34 >= 27:
            seen = tiles_seen[t34]
            if seen >= 4:
                base = 0.02
                if is_riichi and "绝张字牌" not in tags:
                    tags.append("绝张字牌")
            elif seen == 3:
                base = 0.06
                if is_riichi and "二枚切字" not in tags:
                    tags.append("二枚切字")
            elif seen == 2:
                base = 0.14
            else:
                base = 0.26
                if is_riichi and "生张字牌" not in tags:
                    tags.append("生张字牌")
        else:
            r = t34 % 9
            s_base = t34 - r
            # Kabe (all 4 seen of adjacent connector)
            def kabe(idx_in_suit: int) -> bool:
                return 0 <= idx_in_suit < 9 and tiles_seen[s_base + idx_in_suit] >= 4

            if r == 0:
                suji = (s_base + 3) in safe
                wall = kabe(1) or kabe(2)
                base = 0.05 if wall else (0.10 if suji else 0.22)
                if is_riichi:
                    tags.append("壁牌" if wall else ("筋19" if suji else "无筋19"))
            elif r == 8:
                suji = (s_base + 5) in safe
                wall = kabe(7) or kabe(6)
                base = 0.05 if wall else (0.10 if suji else 0.22)
                if is_riichi:
                    tags.append("壁牌" if wall else ("筋19" if suji else "无筋19"))
            elif r in (1, 7):
                suji = (s_base + (4 if r == 1 else 4)) in safe
                wall = kabe(2) if r == 1 else kabe(6)
                base = 0.07 if wall else (0.14 if suji else 0.34)
                if is_riichi:
                    tags.append("筋28" if suji else "无筋28")
            elif r in (2, 6):
                suji = (s_base + (5 if r == 2 else 3)) in safe
                base = 0.16 if suji else 0.38
                if is_riichi:
                    tags.append("筋37" if suji else "无筋37")
            else:
                # 4, 5, 6 (r = 3, 4, 5)
                left_safe = (s_base + r - 3) in safe
                right_safe = (s_base + r + 3) in safe
                if left_safe and right_safe:
                    base = 0.12
                    if is_riichi:
                        tags.append("中筋456")
                elif left_safe or right_safe:
                    base = 0.27
                    if is_riichi:
                        tags.append("片筋456")
                else:
                    base = 0.48
                    if is_riichi:
                        tags.append("无筋456")

        base *= 1.0 + 0.22 * dora_cnt
        max_danger = max(max_danger, min(1.0, base * weight))

    # Deduplicate tags preserving order
    dedup = []
    for t in tags:
        if t not in dedup:
            dedup.append(t)
    return round(float(max_danger), 3), dedup[:2]


def analyze_candidates_and_heuristic_q(
    ps: Any,
    obs: np.ndarray,
    mask: np.ndarray,
    target_actor: int,
    bakaze: str,
    oya: int,
    rivers: list[list[dict]],
    riichi_step: list[int | None],
    melds: list[list[dict]],
    dora_markers: list[str],
    tiles_left: int,
) -> tuple[np.ndarray, dict[int, dict[str, Any]]]:
    """Compute per-action metadata (shanten, ukeire, EV, win_prob, danger, tags)
    AND calibrated heuristic Q-values for all legal actions in `mask`.
    """
    legal = np.nonzero(mask)[0].tolist()
    q = np.full(46, -np.inf, dtype=np.float32)
    meta_by_action: dict[int, dict[str, Any]] = {}

    cans = ps.last_cans
    tehai = list(ps.tehai)
    akas = list(ps.akas_in_hand)
    n_melds = len(ps.chis) + len(ps.pons) + len(ps.minkans) + len(ps.ankans)
    dora_t34s = [dora_from_marker(m) for m in dora_markers]
    tiles_seen = [int(round(float(obs[835, i]) * 4.0)) for i in range(34)]
    rem = [max(0, 4 - tiles_seen[i]) for i in range(34)]

    max_ev = float(obs[889, 0]) * 100000.0
    has_next_shanten = bool(obs[876].any())
    cur_shanten = int(ps.shanten)
    real_shanten = (cur_shanten - 1) if (cans.can_discard and cur_shanten > 0 and has_next_shanten) else cur_shanten

    num_opp_riichi = sum(1 for s in range(4) if s != target_actor and riichi_step[s] is not None)
    any_opp_threat = num_opp_riichi > 0 or any(
        s != target_actor and len(melds[s]) >= 2 and len(rivers[s]) >= 6 for s in range(4)
    )

    jikaze_idx = 27 + (target_actor - oya) % 4
    bakaze_idx = 27 + "ESWN".index(bakaze)
    yakuhai_set = {bakaze_idx, jikaze_idx, 31, 32, 33}

    # --- 1. Evaluate discard actions (0..36) ---
    discard_actions = [a for a in legal if a <= 36]
    for a in discard_actions:
        t34 = tid(tile_of_action_idx(a))
        is_red = a >= 34
        is_next = bool(obs[876, t34] > 0.5)
        is_keep = bool(obs[875, t34] > 0.5)
        is_tenpai = bool(obs[877, t34] > 0.5)

        if cur_shanten > 0:
            if is_next:
                shanten_after = cur_shanten - 1
            elif is_keep:
                shanten_after = cur_shanten
            else:
                shanten_after = cur_shanten + 1
        else:
            shanten_after = 0 if (is_keep or is_next or ps.self_riichi_accepted) else 1

        # Ukeire from libriichi SP table (rows 891..959) or fallback to shanten.py
        req_keep = obs[891 + t34]
        req_down = obs[925 + t34]
        if req_keep.sum() > 0:
            req_row = req_keep
        elif req_down.sum() > 0:
            req_row = req_down
        else:
            req_row = None

        if req_row is not None:
            uk_indices = [u for u in range(34) if req_row[u] > 0.5]
            uk_count = sum(rem[u] for u in uk_indices)
            uk_tiles = [{"pai": tname(u), "count": rem[u]} for u in uk_indices if rem[u] > 0]
        else:
            c_copy = list(tehai)
            c_copy[t34] = max(0, c_copy[t34] - 1)
            uk_count, uk_indices = py_ukeire(c_copy, n_melds, tiles_seen, shanten_after)
            uk_tiles = [{"pai": tname(u), "count": rem[u]} for u in uk_indices if rem[u] > 0]

        tenpai_prob = float(obs[961, t34])
        win_prob = float(obs[978, t34])
        ev = float(obs[995, t34]) * max_ev

        danger, def_tags = compute_tile_danger(
            t34, is_red, target_actor, rivers, riichi_step, melds, dora_t34s, tiles_seen
        )

        tags: list[str] = []
        if shanten_after > real_shanten:
            tags.append("退向")
        elif is_tenpai and shanten_after == 0:
            tags.append("听牌")
        tags.extend(def_tags)

        # Calibrated heuristic Q-value for discard `a`
        dora_loss = sum(1 for d in dora_t34s if d == t34) + (1.2 if is_red else 0.0)
        if ev > 0:
            attack_q = (ev / 2400.0) + (win_prob * 2.8) + (tenpai_prob * 0.7) + (uk_count * 0.035)
            # Red 5 vs normal 5 tie-breaker (SP table is indexed by 34-tile, so penalize discarding red 5)
            if is_red:
                attack_q -= 0.45
        else:
            shanten_diff = real_shanten - shanten_after
            attack_q = shanten_diff * 1.9 + uk_count * 0.065 - dora_loss * 0.28
            # Positional value tie-breaker for isolated tiles
            if t34 >= 27:
                if t34 not in yakuhai_set and tehai[t34] == 1:
                    attack_q += 0.12  # prefer discarding guest winds first
                elif t34 in yakuhai_set and tehai[t34] == 1:
                    attack_q += 0.04
            else:
                r = t34 % 9
                if r in (0, 8) and tehai[t34] == 1:
                    attack_q += 0.05

        # Push / fold adjustment
        if any_opp_threat:
            if shanten_after >= 2:
                fold_w = 4.2 * max(1, num_opp_riichi)
            elif shanten_after == 1:
                fold_w = (2.2 if ev >= 6000 else 3.4) * max(1, num_opp_riichi)
            else:
                fold_w = (1.1 if ev >= 5000 else 1.8) * max(1, num_opp_riichi)
            score_q = attack_q - fold_w * danger
        else:
            score_q = attack_q - 0.15 * danger

        q[a] = score_q
        meta_by_action[a] = {
            "shanten_after": shanten_after,
            "ukeire": uk_count,
            "ukeire_tiles": uk_tiles[:10],
            "ev": round(ev, 0),
            "win_prob": round(win_prob * 100, 1),
            "tenpai_prob": round(tenpai_prob * 100, 1),
            "danger": round(danger * 100, 1),
            "tags": tags,
        }

    # Add efficiency / EV tags to top discard candidates
    if discard_actions:
        max_uk = max((meta_by_action[a]["ukeire"] for a in discard_actions if meta_by_action[a]["shanten_after"] == real_shanten), default=0)
        max_ev_cand = max((meta_by_action[a]["ev"] for a in discard_actions), default=0)
        for a in discard_actions:
            m = meta_by_action[a]
            if m["shanten_after"] == real_shanten and max_uk > 0 and m["ukeire"] == max_uk:
                if "进张最广" not in m["tags"]:
                    m["tags"].insert(0, "进张最广")
            if max_ev_cand >= 1000 and m["ev"] == max_ev_cand and "打点最优" not in m["tags"]:
                m["tags"].append("打点最优")

    best_discard_q = float(np.max(q[discard_actions])) if discard_actions else 0.0

    # --- 2. Evaluate Riichi (37) ---
    if 37 in legal:
        tenpai_discards = [a for a in discard_actions if meta_by_action[a]["shanten_after"] == 0]
        best_uk = max((meta_by_action[a]["ukeire"] for a in tenpai_discards), default=0)
        min_danger = min((meta_by_action[a]["danger"] for a in tenpai_discards), default=0.0)
        if best_uk >= 2 and tiles_left >= 8 and not (num_opp_riichi >= 2 and min_danger > 25):
            q[37] = best_discard_q + 0.55
            r_tags = ["推荐立直"]
        elif best_uk >= 1 and tiles_left >= 4:
            q[37] = best_discard_q + 0.15
            r_tags = ["可立直"]
        else:
            q[37] = best_discard_q - 0.35
            r_tags = ["建议默听"]
        meta_by_action[37] = {
            "shanten_after": 0,
            "ukeire": best_uk,
            "ukeire_tiles": [],
            "ev": round(max_ev, 0),
            "win_prob": round(max((meta_by_action[a]["win_prob"] for a in tenpai_discards), default=0.0), 1),
            "tenpai_prob": 100.0,
            "danger": min_danger,
            "tags": r_tags,
        }

    # --- 3. Evaluate Kan (42), Agari (43), Ryukyoku (44) ---
    if 43 in legal:
        q[43] = max(best_discard_q, 0.0) + 4.0
        meta_by_action[43] = {
            "shanten_after": -1,
            "ukeire": 0,
            "ukeire_tiles": [],
            "ev": round(max_ev, 0),
            "win_prob": 100.0,
            "tenpai_prob": 100.0,
            "danger": 0.0,
            "tags": ["和牌"],
        }

    if 44 in legal:
        yaochu_cnt = sum(1 for i in range(34) if is_yaochu(i) and tehai[i] > 0)
        q[44] = best_discard_q + (0.6 if real_shanten >= 3 and yaochu_cnt < 10 else -0.8)
        meta_by_action[44] = {
            "shanten_after": real_shanten,
            "ukeire": 0,
            "ukeire_tiles": [],
            "ev": 0,
            "win_prob": 0.0,
            "tenpai_prob": 0.0,
            "danger": 0.0,
            "tags": ["九种九牌"],
        }

    if 42 in legal:
        if cans.can_ankan or cans.can_kakan:
            q[42] = best_discard_q + (0.25 if (real_shanten <= 1 and num_opp_riichi == 0) else -0.9)
        else:
            q[42] = -0.8
        meta_by_action[42] = {
            "shanten_after": real_shanten,
            "ukeire": 0,
            "ukeire_tiles": [],
            "ev": round(max_ev, 0),
            "win_prob": 0.0,
            "tenpai_prob": 0.0,
            "danger": 0.0,
            "tags": ["杠"],
        }

    # --- 4. Evaluate Naki / Pass (38, 39, 40, 41, 45) ---
    if 45 in legal:
        # 3n+1 state before calling
        uk_pass_idx = [u for u in range(34) if obs[960, u] > 0.5]
        uk_pass = sum(rem[u] for u in uk_pass_idx) if uk_pass_idx else py_ukeire(list(tehai), n_melds, tiles_seen, cur_shanten)[0]
        pass_ev = float(obs[995, 0]) * max_ev
        pass_wp = float(obs[978, 0])
        base_pass_q = (pass_ev / 2500.0) + pass_wp * 2.5 + uk_pass * 0.03
        q[45] = base_pass_q
        meta_by_action[45] = {
            "shanten_after": cur_shanten,
            "ukeire": uk_pass,
            "ukeire_tiles": [{"pai": tname(u), "count": rem[u]} for u in uk_pass_idx if rem[u] > 0][:10],
            "ev": round(pass_ev, 0),
            "win_prob": round(pass_wp * 100, 1),
            "tenpai_prob": round(float(obs[961, 0]) * 100, 1),
            "danger": 0.0,
            "tags": ["门清/防守" if n_melds == 0 else "跳过"],
        }

        kawa_pai = ps.last_kawa_tile()
        if kawa_pai:
            k34 = tid(kawa_pai)
            dora_owned = sum(tehai[d] for d in dora_t34s) + sum(1 for b in akas if b)
            has_yakuhai_meld = any(p in yakuhai_set for p in ps.pons) or any(p in yakuhai_set for p in ps.minkans) or any(p in yakuhai_set for p in ps.ankans)
            has_yakuhai_pair = any(tehai[y] >= 2 for y in yakuhai_set)

            for call_act in [38, 39, 40, 41]:
                if call_act not in legal:
                    continue
                c_after = list(tehai)
                if call_act == 41:
                    c_after[k34] = max(0, c_after[k34] - 2)
                elif call_act == 38:
                    c_after[k34 + 1] -= 1
                    c_after[k34 + 2] -= 1
                elif call_act == 39:
                    c_after[k34 - 1] -= 1
                    c_after[k34 + 1] -= 1
                elif call_act == 40:
                    c_after[k34 - 2] -= 1
                    c_after[k34 - 1] -= 1

                # c_after now has 3n+2 tiles (with n_melds + 1); find best discard after calling
                best_s_after = 8
                best_uk_after = 0
                for d34 in range(34):
                    if c_after[d34] <= 0:
                        continue
                    c_after[d34] -= 1
                    s_d = py_shanten(c_after, n_melds + 1)
                    uk_d, _ = py_ukeire(c_after, n_melds + 1, tiles_seen, s_d)
                    if s_d < best_s_after or (s_d == best_s_after and uk_d > best_uk_after):
                        best_s_after = s_d
                        best_uk_after = uk_d
                    c_after[d34] += 1

                shanten_gain = cur_shanten - best_s_after
                is_yakuhai_call = call_act == 41 and k34 in yakuhai_set
                yaochu_in_hand = sum(c_after[i] for i in range(34) if is_yaochu(i))
                is_tanyao_like = not is_yaochu(k34) and yaochu_in_hand <= 1
                has_yaku = is_yakuhai_call or has_yakuhai_meld or has_yakuhai_pair or is_tanyao_like

                call_tags = []
                if is_yakuhai_call:
                    call_tags.append("役牌碰")
                if best_s_after == 0:
                    call_tags.append("鸣牌听牌")
                elif shanten_gain > 0:
                    call_tags.append("进向")
                if not has_yaku:
                    call_tags.append("注意无役")

                if not has_yaku:
                    call_q = base_pass_q - 1.4
                elif best_s_after == 0 and shanten_gain >= 1:
                    call_q = base_pass_q + 0.75 + dora_owned * 0.15 - num_opp_riichi * 0.4
                elif is_yakuhai_call and shanten_gain >= 1:
                    call_q = base_pass_q + 0.55 + dora_owned * 0.12 - num_opp_riichi * 0.5
                elif shanten_gain >= 1 and best_s_after <= 1 and (dora_owned >= 2 or n_melds >= 1):
                    call_q = base_pass_q + 0.30 - num_opp_riichi * 0.6
                else:
                    call_q = base_pass_q - 0.35 - num_opp_riichi * 0.6

                q[call_act] = call_q
                meta_by_action[call_act] = {
                    "shanten_after": best_s_after,
                    "ukeire": best_uk_after,
                    "ukeire_tiles": [],
                    "ev": round(pass_ev * (1.1 if shanten_gain > 0 else 0.8), 0),
                    "win_prob": round(min(99.0, pass_wp * 100 * (1.25 if shanten_gain > 0 else 0.9)), 1),
                    "tenpai_prob": 100.0 if best_s_after == 0 else round(float(obs[961, 0]) * 100, 1),
                    "danger": 0.0,
                    "tags": call_tags,
                }

    return q, meta_by_action


# ---------------------------------------------------------------------------
# Full Game Reviewer + Replay Builder
# ---------------------------------------------------------------------------
def review_game(
    mjai_events: list[dict],
    target_actor: int,
    engine_mgr: EngineManager,
    meta: dict | None = None,
    temperature: float = 0.1,
    force_engine: str = "auto",
) -> dict[str, Any]:
    assert 0 <= target_actor <= 3
    mortal = engine_mgr.get_mortal()
    if mortal is None:
        raise ValueError("未检测到可用的神经网络模型，请确认 data/models/ 中存在 akagi.pth 或 mortal.pth")
    obs_version = mortal.version

    names = (meta or {}).get("names") or ["东家", "南家", "西家", "北家"]
    for ev in mjai_events:
        if ev.get("type") == "start_game" and ev.get("names"):
            names = list(ev["names"])
            break

    ps = lstate.PlayerState(target_actor)

    kyokus: list[dict[str, Any]] = []
    cur_kyoku: dict[str, Any] | None = None

    # Board tracking state per kyoku
    hands: list[list[str]] = [[], [], [], []]
    drawn_tile: list[str | None] = [None, None, None, None]
    melds: list[list[dict]] = [[], [], [], []]
    rivers: list[list[dict]] = [[], [], [], []]
    riichi_declared: list[bool] = [False, False, False, False]
    riichi_step: list[int | None] = [None, None, None, None]
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

    # Batch buffers for Mortal neural network evaluation
    pending_decisions: list[dict[str, Any]] = []
    mortal_obs_batch: list[np.ndarray] = []
    mortal_mask_batch: list[np.ndarray] = []

    def snapshot_board(ev: dict, step_idx: int) -> dict[str, Any]:
        return {
            "step_idx": step_idx,
            "event": ev,
            "hands": [sorted(h, key=sort_key) for h in hands],
            "drawn": list(drawn_tile),
            "melds": [[dict(m) for m in ms] for ms in melds],
            "rivers": [[dict(r) for r in rs] for rs in rivers],
            "riichi": list(riichi_declared),
            "scores": list(scores),
            "dora_markers": list(dora_markers),
            "kyotaku": kyotaku,
            "honba": honba,
            "tiles_left": tiles_left,
            "junme": junme,
            "actor": ev.get("actor"),
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
            for x in list(hands[seat]):
                if deaka(x) == base:
                    hands[seat].remove(x)
                    break

    for i, ev in enumerate(mjai_events):
        etype = ev["type"]
        ps.update(json.dumps(ev))

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
            riichi_declared = [False, False, False, False]
            riichi_step = [None, None, None, None]
            dora_markers = [ev["dora_marker"]]
            tiles_left = 70
            junme = 0
            pending_riichi_Discard = None
            last_actor = oya
            last_tile = None

            label = f"{BAKAZE_CN.get(bakaze, bakaze)}{kyoku_num}局"
            if honba > 0:
                label += f" {honba}本场"
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
            cur_kyoku["steps"].append(snapshot_board(ev, 0))
            continue

        if cur_kyoku is None:
            continue

        if etype == "end_kyoku":
            kyokus.append(cur_kyoku)
            cur_kyoku = None
            continue

        if etype == "dora":
            dora_markers.append(ev["dora_marker"])
            if cur_kyoku["steps"]:
                cur_kyoku["steps"][-1]["dora_markers"] = list(dora_markers)
            continue

        if etype == "reach_accepted":
            r_seat = int(ev["actor"])
            scores[r_seat] -= 1000
            kyotaku += 1
            if cur_kyoku["steps"]:
                cur_kyoku["steps"][-1]["scores"] = list(scores)
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
                    actual_ev = next_player_action(mjai_events[i + 1 :], target_actor, False, False, False)
                    if actual_ev is not None and actual_ev.get("type") == "dahai":
                        obs_m = obs_4 if obs_version == 4 else ps.encode_obs(obs_version, False)[0]
                        q_h, meta_a = analyze_candidates_and_heuristic_q(
                            ps, obs_4, mask_4, target_actor, bakaze, oya, rivers, riichi_step, melds, dora_markers, tiles_left
                        )
                        step_ref = len(cur_kyoku["steps"]) - 1
                        pending_decisions.append(
                            {
                                "kyoku_idx": cur_kyoku["kyoku_idx"],
                                "step_idx": step_ref,
                                "junme": junme,
                                "tiles_left": tiles_left,
                                "shanten": 0,
                                "at_furiten": bool(ps.at_furiten),
                                "decision_type": "riichi_discard",
                                "context_tile": last_tile,
                                "last_actor": last_actor,
                                "actual_ev": actual_ev,
                                "mask": mask_4,
                                "q_heuristic": q_h,
                                "meta_by_action": meta_a,
                                "akas_in_hand": list(ps.akas_in_hand),
                                "ankan_cands": ps.ankan_candidates(),
                                "kakan_cands": ps.kakan_candidates(),
                            }
                        )
                        if mortal is not None:
                            mortal_obs_batch.append(obs_m)
                            mortal_mask_batch.append(mask_4)
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
                riichi_step[seat] = len(cur_kyoku["steps"])
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
                rivers[target][-1]["called"] = True
            melds[seat].append({"type": etype, "pai": pai, "consumed": consumed, "from": target, "tiles": [pai] + consumed})
            if seat == target_actor and etype in ("chi", "pon"):
                junme += 1
            last_actor = seat

        elif etype == "ankan":
            seat = int(ev["actor"])
            consumed = list(ev["consumed"])
            for c in consumed:
                remove_from_hand(seat, c)
            melds[seat].append({"type": "ankan", "pai": consumed[0], "consumed": consumed, "from": seat, "tiles": consumed})
            last_actor = seat

        elif etype == "kakan":
            seat = int(ev["actor"])
            pai = ev["pai"]
            remove_from_hand(seat, pai)
            base = deaka(pai)
            for m in melds[seat]:
                if m["type"] == "pon" and deaka(m["pai"]) == base:
                    m["type"] = "kakan"
                    m["tiles"].append(pai)
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
            if seat == target:
                cur_kyoku["end_summaries"].append(f"{who} 自摸 {pts}")
            else:
                cur_kyoku["end_summaries"].append(f"{who} 荣和 {names[target]} {pts}")

        elif etype == "ryukyoku":
            deltas = ev.get("deltas")
            if deltas:
                for s in range(4):
                    scores[s] += int(deltas[s])
                    cur_kyoku["score_deltas"][s] += int(deltas[s])
                cur_kyoku["end_scores"] = list(scores)
            cur_kyoku["end_summaries"].append("流局")

        step_idx = len(cur_kyoku["steps"])
        step_snap = snapshot_board(ev, step_idx)
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
        actual_ev = next_player_action(mjai_events[i + 1 :], target_actor, can_pon_or_kan, can_agari, can_ryukyoku)
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
        q_h, meta_a = analyze_candidates_and_heuristic_q(
            ps, obs_4, mask_4, target_actor, bakaze, oya, rivers, riichi_step, melds, dora_markers, tiles_left
        )

        has_next = bool(obs_4[876].any())
        cur_s = int(ps.shanten)
        real_s = (cur_s - 1) if (cans.can_discard and cur_s > 0 and has_next) else cur_s

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
                "shanten": real_s,
                "at_furiten": bool(ps.at_furiten),
                "decision_type": dtype,
                "context_tile": last_tile,
                "last_actor": last_actor,
                "actual_ev": actual_ev,
                "actual_label": actual_label,
                "mask": mask_4,
                "q_heuristic": q_h,
                "meta_by_action": meta_a,
                "akas_in_hand": list(ps.akas_in_hand),
                "ankan_cands": ps.ankan_candidates(),
                "kakan_cands": ps.kakan_candidates(),
            }
        )
        if mortal is not None:
            mortal_obs_batch.append(obs_m)
            mortal_mask_batch.append(mask_4)

    if cur_kyoku is not None:
        kyokus.append(cur_kyoku)

    # Run Mortal neural network batch inference if loaded
    mortal_q_batch = mortal.eval_batch(mortal_obs_batch, mortal_mask_batch) if mortal is not None else None

    # Finalize all decisions & compute summary statistics
    total_reviewed = 0
    total_matches = 0
    blunders = 0
    inaccuracies = 0
    raw_rating_sum = 0.0

    for d_idx, item in enumerate(pending_decisions):
        mask = item["mask"]
        legal_labels = np.nonzero(mask)[0].tolist()
        q_full = mortal_q_batch[d_idx] if mortal_q_batch is not None else item["q_heuristic"]

        q_legal = np.array([float(q_full[l]) for l in legal_labels], dtype=np.float32)
        probs = softmax(q_legal, temperature=temperature)

        candidates = []
        for idx_in_legal, label in enumerate(legal_labels):
            act_dict = label_to_action_dict(
                label,
                target_actor,
                item["last_actor"],
                item["context_tile"],
                item["akas_in_hand"],
                item["ankan_cands"],
                item["kakan_cands"],
            )
            m_info = item["meta_by_action"].get(label, {})
            candidates.append(
                {
                    "label": int(label),
                    "action": act_dict,
                    "action_cn": format_action_cn(act_dict),
                    "short_cn": short_action_cn(act_dict),
                    "pai": act_dict.get("pai"),
                    "q_value": round(float(q_legal[idx_in_legal]), 3),
                    "prob": round(float(probs[idx_in_legal]) * 100.0, 2),
                    "shanten_after": m_info.get("shanten_after", item["shanten"]),
                    "ukeire": m_info.get("ukeire", 0),
                    "ukeire_tiles": m_info.get("ukeire_tiles", []),
                    "ev": m_info.get("ev", 0),
                    "win_prob": m_info.get("win_prob", 0.0),
                    "tenpai_prob": m_info.get("tenpai_prob", 0.0),
                    "danger": m_info.get("danger", 0.0),
                    "tags": m_info.get("tags", []),
                }
            )

        candidates.sort(key=lambda c: c["q_value"], reverse=True)
        for rank_i, c in enumerate(candidates):
            c["rank"] = rank_i + 1

        actual_label = item.get("actual_label", event_to_label(item["actual_ev"]))
        actual_cand = next((c for c in candidates if c["label"] == actual_label), candidates[0])
        best_cand = candidates[0]

        is_equal = actual_cand["label"] == best_cand["label"]
        q_min = min(c["q_value"] for c in candidates)
        q_max = max(c["q_value"] for c in candidates)
        rel_score = 1.0 if is_equal else (actual_cand["q_value"] - q_min) / max(q_max - q_min, 1e-6)
        q_loss = max(0.0, best_cand["q_value"] - actual_cand["q_value"])
        actual_prob = actual_cand["prob"]

        # Verdict classification (NAGA style)
        if is_equal or (actual_prob >= 35.0 and q_loss < 0.08):
            verdict = "best"
            verdict_cn = "一致"
        elif actual_prob >= 15.0 or rel_score >= 0.72 or q_loss < 0.25:
            verdict = "ok"
            verdict_cn = "微差"
            inaccuracies += 1
        elif actual_prob >= 5.0 or rel_score >= 0.42 or q_loss < 0.65:
            verdict = "question"
            verdict_cn = "疑问手"
            inaccuracies += 1
        else:
            verdict = "blunder"
            verdict_cn = "恶手"
            blunders += 1

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
                    "q_value": c["q_value"],
                    "rank": c["rank"],
                    "ukeire": c["ukeire"],
                    "ev": c["ev"],
                    "danger": c["danger"],
                    "is_best": c["label"] == best_cand["label"],
                    "is_actual": c["label"] == actual_cand["label"],
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
            "shanten": item["shanten"],
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
            "rel_score": round(rel_score * 100, 1),
            "is_equal": is_equal,
            "verdict": verdict,
            "verdict_cn": verdict_cn,
            "candidates": candidates,
            "tile_bars": tile_bars,
        }
        kyoku_obj["decisions"].append(decision_entry)

        # Attach decision_idx to the replay step (and also to the immediately following dahai step if self_turn)
        step_idx = item["step_idx"]
        if 0 <= step_idx < len(kyoku_obj["steps"]):
            if kyoku_obj["steps"][step_idx]["decision_idx"] is None:
                kyoku_obj["steps"][step_idx]["decision_idx"] = dec_idx_in_kyoku
            if (
                step_idx + 1 < len(kyoku_obj["steps"])
                and kyoku_obj["steps"][step_idx + 1]["event"].get("type") == "dahai"
                and kyoku_obj["steps"][step_idx + 1]["event"].get("actor") == target_actor
                and kyoku_obj["steps"][step_idx + 1]["decision_idx"] is None
            ):
                kyoku_obj["steps"][step_idx + 1]["decision_idx"] = dec_idx_in_kyoku

    # Compute overall rating & per-kyoku stats
    rating = ((raw_rating_sum / total_reviewed) ** 2) * 100.0 if total_reviewed > 0 else 100.0
    match_rate = (total_matches / total_reviewed) * 100.0 if total_reviewed > 0 else 100.0
    blunder_rate = (blunders / total_reviewed) * 100.0 if total_reviewed > 0 else 0.0

    if rating >= 92:
        grade = "S+"
    elif rating >= 87:
        grade = "S"
    elif rating >= 82:
        grade = "A+"
    elif rating >= 76:
        grade = "A"
    elif rating >= 70:
        grade = "B+"
    elif rating >= 62:
        grade = "B"
    else:
        grade = "C"

    for k in kyokus:
        decs = k["decisions"]
        k_tot = len(decs)
        k_match = sum(1 for d in decs if d["is_equal"])
        k_blunder = sum(1 for d in decs if d["verdict"] == "blunder")
        k_raw = sum(d["rel_score"] / 100.0 for d in decs)
        k["stats"] = {
            "total": k_tot,
            "matches": k_match,
            "match_rate": round((k_match / k_tot) * 100.0, 1) if k_tot else 100.0,
            "blunders": k_blunder,
            "rating": round(((k_raw / k_tot) ** 2) * 100.0, 1) if k_tot else 100.0,
        }

    return {
        "target_actor": target_actor,
        "player_name": names[target_actor],
        "names": names,
        "meta": meta or {},
        "engine": {
            "type": "mortal" if mortal is not None else "builtin",
            "tag": mortal.tag if mortal is not None else "Built-in SP+Defense (libriichi Rust)",
        },
        "summary": {
            "total_reviewed": total_reviewed,
            "total_matches": total_matches,
            "match_rate": round(match_rate, 1),
            "rating": round(rating, 1),
            "grade": grade,
            "blunders": blunders,
            "blunder_rate": round(blunder_rate, 1),
            "inaccuracies": inaccuracies,
        },
        "kyokus": kyokus,
    }
