"""Tests for reusable contiguous inference observation batches."""
from __future__ import annotations

import unittest

import numpy as np

from server.engine.inference_batch import InferenceBatchBuffer


class InferenceBatchBufferTests(unittest.TestCase):
    def test_append_view_and_clear_reuse_the_same_allocations(self):
        batch = InferenceBatchBuffer(2)
        obs_a = np.ones((3, 4), dtype=np.float32)
        obs_b = np.full((3, 4), 2, dtype=np.float32)
        mask_a = np.array([True, False], dtype=np.bool_)
        mask_b = np.array([False, True], dtype=np.bool_)

        self.assertFalse(batch.append(obs_a, mask_a))
        self.assertTrue(batch.append(obs_b, mask_b))
        observations, masks = batch.view()
        self.assertEqual(observations.shape, (2, 3, 4))
        np.testing.assert_array_equal(observations[0], obs_a)
        np.testing.assert_array_equal(observations[1], obs_b)
        np.testing.assert_array_equal(masks[0], mask_a)
        np.testing.assert_array_equal(masks[1], mask_b)
        observation_pointer = batch._observations.__array_interface__["data"][0]

        batch.clear()
        self.assertEqual(batch.count, 0)
        self.assertTrue(batch.append(obs_b, mask_b))
        self.assertEqual(
            batch._observations.__array_interface__["data"][0],
            observation_pointer,
        )

    def test_rejects_overflow_and_mixed_shapes(self):
        batch = InferenceBatchBuffer(1)
        obs = np.zeros((2, 2), dtype=np.float32)
        mask = np.array([True], dtype=np.bool_)
        self.assertTrue(batch.append(obs, mask))
        with self.assertRaises(OverflowError):
            batch.append(obs, mask)
        batch.clear()
        with self.assertRaises(ValueError):
            batch.append(np.zeros((3, 2), dtype=np.float32), mask)


if __name__ == "__main__":
    unittest.main()
