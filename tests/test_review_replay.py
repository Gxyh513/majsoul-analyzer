"""Regression tests for allocation-aware replay snapshots."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

# The native libriichi extension is not part of the lightweight unit-test
# environment. Replay behavior under test only needs a replaceable PlayerState.
try:
    import libriichi.state  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name not in {"libriichi", "libriichi.state"}:
        raise
    libriichi_package = ModuleType("libriichi")
    libriichi_package.__path__ = []
    libriichi_state = ModuleType("libriichi.state")
    libriichi_state.PlayerState = None
    libriichi_package.state = libriichi_state
    sys.modules["libriichi"] = libriichi_package
    sys.modules["libriichi.state"] = libriichi_state

from server.engine import review_replay


class FakePlayerState:
    def __init__(self, _actor: int):
        self.last_cans = SimpleNamespace(can_act=False)

    def update(self, _event_json: str) -> None:
        pass


class ReplaySnapshotTests(unittest.TestCase):
    def test_unchanged_snapshot_lists_are_reused_and_old_states_stay_frozen(self):
        initial_hand = [
            "1m", "2m", "3m", "4m", "6m", "7m", "8m",
            "9m", "1p", "2p", "3p", "4p", "5p",
        ]
        events = [
            {
                "type": "start_kyoku",
                "bakaze": "E",
                "kyoku": 1,
                "honba": 0,
                "kyotaku": 0,
                "oya": 0,
                "scores": [25000, 25000, 25000, 25000],
                "dora_marker": "E",
                "tehais": [
                    initial_hand,
                    ["1m"] * 13,
                    ["2m"] * 13,
                    ["3m"] * 13,
                ],
            },
            {"type": "dora", "dora_marker": "2m"},
            {"type": "tsumo", "actor": 0, "pai": "5m"},
            {"type": "reach_accepted", "actor": 1},
            {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": False},
            {"type": "end_kyoku"},
        ]

        with patch.object(review_replay.lstate, "PlayerState", FakePlayerState):
            kyokus, decisions, q_values, _names = review_replay.build_replay(
                events, 0, 4, None, None, None
            )

        self.assertEqual(decisions, [])
        self.assertIsNone(q_values)
        steps = kyokus[0]["steps"]
        self.assertEqual(len(steps), 3)
        start, after_draw, after_discard = steps

        # Repeated snapshots share unchanged immutable containers.
        self.assertIs(start["hands"], after_draw["hands"])
        self.assertIs(start["melds"], after_draw["melds"])
        self.assertIs(start["rivers"], after_draw["rivers"])
        self.assertIs(start["riichi"], after_draw["riichi"])
        self.assertIs(start["dora_markers"], after_draw["dora_markers"])
        self.assertIs(after_draw["scores"], after_discard["scores"])
        self.assertIs(after_draw["melds"], after_discard["melds"])
        self.assertIs(after_draw["riichi"], after_discard["riichi"])
        self.assertIs(after_draw["dora_markers"], after_discard["dora_markers"])

        # Changed state gets a new container; earlier snapshots retain their values.
        self.assertIsNot(start["hands"], after_discard["hands"])
        self.assertIsNot(start["rivers"], after_discard["rivers"])
        self.assertEqual(start["hands"][0], sorted(initial_hand, key=review_replay.sort_key))
        self.assertIn("1m", start["hands"][0])
        self.assertNotIn("1m", after_discard["hands"][0])
        self.assertNotIn("5m", start["hands"][0])
        self.assertIn("5m", after_discard["hands"][0])
        self.assertEqual(start["rivers"][0], [])
        self.assertEqual(after_discard["rivers"][0][0]["pai"], "1m")

        # The dora and reach-accepted updates intentionally amend the preceding
        # visual step, without mutating other snapshots that shared old lists.
        self.assertEqual(start["dora_markers"], ["E", "2m"])
        self.assertEqual(start["scores"], [25000, 25000, 25000, 25000])
        self.assertEqual(after_draw["scores"], [25000, 24000, 25000, 25000])
        self.assertEqual(after_discard["scores"], [25000, 24000, 25000, 25000])
        self.assertEqual(after_draw["kyotaku"], 1)
        self.assertEqual(after_discard["kyotaku"], 1)

        self.assertEqual(start["drawn"], [None, None, None, None])
        self.assertEqual(after_draw["drawn"], ["5m", None, None, None])
        self.assertEqual(after_discard["drawn"], [None, None, None, None])


if __name__ == "__main__":
    unittest.main()
