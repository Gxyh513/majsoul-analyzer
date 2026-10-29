"""Core review + replay builder for Mahjong Soul 4-player logs.

Uses only Mortal-V4-582500 for action Q values. Supplemental statistics
(shanten, ukeire, danger and EV) are separate from model recommendations.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import libriichi.state as lstate  # noqa: E402

from .mortal_model import LoadedMortal  # noqa: E402
from .model_policy import MODEL_FILE, MODEL_ID, MODEL_SHA256, REVIEW_SCHEMA, normalize_model_id, sha256_file  # noqa: E402
from .shanten import shanten as py_shanten  # noqa: E402
from .shanten import ukeire as py_ukeire  # noqa: E402
from .tiles import (  # noqa: E402
    action_idx_of_tile,
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


def _hora_score_description(event: dict[str, Any]) -> str:
    """Format Mahjong Soul yakuman values as multipliers, not han/fu."""
    if not event.get("yiman"):
        han, fu = event.get("han", event.get("count")), event.get("fu")
        score = f"{fu}符{han}番 " if fu and han else ""
        return f"累计役满 {score}" if event.get("title_id") == 11 else score

    fans = []
    for fan in event.get("fans") or []:
        if not isinstance(fan, dict):
            continue
        try:
            value = int(fan.get("val", 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            fans.append((fan, value))

    # For yiman the protocol's count, converted to han, is the total. Fan
    # entries can be missing, so only use their sum when no total is available.
    count = event.get("count", event.get("han"))
    title_id = event.get("title_id")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        multiplier = count
    elif isinstance(title_id, int) and 5 <= title_id <= 10:
        multiplier = title_id - 4
    else:
        multiplier = sum(value for _, value in fans) or 1
    title = f"{multiplier}倍役满" if multiplier > 1 else "役满"

    # Mahjong Soul fan IDs, not Tenhou IDs: 雀魂API 定数一覧にゃ.
    # https://wikiwiki.jp/majsoul-api/定数一覧にゃ
    known_names = {
        35: "天和", 36: "地和", 37: "大三元", 38: "四暗刻", 39: "字一色",
        40: "绿一色", 41: "清老头", 42: "国士无双", 43: "小四喜", 44: "四杠子",
        45: "九莲宝灯", 46: "八连庄", 47: "纯正九莲宝灯", 48: "四暗刻单骑",
        49: "国士无双十三面", 50: "大四喜", 51: "燕返し", 52: "槓振り", 53: "十二落抬",
        54: "五門斉", 55: "三連刻", 56: "一色三順", 57: "一筒摸月", 58: "九筒撈魚",
        59: "人和", 60: "大車輪", 61: "大竹林", 62: "大数隣", 63: "石の上にも三年", 64: "大七星",
    }
    names = [
        fan.get("name") or known_names.get(fan.get("id")) or f"未知役种（ID {fan.get('id', '?')}）"
        for fan, _ in fans
    ]
    return f"{title}{'（' + '、'.join(str(name) for name in names) + '）' if names else ''} "


# ---------------------------------------------------------------------------
# Model Manager (hot-loads mortal.pth if present)
# ---------------------------------------------------------------------------
class EngineManager:
    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.custom_pth: Path | None = None
        self.selected_model: str = "auto"
        self._loaded_models: dict[str, tuple[LoadedMortal, tuple[int, int, int]]] = {}
        self._load_error: str | None = None

    def list_available_models(self) -> list[dict[str, str]]:
        p = self.models_dir / MODEL_FILE
        return [{"id": MODEL_ID, "name": "Mortal-V4-582500", "path": str(p)}] if p.is_file() else []

    def resolve_model_path(self, model_id: str | None = None) -> tuple[Path | None, str]:
        normalize_model_id(model_id)
        p = self.models_dir / MODEL_FILE
        return (p if p.is_file() else None), MODEL_ID

    def find_pth(self, model_id: str | None = None) -> Path | None:
        return self.resolve_model_path(model_id)[0]

    def get_mortal(self, model_id: str | None = None) -> tuple[LoadedMortal, str, Path]:
        pth, resolved_id = self.resolve_model_path(model_id)
        if pth is None:
            self._loaded_models.clear()
            self._load_error = f"Mortal-V4-582500 真实模型权重文件缺失: {self.models_dir / MODEL_FILE}；严禁自动降级到其他引擎。"
            raise FileNotFoundError(self._load_error)
        key = str(pth.resolve())
        stat = pth.stat()
        identity = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if key in self._loaded_models:
            loaded, cached_identity = self._loaded_models[key]
            if cached_identity == identity:
                return loaded, resolved_id, pth
        try:
            digest = sha256_file(pth)
            if digest != MODEL_SHA256:
                raise ValueError("权重 SHA-256 与已发布的 mortal_582500.pth 不一致")
            loaded = LoadedMortal(pth, enforce_v4_582500=True)
            stat_after = pth.stat()
            if identity != (stat_after.st_size, stat_after.st_mtime_ns, stat_after.st_ctime_ns):
                raise RuntimeError("模型权重在加载时发生变化，请重试")
            loaded.weight_sha256 = digest
            self._loaded_models = {key: (loaded, identity)}
            self._load_error = None
            return loaded, resolved_id, pth
        except Exception as e:
            self._loaded_models.clear()
            self._load_error = str(e)
            raise RuntimeError(f"载入或校验 Mortal-V4-582500 权重 {MODEL_FILE} 失败: {e}") from e

    def status(self) -> dict[str, Any]:
        """Returns real-time status of the model manager, purging any stale cache."""
        # Purge deleted files from cache
        stale_keys = [k for k in self._loaded_models if not Path(k).is_file()]
        for k in stale_keys:
            self._loaded_models.pop(k, None)

        pth, resolved_id = self.resolve_model_path(MODEL_ID)
        mortal = None
        if pth is not None and pth.is_file():
            try:
                mortal, _, _ = self.get_mortal(MODEL_ID)
                self._load_error = None
            except Exception as e:
                self._load_error = str(e)
        else:
            expected_pth = self.models_dir / "mortal_582500.pth"
            self._load_error = f"权重文件缺失: {expected_pth}"

        return {
            "has_mortal_weights": mortal is not None,
            "active_engine": "mortal" if mortal is not None else "unavailable",
            "model_name": "Mortal-V4-582500" if mortal is not None else "Mortal-V4-582500 (不可用)",
            "model_id": resolved_id,
            "model_tag": mortal.tag if mortal is not None else "Mortal-V4-582500 (未加载)",
            "weight_sha256": mortal.weight_sha256 if mortal is not None else None,
            "review_schema": REVIEW_SCHEMA,
            "model_path": str(pth) if pth else str(self.models_dir / "mortal_582500.pth"),
            "models_dir": str(self.models_dir),
            "device": str(mortal.device).upper() if mortal is not None else "N/A",
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
    if t == "reach":
        res = dict(ev)
        for sub_ev in events[1:]:
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
            consumed = [c] * 4
            if len(c) >= 2 and c[0] == "5" and c[1] in "mps" and akas_in_hand["mps".index(c[1])]:
                consumed[0] = c[:2] + "r"
            return {"type": "ankan", "actor": actor, "consumed": consumed}
        if kakan_cands:
            c = kakan_cands[0]
            return {"type": "kakan", "actor": actor, "pai": c}
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
    if t == "kan":
        return "杠（具体牌未判定）"
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
    if t == "kan":
        return "杠（具体牌未判定）"
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
    riichi_safe = 0
    riichi_count = sum(1 for _, _, is_riichi in threatening_opps if is_riichi)

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
            if is_riichi:
                riichi_safe += 1
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

    # A tile safe against one riichi player may still be dangerous against another.
    if riichi_safe:
        if riichi_safe != riichi_count:
            safe_tag = "对部分立直者现物"
        elif max_danger > 0:
            safe_tag = "对立直者现物"
        else:
            safe_tag = "现物"
        tags.insert(0, safe_tag)

    # Deduplicate tags preserving order
    dedup = []
    for t in tags:
        if t not in dedup:
            dedup.append(t)
    return round(float(max_danger), 3), dedup[:2]


def analyze_candidate_metadata(
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
) -> dict[int, dict[str, Any]]:
    """Compute supplemental action metadata, never action weights."""
    legal = np.nonzero(mask)[0].tolist()
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

        meta_by_action[a] = {
            "shanten_after": shanten_after,
            "ukeire": uk_count,
            "ukeire_tiles": uk_tiles,
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

    # --- 2. Supplemental riichi metadata (not a recommendation) ---
    if 37 in legal:
        tenpai_discards = [a for a in discard_actions if meta_by_action[a]["shanten_after"] == 0]
        best_uk = max((meta_by_action[a]["ukeire"] for a in tenpai_discards), default=0)
        min_danger = min((meta_by_action[a]["danger"] for a in tenpai_discards), default=0.0)
        r_tags = ["立直"]
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

    return meta_by_action


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
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    assert 0 <= target_actor <= 3
    mortal, resolved_id, resolved_pth = engine_mgr.get_mortal(force_engine)
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
            cur_kyoku["steps"].append(snapshot_board(ev, 0))
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
                cur_kyoku["steps"][-1]["dora_markers"] = list(dora_markers)
            continue

        if etype == "reach_accepted":
            r_seat = int(ev["actor"])
            scores[r_seat] -= 1000
            kyotaku += 1
            cur_kyoku["end_scores"] = list(scores)
            cur_kyoku["score_deltas"][r_seat] -= 1000
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
                        meta_a = analyze_candidate_metadata(
                            ps, obs_4, mask_4, target_actor, bakaze, oya, rivers, riichi_step, melds, dora_markers, tiles_left
                        )
                        # Separate the riichi declaration from its discard choice,
                        # so both decisions have an independently navigable step.
                        step_ref = len(cur_kyoku["steps"])
                        cur_kyoku["steps"].append(snapshot_board(ev, step_ref))
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
                    m["added"] = pai
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
        meta_a = analyze_candidate_metadata(
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
        cur_kyoku["end_scores"] = list(scores)
        cur_kyoku["score_deltas"] = [cur_kyoku["end_scores"][s] - cur_kyoku["init_scores"][s] for s in range(4)]
        kyokus.append(cur_kyoku)

    # Run Mortal neural network batch inference if mortal is active
    if mortal is not None:
        if progress_callback:
            progress_callback(82.0, f"正在运行 Mortal-V4-582500 神经网络批量推理 ({len(pending_decisions)} 处待评决策)...")
        mortal_q_batch = mortal.eval_batch(mortal_obs_batch, mortal_mask_batch)
        if mortal_q_batch is None or len(mortal_q_batch) != len(pending_decisions):
            raise RuntimeError("Mortal-V4-582500 神经网络推理输出与待评决策数不匹配，已终止分析。")
        if any(not np.isfinite(mortal_q_batch[i][item["mask"]]).all() for i, item in enumerate(pending_decisions)):
            raise RuntimeError("Mortal 神经网络输出包含无效的合法动作权重，已终止分析。")
    else:
        mortal_q_batch = None

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
        legal_labels = np.nonzero(mask)[0].tolist()
        q_full = mortal_q_batch[d_idx]

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
            raw_q = float(q_legal[idx_in_legal])
            candidates.append(
                {
                    "label": int(label),
                    "action": act_dict,
                    "action_cn": format_action_cn(act_dict),
                    "short_cn": short_action_cn(act_dict),
                    "pai": act_dict.get("pai"),
                    "raw_q": raw_q,
                    "q_value": round(raw_q, 3),
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

        # Primary sort strictly determined by raw unrounded Q-value descending
        candidates.sort(key=lambda c: c["raw_q"], reverse=True)
        for rank_i, c in enumerate(candidates):
            c["rank"] = rank_i + 1

        actual_label = item.get("actual_label", event_to_label(item["actual_ev"]))
        actual_cand = next((c for c in candidates if c["label"] == actual_label), candidates[0])
        best_cand = candidates[0]

        ambiguous_kan = bool(best_cand["label"] == 42 and actual_cand["label"] == 42 and best_cand["action"].get("ambiguous"))
        is_equal = None if ambiguous_kan else actual_cand["label"] == best_cand["label"]
        q_min = min(c["raw_q"] for c in candidates)
        q_max = max(c["raw_q"] for c in candidates)
        rel_score = 1.0 if is_equal else (actual_cand["raw_q"] - q_min) / max(q_max - q_min, 1e-6)
        q_loss = max(0.0, best_cand["raw_q"] - actual_cand["raw_q"])
        actual_prob = actual_cand["prob"]

        # Verdict classification (NAGA style)
        if ambiguous_kan:
            verdict = "unknown"
            verdict_cn = "杠牌未判定"
        elif is_equal or (actual_prob >= 35.0 and q_loss < 0.08):
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
        "decisions": [d for k in kyokus for d in k.get("decisions", [])],
        "kyokus": kyokus,
    }
