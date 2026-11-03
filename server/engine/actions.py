"""Mortal action-space mapping, selection and Chinese action labels."""
from __future__ import annotations

import numpy as np

from .tiles import action_idx_of_tile, deaka, is_aka, tid, tile_cn, tile_of_action_idx, tname


def softmax(q: np.ndarray, temperature: float = 0.1) -> np.ndarray:
    if len(q) == 0:
        return q
    t = max(float(temperature), 1e-4)
    x = (q.astype(np.float64) - float(np.max(q))) / t
    x = np.clip(x, -500.0, 0.0)
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
    start_idx: int = 0,
) -> dict | None:
    """Port of `next_action` from `mjai-reviewer/src/review/mortal.rs`."""
    while start_idx < len(events) and events[start_idx]["type"] in ("dora", "reach_accepted"):
        start_idx += 1
    if start_idx >= len(events):
        return None
    ev = events[start_idx]
    t = ev["type"]
    if t in ("end_kyoku", "end_game", "start_kyoku"):
        return None
    if t == "tsumo":
        return {"type": "none"}
    if t == "hora":
        for idx in range(start_idx, min(start_idx + 3, len(events))):
            a = events[idx]
            if a["type"] == "hora" and a.get("actor") == player_id:
                return a
        return {"type": "none"} if can_agari else None
    if t == "ryukyoku":
        return ev if can_ryukyoku else None
    if t == "reach":
        res = dict(ev)
        for idx in range(start_idx + 1, len(events)):
            sub_ev = events[idx]
            if sub_ev.get("actor") == player_id and sub_ev.get("type") == "dahai":
                res["pai"] = sub_ev.get("pai")
                break
        return res
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
        # The primary Mortal action head has a single kan label.  It does
        # not identify which kan was selected when multiple options exist.
        if len(ankan_cands) + len(kakan_cands) > 1:
            return {"type": "kan", "actor": actor, "ambiguous": True}
        if ankan_cands:
            c = ankan_cands[0]
            base = deaka(c)
            consumed = [base] * 4
            if base[0] == "5" and base[1] in "mps" and (is_aka(c) or akas_in_hand["mps".index(base[1])]):
                consumed[0] = base + "r"
            return {"type": "ankan", "actor": actor, "consumed": consumed}
        if kakan_cands:
            c = kakan_cands[0]
            base = deaka(c)
            if base[0] == "5" and base[1] in "mps" and akas_in_hand["mps".index(base[1])]:
                c = base + "r"
            return {"type": "kakan", "actor": actor, "pai": c}
        base = deaka(last_tile or "1m")
        consumed = [base] * 3
        if last_tile and base[0] == "5" and base[1] in "mps" and not is_aka(last_tile) and akas_in_hand["mps".index(base[1])]:
            consumed[0] = base + "r"
        return {"type": "daiminkan", "actor": actor, "target": target, "pai": last_tile, "consumed": consumed}
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
    if t == "kan":
        return "杠（具体牌未判定）"
    if t == "hora":
        return "自摸" if ev.get("actor") == ev.get("target") else "荣和"
    if t == "ryukyoku":
        return "九种九牌流局"
    return "跳过 (Pass)"


