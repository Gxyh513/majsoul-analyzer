"""Tests for allocation-aware ONNX batch input handling."""
from __future__ import annotations

import unittest

import numpy as np

from server.engine.onnx_mortal import ACTION_SPACE, OBS_SHAPE, OnnxMortal


class FakeSession:
    def __init__(self):
        self.batch_sizes = []
        self.last_output = None

    def run(self, output_names, inputs):
        self.assert_inputs(inputs)
        mask = inputs["mask"]
        self.batch_sizes.append(len(mask))
        self.last_output = np.where(mask, np.float32(0.25), np.float32(-np.inf))
        return [self.last_output]

    @staticmethod
    def assert_inputs(inputs):
        assert inputs["obs"].flags.c_contiguous
        assert inputs["mask"].flags.c_contiguous


class OnnxBatchTests(unittest.TestCase):
    def setUp(self):
        self.model = OnnxMortal.__new__(OnnxMortal)
        self.model.session = FakeSession()
        self.obs = np.zeros((3, *OBS_SHAPE), dtype=np.float32)
        self.mask = np.zeros((3, ACTION_SPACE), dtype=np.bool_)
        self.mask[:, 0] = True
        self.mask[:, 12] = True

    def test_contiguous_array_input_avoids_restacking_and_preserves_chunks(self):
        result = self.model.eval_batch(self.obs, self.mask, chunk_size=2)
        self.assertEqual(self.model.session.batch_sizes, [2, 1])
        self.assertEqual(result.shape, (3, ACTION_SPACE))
        self.assertTrue(np.isfinite(result[self.mask]).all())
        self.assertTrue(np.isneginf(result[~self.mask]).all())

    def test_single_chunk_returns_session_output_without_an_extra_concat(self):
        result = self.model.eval_batch(self.obs[:1], self.mask[:1])
        self.assertIs(result, self.model.session.last_output)

    def test_list_input_remains_supported(self):
        result = self.model.eval_batch(
            [row for row in self.obs], [row for row in self.mask]
        )
        self.assertEqual(result.shape, (3, ACTION_SPACE))
        self.assertEqual(self.model.session.batch_sizes, [3])

    def test_non_finite_batched_observation_is_rejected_before_inference(self):
        self.obs[1, 0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "有限值"):
            self.model.eval_batch(self.obs, self.mask)
        self.assertEqual(self.model.session.batch_sizes, [])


if __name__ == "__main__":
    unittest.main()
