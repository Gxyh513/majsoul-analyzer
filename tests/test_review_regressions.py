"""Regression coverage for scoring and replay decision indexing."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.engine import reviewer


class FakePlayerState:
    def __init__(self, actor):
        self.phase = None
        self.shanten = 0
        self.at_furiten = False
        self.akas_in_hand = [False, False, False]
        self.last_cans = SimpleNamespace(can_act=False, can_discard=False)

    def update(self, raw):
        self.phase = json.loads(raw)["type"]
        active = self.phase in ("tsumo", "reach")
        self.last_cans = SimpleNamespace(can_act=active, can_discard=active)

    def encode_obs(self, version, oracle):
        obs = np.zeros((1012, 34), dtype=np.float32)
        mask = np.zeros(46, dtype=bool)
        mask[0] = mask[1] = True
        if self.phase == "tsumo":
            mask[37] = True
        return obs, mask

    def ankan_candidates(self): return []
    def kakan_candidates(self): return []


class FakeModel:
    version = 4
    steps = 582500
    tag = "test"
    device = "cpu"
    weight_sha256 = "a" * 64
    def eval_batch(self, obs, masks):
        return np.array([np.arange(46, dtype=np.float32) for _ in obs])


class FakeManager:
    def get_mortal(self, model):
        return FakeModel(), "akagi", Path("mortal_582500.pth")


def start_round():
    return {"type":"start_kyoku","bakaze":"E","kyoku":1,"honba":0,"kyotaku":0,
            "oya":0,"scores":[25000]*4,"dora_marker":"E","tehais":[["1m"]*13 for _ in range(4)]}


def test_riichi_discard_is_a_separate_navigable_step(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    monkeypatch.setattr(reviewer, "analyze_candidate_metadata", lambda *args: {})
    events = [{"type":"start_game","names":["A","B","C","D"]}, start_round(),
              {"type":"tsumo","actor":0,"pai":"1m"},
              {"type":"reach","actor":0},
              {"type":"dahai","actor":0,"pai":"1m","tsumogiri":True},
              {"type":"end_kyoku"},{"type":"end_game"}]
    result = reviewer.review_game(events, 0, FakeManager())
    k = result["kyokus"][0]
    turn = next(d for d in k["decisions"] if d["decision_type"] == "self_turn")
    discard = next(d for d in k["decisions"] if d["decision_type"] == "riichi_discard")
    assert discard["step_idx"] == turn["step_idx"] + 1
    assert k["steps"][discard["step_idx"]]["event"]["type"] == "reach"
    assert k["steps"][turn["step_idx"]]["decision_idx"] == turn["decision_idx"]
    assert k["steps"][discard["step_idx"]]["decision_idx"] == discard["decision_idx"]
    assert all("tsumogiri" not in c["action"] for d in k["decisions"] for c in d["candidates"] if c["action"]["type"] == "dahai")
    summary = result["summary"]
    assert summary["match_rate"] == round(100 * summary["total_matches"] / summary["total_reviewed"], 1)


def test_round_without_decisions_has_valid_overall_summary(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    events = [{"type":"start_game","names":["A","B","C","D"]}, start_round(),
              {"type":"end_kyoku"},{"type":"end_game"}]
    result = reviewer.review_game(events, 0, FakeManager())
    assert result["summary"]["total_reviewed"] == 0
    assert result["summary"]["match_rate"] == 100
    assert result["summary"]["rating"] == 100
    assert result["kyokus"][0]["match_rate"] is None


def test_global_summary_is_not_last_round():
    path = Path("data/models/mortal_582500.pth")
    if not path.is_file(): pytest.skip("published model not present")
    events = json.loads(Path("data/demo_mjai.json").read_text(encoding="utf8"))
    result = reviewer.review_game(events, 0, reviewer.EngineManager(path.parent))
    summary = result["summary"]
    assert summary["match_rate"] == round(100 * summary["total_matches"] / summary["total_reviewed"], 1)
    assert result["match_rate"] == summary["match_rate"]
    # Per-decision rel_score is rounded to one decimal place before display.
    estimated = (sum(d["rel_score"] / 100 for d in result["decisions"]) / summary["total_reviewed"]) ** 2 * 100
    assert summary["rating"] == pytest.approx(estimated, abs=0.1)
