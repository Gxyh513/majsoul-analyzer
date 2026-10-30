"""Focused regressions for Rating and kan-tile handling."""
import json
from pathlib import Path

import numpy as np
import pytest

from server.engine import reviewer
from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA, valid_review_source
from server.majsoul.convert import to_mjai
from test_review_regressions import FakeManager, FakeModel, FakePlayerState, start_round


@pytest.mark.parametrize("loss,expected", [(0.0001, 100.0), (0.1, 81.0), (0.5, 25.0), (1.5, 0.0)])
def test_rating_depends_on_q_gap_not_candidate_range(monkeypatch, loss, expected):
    class TwoChoiceState(FakePlayerState):
        def encode_obs(self, version, oracle):
            mask = np.zeros(46, dtype=bool)
            mask[0] = mask[1] = True
            return np.zeros((1012, 34), dtype=np.float32), mask

    class Model(FakeModel):
        def eval_batch(self, obs, masks):
            q = np.zeros(46, dtype=np.float32)
            q[0], q[1] = 1, 1 - loss
            return np.array([q for _ in obs])

    class Manager(FakeManager):
        def get_mortal(self, model):
            return Model(), "akagi", Path(MODEL_FILE)

    monkeypatch.setattr(reviewer.lstate, "PlayerState", TwoChoiceState)
    events = [start_round(), {"type": "tsumo", "actor": 0, "pai": "2m"},
              {"type": "dahai", "actor": 0, "pai": "2m"}, {"type": "end_kyoku"}]
    result = reviewer.review_game(events, 0, Manager())
    decision = result["decisions"][0]
    assert result["summary"]["rating"] == pytest.approx(expected, abs=0.1)
    assert decision["rel_score"] == pytest.approx(max(0, 1 - loss) * 100, abs=0.1)
    assert result["summary"]["total_matches"] == 0
    if loss == 0.0001:
        assert [c["prob"] for c in decision["candidates"]] == [50.03, 49.97]
        assert decision["verdict"] == "best"


def test_rating_is_independent_of_additional_low_q_candidate(monkeypatch):
    class ThreeChoiceState(FakePlayerState):
        def encode_obs(self, version, oracle):
            mask = np.zeros(46, dtype=bool)
            mask[0] = mask[1] = mask[2] = True
            return np.zeros((1012, 34), dtype=np.float32), mask

    class Model(FakeModel):
        def eval_batch(self, obs, masks):
            q = np.zeros(46, dtype=np.float32)
            q[0], q[1], q[2] = 1, 0.9, -10
            return np.array([q for _ in obs])

    class Manager(FakeManager):
        def get_mortal(self, model):
            return Model(), "akagi", Path(MODEL_FILE)

    monkeypatch.setattr(reviewer.lstate, "PlayerState", ThreeChoiceState)
    events = [start_round(), {"type": "tsumo", "actor": 0, "pai": "2m"},
              {"type": "dahai", "actor": 0, "pai": "2m"}, {"type": "end_kyoku"}]
    result = reviewer.review_game(events, 0, Manager())
    assert result["summary"]["rating"] == 81.0
    assert result["decisions"][0]["rel_score"] == 90.0


def test_previous_rating_cache_version_is_rejected():
    engine = {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
              "weight_sha256": "a" * 64, "version": 4, "steps": 582500,
              "q_source": "neural_network", "review_schema": REVIEW_SCHEMA}
    assert valid_review_source({"engine": engine}, "a" * 64)
    assert not valid_review_source({"engine": {**engine, "review_schema": "mortal-582500-only-v3"}}, "a" * 64)


@pytest.mark.parametrize("suit,index", [("m", 0), ("p", 1), ("s", 2)])
def test_kan_candidates_preserve_red_five(suit, index):
    red = [False] * 3
    red[index] = True
    base = "5" + suit
    action = reviewer.label_to_action_dict(42, 1, 0, base, red, [], [])
    assert sorted(action["consumed"]) == sorted([base, base, base + "r"])
    action = reviewer.label_to_action_dict(42, 1, 0, base + "r", [False] * 3, [], [])
    assert action["consumed"] == [base] * 3
    assert reviewer.label_to_action_dict(42, 1, 0, None, red, [], [base])["pai"] == base + "r"
    assert reviewer.label_to_action_dict(42, 1, 0, None, [False] * 3, [], [base])["pai"] == base
    assert sorted(reviewer.label_to_action_dict(42, 1, 0, None, red, [base], [])["consumed"]) == sorted([base] * 3 + [base + "r"])


