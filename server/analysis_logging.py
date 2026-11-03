"""Request-scoped performance reporting and structured diagnostic logs."""
from __future__ import annotations

import time
from typing import Any

from . import ops_metrics
from .logger import format_debug_performance, is_debug_mode, log_debug, log_error, log_info, log_warning
from .paipu_performance import build_report as _build_performance_report


def _log_performance(stats: dict[str, Any], started: float) -> dict[str, Any] | None:
    try:
        report = _build_performance_report(stats, started)
    except Exception:  # noqa: BLE001
        report = None

    try:
        total_ms = (time.perf_counter() - started) * 1000
        request_id = stats.get("request_id") or (report.get("请求ID") if report else None) or ""

        acq_result = stats.get("牌谱获取结果")
        last_failure = stats.get("最后失败阶段") or (report["状态"].get("最后失败阶段") if report and "状态" in report else None)
        if "_succeeded" in stats or "_failed" in stats:
            failed = bool(stats.get("_failed") or not stats.get("_succeeded", True))
        else:
            failed = bool(last_failure or (acq_result and acq_result not in ("成功", "进行中")))
        ops_metrics.record(report, succeeded=not failed)

        if failed:
            fail_stage = last_failure or stats.get("失败阶段") or "雀魂牌谱请求"
            err_type = stats.get("最近雀魂失败类型") or stats.get("错误类型") or "网络失败"
            retries = stats.get("雀魂牌谱请求失败次数") or stats.get("login_failures") or 0
            fail_data: dict[str, Any] = {
                "事件": "牌谱分析",
                "请求编号": request_id,
                "状态": "失败",
                "失败阶段": fail_stage,
                "错误类型": err_type,
                "耗时毫秒": round(total_ms, 1),
            }
            if retries > 0:
                fail_data["重试次数"] = retries
            log_error(**fail_data)
        else:
            acq_mode = stats.get("牌谱获取方式")
            hit_local = stats.get("是否命中本地记录")
            is_local = hit_local or acq_mode in ("本地直接读取", "进程内锁后读取本地", "文件锁后读取本地")
            is_download = acq_mode == "雀魂下载" or (acq_result == "成功" and not is_local)

            success_data: dict[str, Any] = {
                "事件": "牌谱分析",
                "请求编号": request_id,
                "状态": "成功",
            }
            if is_download:
                success_data["获取方式"] = "雀魂下载"
                fetch_ms = stats.get("雀魂抓取") or stats.get("原始牌谱获取总耗时")
                if fetch_ms is not None:
                    success_data["牌谱获取耗时毫秒"] = round(fetch_ms, 1)
            elif is_local:
                success_data["获取方式"] = "本地读取"
                read_ms = stats.get("本地文件读取") or stats.get("原始牌谱获取总耗时")
                if read_ms is not None:
                    success_data["本地读取耗时毫秒"] = round(read_ms, 1)
            elif acq_mode:
                success_data["获取方式"] = acq_mode

            if stats.get("分析结果缓存") is not None:
                success_data["分析结果缓存"] = stats["分析结果缓存"]
            if stats.get("AI检讨流程耗时") is not None:
                success_data["AI检讨耗时毫秒"] = round(stats["AI检讨流程耗时"], 1)
            if stats.get("ONNX实际推理耗时") is not None:
                success_data["ONNX推理耗时毫秒"] = round(stats["ONNX实际推理耗时"], 1)
            if stats.get("完成结果序列化耗时") is not None:
                success_data["结果序列化耗时毫秒"] = round(stats["完成结果序列化耗时"], 1)

            raw_bytes = stats.get("原始complete事件字节数") or stats.get("原始检讨JSON字节数")
            if raw_bytes is not None:
                success_data["原始结果字节数"] = int(raw_bytes)
            gz_bytes = stats.get("gzip压缩后字节数")
            if gz_bytes is not None:
                success_data["压缩后字节数"] = int(gz_bytes)
            if raw_bytes and gz_bytes is not None and raw_bytes > 0:
                success_data["压缩比例"] = round(gz_bytes / raw_bytes, 3)

            success_data["总耗时毫秒"] = round(total_ms, 1)
            log_info(**success_data)

        if is_debug_mode() and report:
            log_debug(**format_debug_performance(report))

        return report
    except Exception:  # noqa: BLE001
        log_warning(事件="牌谱性能诊断", 错误原因="无法输出性能诊断")
        return report


