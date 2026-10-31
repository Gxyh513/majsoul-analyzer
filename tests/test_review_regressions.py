"""Regression coverage for scoring and replay decision indexing."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.engine import reviewer
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_SHA256


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
    steps = None
    tag = "test"
    device = "cpu"
    weight_sha256 = MODEL_SHA256
    def eval_batch(self, obs, masks):
        return np.array([np.arange(46, dtype=np.float32) for _ in obs])


class FakeManager:
    def get_mortal(self, model):
        return FakeModel(), MODEL_ID, Path(MODEL_FILE)


def start_round():
    return {"type":"start_kyoku","bakaze":"E","kyoku":1,"honba":0,"kyotaku":0,
            "oya":0,"scores":[25000]*4,"dora_marker":"E","tehais":[["1m"]*13 for _ in range(4)]}


def test_riichi_discard_is_a_separate_navigable_step(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    events = [{"type":"start_game","names":["A","B","C","D"]}, start_round(),
              {"type":"tsumo","actor":0,"pai":"1m"},
              {"type":"reach","actor":0},
              {"type":"dahai","actor":0,"pai":"1m","tsumogiri":True},
              {"type":"end_kyoku"},{"type":"end_game"}]
    result = reviewer.review_game(events, 0, FakeManager())
    assert "decisions" not in result
    k = result["kyokus"][0]
    assert all("actor" not in step and "step_idx" not in step for step in k["steps"])
    turn = next(d for d in k["decisions"] if d["decision_type"] == "self_turn")
    discard = next(d for d in k["decisions"] if d["decision_type"] == "riichi_discard")
    assert discard["step_idx"] == turn["step_idx"] + 1
    assert k["steps"][discard["step_idx"]]["event"]["type"] == "reach"
    assert turn["actual"]["type"] == "reach"
    assert discard["actual"]["tsumogiri"] is True
    assert k["steps"][turn["step_idx"]]["decision_idx"] == turn["decision_idx"]
    assert k["steps"][discard["step_idx"]]["decision_idx"] == discard["decision_idx"]
    assert all("tsumogiri" not in c["action"] for d in k["decisions"] for c in d["candidates"] if c["action"]["type"] == "dahai")
    summary = result["summary"]
    assert summary["match_rate"] == round(100 * summary["total_matches"] / summary["total_reviewed"], 1)


def test_indexed_next_action_skips_long_metadata_and_preserves_hora_and_reach():
    prefix = [{"type": "dora"}] * 1500
    win = {"type": "hora", "actor": 0}
    events = [{"type": "end_game"}, *prefix, {"type": "hora", "actor": 1}, win]
    assert reviewer.next_player_action(events, 0, False, True, False, 1) == win
    reach = {"type": "reach", "actor": 0}
    events = [reach, {"type": "reach_accepted"}, {"type": "dahai", "actor": 0, "pai": "2m"}]
    assert reviewer.next_player_action(events, 0, False, False, False) == {**reach, "pai": "2m"}
    assert reach == {"type": "reach", "actor": 0}


def test_snapshots_preserve_river_and_meld_before_calls_and_kakan(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    events = [start_round(),
              {"type": "dahai", "actor": 0, "pai": "1m"},
              {"type": "pon", "actor": 1, "target": 0, "pai": "1m", "consumed": ["1m", "1m"]},
              {"type": "kakan", "actor": 1, "pai": "1m"},
              {"type": "end_kyoku"}]
    steps = reviewer.review_game(events, 0, FakeManager())["kyokus"][0]["steps"]
    assert steps[1]["rivers"][0][0]["called"] is False
    assert steps[2]["rivers"][0][0]["called"] is True
    assert steps[2]["melds"][1][0]["type"] == "pon"
    assert len(steps[2]["melds"][1][0]["tiles"]) == 3
    assert steps[3]["melds"][1][0]["type"] == "kakan"
    assert len(steps[3]["melds"][1][0]["tiles"]) == 4


def test_candidates_keep_model_scores_without_supplemental_statistics(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    events = [start_round(), {"type": "tsumo", "actor": 0, "pai": "1m"},
              {"type": "dahai", "actor": 0, "pai": "1m"}, {"type": "end_kyoku"}]
    decision = reviewer.review_game(events, 0, FakeManager())["kyokus"][0]["decisions"][0]
    candidates = decision["candidates"]
    assert [c["label"] for c in candidates] == [37, 1, 0]
    assert [c["rank"] for c in candidates] == [1, 2, 3]
    assert candidates[0]["raw_q"] == 37 and candidates[0]["prob"] > candidates[-1]["prob"]
    assert all(not ({"shanten_after", "ukeire", "ev", "danger", "tenpai_prob"} & c.keys()) for c in candidates)
    assert all(set(bar) == {"prob"} for bar in decision["tile_bars"].values())


def test_self_turn_decision_preserves_tsumogiri_flag(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}, start_round(),
              {"type": "tsumo", "actor": 0, "pai": "1m"},
              {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": True},
              {"type": "end_kyoku"}, {"type": "end_game"}]
    kyoku = reviewer.review_game(events, 0, FakeManager())["kyokus"][0]
    decision = next(d for d in kyoku["decisions"] if d["decision_type"] == "self_turn")
    assert decision["actual"]["tsumogiri"] is True
    assert kyoku["steps"][decision["step_idx"]]["drawn"][0] == "1m"


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
    path = Path(__file__).resolve().parents[1] / "data" / "models" / MODEL_FILE
    if not path.is_file(): pytest.skip("published model not present")
    events = json.loads(Path("data/demo_mjai.json").read_text(encoding="utf8"))
    result = reviewer.review_game(events, 0, reviewer.EngineManager(path.parent))
    summary = result["summary"]
    assert summary["match_rate"] == round(100 * summary["total_matches"] / summary["total_reviewed"], 1)
    assert result["match_rate"] == summary["match_rate"]
    # Per-decision rel_score is rounded to one decimal place before display.
    estimated = (sum(d["rel_score"] / 100 for k in result["kyokus"] for d in k["decisions"]) / summary["total_reviewed"]) ** 2 * 100
    assert summary["rating"] == pytest.approx(estimated, abs=0.1)


def test_majsoul_yakuman_fields_survive_conversion_to_round_summary(monkeypatch):
    from server.majsoul.convert import to_mjai

    # Synthetic local fixture: three dragon triplets and a fourth white tile.
    hands = [["5z"] * 4 + ["6z"] * 3 + ["7z"] * 3 + ["1m"] * 3]
    hands.extend([[f"{seat}m"] * 13 for seat in (2, 3, 4)])
    round_data = {f"tiles{seat}": tiles for seat, tiles in enumerate(hands)}
    round_data.update({"dora": "1m", "scores": [25000] * 4})
    win = {"seat": 0, "zimo": True, "yiman": True, "title_id": 5,
           "count": 1, "fu": 50, "fans": [{"id": 37, "val": 1}]}
    record = {"head": {}, "actions": [
        {"name": "RecordNewRound", "data": round_data},
        {"name": "RecordHule", "data": {"hules": [win], "delta_scores": [32000, -12000, -10000, -10000]}},
    ]}
    events = to_mjai(record)
    hora = next(event for event in events if event["type"] == "hora")
    assert hora["yiman"] is True and hora["fans"][0]["id"] == 37
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    result = reviewer.review_game(events, 0, FakeManager())
    assert "役满（大三元）" in result["kyokus"][0]["end_summaries"][0]
    assert "50符1番" not in result["kyokus"][0]["end_summaries"][0]


@pytest.mark.parametrize(
    ("fields", "expected", "forbidden"),
    [
        ({"yiman": True, "title_id": 5, "han": 1, "fu": 50,
          "fans": [{"id": 37, "val": 1}]}, "役满（大三元）", "50符1番"),
        ({"yiman": True, "title_id": 7, "han": 3, "fu": 50,
          "fans": [{"id": 37, "val": 1}, {"id": 48, "val": 2}]},
         "3倍役满（大三元、四暗刻单骑）", "50符3番"),
        ({"yiman": True, "title_id": 5, "han": 3, "fu": 40,
          "fans": [{"id": 37, "val": 1}]}, "3倍役满（大三元）", "40符3番"),
        ({"yiman": True, "title_id": 7, "han": 0, "fu": 40,
          "fans": [{"id": 37, "val": 1}]}, "3倍役满（大三元）", "40符1番"),
        ({"yiman": True, "title_id": 0, "han": 0, "fu": 40,
          "fans": [{"id": 37, "val": 1}, {"id": 48, "val": 2}]},
         "3倍役满（大三元、四暗刻单骑）", "40符3番"),
        ({"yiman": True, "title_id": 6, "han": 2, "fu": 40}, "2倍役满", "40符2番"),
        ({"yiman": True, "title_id": 10, "han": 6, "fu": 40}, "6倍役满", "40符6番"),
        ({"yiman": True, "title_id": 0, "han": 4, "fu": 40}, "4倍役满", "40符4番"),
        ({"yiman": False, "title_id": 11, "han": 13, "fu": 40,
          "fans": [{"id": 29, "val": 6}, {"id": 31, "val": 7}]},
         "累计役满 40符13番", "13倍役满"),
        ({"yiman": True, "title_id": 5, "han": 1, "fu": 40,
          "fans": [{"id": 999, "val": 1}]}, "役满（未知役种（ID 999））", "40符1番"),
        ({"yiman": False, "title_id": 0, "han": 3, "fu": 40,
          "fans": [{"id": 15, "val": 2}, {"id": 2, "val": 1}]}, "40符3番", "役满"),
    ],
)
def test_round_summary_formats_yakuman_as_multiples(monkeypatch, fields, expected, forbidden):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    hora = {"type": "hora", "actor": 0, "target": 1,
            "deltas": [32000, -32000, 0, 0], **fields}
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]},
              start_round(), hora, {"type": "end_kyoku"}, {"type": "end_game"}]
    result = reviewer.review_game(events, 0, FakeManager())
    kyoku = result["kyokus"][0]
    summary = kyoku["end_summaries"][0]
    assert expected in summary
    assert forbidden not in summary
    assert kyoku["steps"][-1]["event"] == hora

def test_perspective_snapshots_reference_stable_mjai_event_indices(monkeypatch):
    monkeypatch.setattr(reviewer.lstate, "PlayerState", FakePlayerState)
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}, start_round(),
              {"type": "tsumo", "actor": 0, "pai": "1m"},
              {"type": "reach", "actor": 0},
              {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": True},
              {"type": "end_kyoku"}, {"type": "end_game"}]
    first = reviewer.review_game(events, 0, FakeManager())["kyokus"][0]["steps"]
    second = reviewer.review_game(events, 1, FakeManager())["kyokus"][0]["steps"]
    assert [s["event_idx"] for s in first] == [1, 2, 3, 4]
    assert [s["event_idx"] for s in second] == [1, 2, 4]