def test_real_state_machine_preserves_red_added_kan():
    own = ["5m", "5m", "1m", "2m", "3m", "4m", "6m", "7m", "8m", "1p", "2p", "3p", "1s"]
    events = [
        {"type": "start_game", "names": ["A", "B", "C", "D"]},
        {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0, "kyotaku": 0,
         "oya": 1, "scores": [25000] * 4, "dora_marker": "E", "tehais": [own] + [["?"] * 13 for _ in range(3)]},
        {"type": "tsumo", "actor": 1, "pai": "?"},
        {"type": "dahai", "actor": 1, "pai": "5m", "tsumogiri": False},
        {"type": "pon", "actor": 0, "target": 1, "pai": "5m", "consumed": ["5m", "5m"]},
        {"type": "dahai", "actor": 0, "pai": "1s", "tsumogiri": False},
        {"type": "tsumo", "actor": 0, "pai": "5mr"},
    ]
    state = reviewer.lstate.PlayerState(0)
    for event in events:
        state.update(json.dumps(event))
    assert state.kakan_candidates() == ["5m"]
    action = reviewer.label_to_action_dict(42, 0, 0, "5mr", list(state.akas_in_hand),
                                           state.ankan_candidates(), state.kakan_candidates())
    assert action["pai"] == "5mr"


def ankan_fixture():
    hands = [
        ["2m", "3m", "4m", "5m", "6m", "7m", "8m", "2p", "3p", "4p", "5p", "6p", "7p", "3m"],
        ["1z"] * 3 + ["2m", "3m", "4m", "5m", "6m", "7m", "8m", "2p", "3p", "4p"],
        ["1m", "1m", "9m", "1p", "9p", "1s", "9s", "2z", "3z", "4z", "5z", "6z", "7z"],
        ["2s", "3s", "4s", "5s", "6s", "7s", "8s", "2p", "3p", "4p", "5p", "6p", "7p"],
    ]
    round_data = {f"tiles{i}": hand for i, hand in enumerate(hands)}
    round_data.update({"dora": "8p", "scores": [25000] * 4})
    return [
        {"name": "RecordNewRound", "data": round_data},
        {"name": "RecordDiscardTile", "data": {"seat": 0, "tile": "3m", "moqie": True}},
        {"name": "RecordDealTile", "data": {"seat": 1, "tile": "1z"}},
        {"name": "RecordAnGangAddGang", "data": {"seat": 1, "type": 3, "tiles": "1z"}},
    ]


def test_robbing_ankan_uses_kan_actor_and_tile():
    actions = ankan_fixture() + [{"name": "RecordHule", "data": {
        "hules": [{"seat": 2, "yiman": True}, {"seat": 3, "yiman": True}],
        "delta_scores": [0, -32000, 32000, 0]}}]
    events = to_mjai({"head": {}, "actions": actions})
    wins = [e for e in events if e["type"] == "hora"]
    assert [(e["actor"], e["target"], e["pai"]) for e in wins] == [(2, 1, "E"), (3, 1, "E")]


def test_robbing_ankan_events_are_accepted_by_real_state_machine():
    actions = ankan_fixture() + [{"name": "RecordHule", "data": {
        "hules": [{"seat": 2, "yiman": True}], "delta_scores": [0, -32000, 32000, 0]}}]
    events = to_mjai({"head": {}, "actions": actions})
    for seat in range(4):
        state = reviewer.lstate.PlayerState(seat)
        for event in events:
            state.update(json.dumps(event))
        result = reviewer.review_game(events, seat, FakeManager())
        win = next(step["event"] for step in result["kyokus"][0]["steps"] if step["event"]["type"] == "hora")
        assert (win["actor"], win["target"], win["pai"]) == (2, 1, "E")


def test_discard_after_kan_no_longer_uses_kan_as_win_source():
    actions = ankan_fixture() + [
        {"name": "RecordDealTile", "data": {"seat": 1, "tile": "2s"}},
        {"name": "RecordDiscardTile", "data": {"seat": 1, "tile": "2s"}},
        {"name": "RecordHule", "data": {"hules": [{"seat": 2}], "delta_scores": [0, -1000, 1000, 0]}},
    ]
    win = next(e for e in to_mjai({"head": {}, "actions": actions}) if e["type"] == "hora")
    assert (win["target"], win["pai"]) == (1, "2s")
