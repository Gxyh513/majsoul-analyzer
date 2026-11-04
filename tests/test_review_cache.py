"""Regression tests for the bounded public review cache."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from server import review_cache


class ReviewCacheEvictionTests(unittest.TestCase):
    def test_eviction_trims_to_soft_budget_and_keeps_newest_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            size = 100 * 1024 * 1024
            base_time = 1_800_000_000
            for index in range(3):
                path = root / f"{index + 1:064x}.json"
                with path.open("wb") as handle:
                    handle.truncate(size)
                os.utime(path, (base_time + index, base_time + index))
                entries.append(path)

            review_cache._evict(root)

            self.assertFalse(entries[0].exists())
            self.assertTrue(entries[1].exists())
            self.assertTrue(entries[2].exists())
            self.assertLessEqual(
                sum(path.stat().st_size for path in root.glob("*.json")),
                review_cache.CACHE_SOFT_BUDGET_BYTES,
            )
            metrics = review_cache.storage_metrics(root)
            self.assertEqual(metrics["budget_bytes"], review_cache.CACHE_BUDGET_BYTES)
            self.assertEqual(metrics["soft_budget_bytes"], review_cache.CACHE_SOFT_BUDGET_BYTES)


if __name__ == "__main__":
    unittest.main()
