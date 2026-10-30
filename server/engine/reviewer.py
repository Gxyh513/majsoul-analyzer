"""Core review + replay builder for Mahjong Soul 4-player logs.

Uses only Mortal-V4-582500 for action Q values and replay decisions.
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
from .tiles import (  # noqa: E402
    action_idx_of_tile,
    deaka,
    is_aka,
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
            # Entries are replaced rather than mutated when calls or kans change them.
            "melds": [list(ms) for ms in melds],
            "rivers": [list(rs) for rs in rivers],
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
                    actual_ev = next_player_action(mjai_events, target_actor, False, False, False, i + 1)
                    if actual_ev is not None and actual_ev.get("type") == "dahai":
                        obs_m = obs_4 if obs_version == 4 else ps.encode_obs(obs_version, False)[0]
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
                rivers[target][-1] = {**rivers[target][-1], "called": True}
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
            last_tile = consumed[0]

        elif etype == "kakan":
            seat = int(ev["actor"])
            pai = ev["pai"]
            remove_from_hand(seat, pai)
            base = deaka(pai)
            for meld_idx, m in enumerate(melds[seat]):
                if m["type"] == "pon" and deaka(m["pai"]) == base:
                    melds[seat][meld_idx] = {**m, "type": "kakan", "added": pai, "tiles": [*m["tiles"], pai]}
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

        actual_label = item.get("actual_label", event_to_label(item["actual_ev"]))
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
