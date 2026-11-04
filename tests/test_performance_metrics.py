"""Regression tests for bounded operational performance metrics."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from unittest.mock import patch

from server import ops_metrics, review_cache


class PerformanceMetricsTests(unittest.TestCase):
    def setUp(self):
        with ops_metrics._lock:
            ops_metrics._samples.clear()
        with ops_metrics._cpu_lock:
            ops_metrics._cpu_previous = None

    def _report(self, *, timings, counters=None, status=None, total=100.0):
        return {
            "总耗时_ms": total,
            "耗时_ms": timings,
            "数据大小_KB": {},
            "计数": counters or {},
            "状态": status or {},
        }

    def test_stage_percentiles_use_independent_sample_counts(self):
        ops_metrics.record(
            self._report(
                timings={"MJAI转换": 1.0, "ONNX实际推理耗时": 10.0},
                counters={
                    "公开牌谱缓存检查次数": 1,
                    "公开牌谱缓存命中次数": 1,
                    "雀魂登录请求次数": 1,
                    "雀魂登录成功次数": 1,
                    "雀魂账号尝试次数": 2,
                    "雀魂账号尝试失败次数": 1,
                    "雀魂接口541错误次数": 1,
                },
                status={"是否命中本地记录": True, "牌谱获取方式": "雀魂下载"},
            ),
            succeeded=True,
        )
        ops_metrics.record(
            self._report(
                timings={"MJAI转换": 3.0, "ONNX实际推理耗时": None},
                counters={
                    "公开牌谱缓存检查次数": 1,
                    "公开牌谱缓存未命中次数": 1,
                    "雀魂会话复用次数": 1,
                },
                status={"是否命中本地记录": False},
            ),
            succeeded=False,
        )

        result = ops_metrics.summary()
        self.assertEqual(result["stage_ms"]["MJAI转换"]["sample_count"], 2)
        self.assertEqual(result["stage_ms"]["MJAI转换"]["p50"], 2.0)
        self.assertEqual(result["stage_ms"]["MJAI转换"]["p95"], 2.9)
        self.assertEqual(result["stage_ms"]["ONNX实际推理耗时"]["sample_count"], 1)
        self.assertEqual(result["cache"]["public_review"]["checks"], 2)
        self.assertEqual(result["cache"]["public_review"]["hits"], 1)
        self.assertEqual(result["cache"]["public_review"]["misses"], 1)
        self.assertEqual(result["cache"]["public_review"]["hit_rate"], 0.5)
        self.assertEqual(result["account_sessions"]["login_attempts"], 1)
        self.assertEqual(result["account_sessions"]["login_successes"], 1)
        self.assertEqual(result["account_sessions"]["session_reuses"], 1)
        self.assertEqual(result["account_sessions"]["account_attempts"], 2)
        self.assertEqual(result["account_sessions"]["account_attempt_failures"], 1)
        self.assertEqual(result["account_sessions"]["error_541"], 1)
        self.assertEqual(result["account_sessions"]["attempts_per_remote_request"], 2.0)
        self.assertEqual(result["acquisition"]["local_record_hits"], 1)
        self.assertEqual(result["acquisition"]["local_record_misses"], 1)

    def test_recent_samples_remain_bounded(self):
        for value in range(ops_metrics.RECENT_LIMIT + 10):
            ops_metrics.record(
                self._report(timings={"MJAI转换": float(value)}),
                succeeded=True,
            )
        result = ops_metrics.summary()
        self.assertEqual(result["sample_count"], ops_metrics.RECENT_LIMIT)
        self.assertEqual(
            result["stage_ms"]["MJAI转换"]["sample_count"],
            ops_metrics.RECENT_LIMIT,
        )

    def test_process_metrics_expose_peak_rss_and_cpu_sampling_scope(self):
        metrics = ops_metrics.memory_usage()
        self.assertIn("rss_bytes", metrics)
        self.assertIn("rss_peak_bytes", metrics)
        self.assertIn("process_cpu_cores_percent", metrics)
        self.assertIn("process_cpu_machine_percent", metrics)
        self.assertIn("cpu_sample_scope", metrics)

    def test_uint64_filesystem_capacity_sentinel_is_unknown(self):
        sentinel = 18446744073692774400
        usage = shutil._ntuple_diskusage(sentinel, 0, sentinel)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(review_cache.shutil, "disk_usage", return_value=usage):
                metrics = review_cache.storage_metrics(directory)

        self.assertIsNone(metrics["free_bytes"])
        self.assertIsNone(metrics["disk_total_bytes"])
        self.assertIsNone(metrics["low_disk_space"])
        self.assertEqual(metrics["capacity_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
