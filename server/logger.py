"""Unified single-line Chinese JSON logger for majsoul-analyzer."""
from __future__ import annotations

from datetime import datetime
import json
import logging
import os
import sys
from typing import Any

LOGGER_NAME = "majsoul-analyzer"


def is_debug_mode() -> bool:
    return os.environ.get("LOG_LEVEL", "").strip().upper() == "DEBUG"


def get_current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def format_chinese_json(level: str, **kwargs: Any) -> str:
    """Format single-line JSON with Chinese fields."""
    payload: dict[str, Any] = {
        "时间": get_current_timestamp(),
        "级别": level,
    }
    for k, v in kwargs.items():
        if v is not None:
            payload[k] = v
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class ChineseJsonFormatter(logging.Formatter):
    """Ensure any log record emitted is formatted as single-line Chinese JSON."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if msg.startswith("{") and msg.endswith("}"):
            try:
                json.loads(msg)
                return msg
            except Exception:
                pass

        level_map = {
            "DEBUG": "调试",
            "INFO": "信息",
            "WARNING": "警告",
            "ERROR": "错误",
            "CRITICAL": "严重",
        }
        level_cn = level_map.get(record.levelname, "信息")
        data: dict[str, Any] = {
            "时间": datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S"),
            "级别": level_cn,
            "事件": "系统消息",
            "信息": msg,
        }
        if record.exc_info:
            data["异常堆栈"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


_configured = False


def setup_logging():
    """Configure python logging root and suppress verbose third-party logs."""
    global _configured
    if _configured:
        return
    _configured = True
    level = logging.DEBUG if is_debug_mode() else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ChineseJsonFormatter())

    root.handlers.clear()
    root.addHandler(handler)

    log_inst = logging.getLogger(LOGGER_NAME)
    log_inst.setLevel(level)

    # Disable uvicorn access logs completely
    u_access = logging.getLogger("uvicorn.access")
    u_access.disabled = True
    u_access.propagate = False
    u_access.handlers.clear()

    # Suppress verbose third-party logs
    u_err = logging.getLogger("uvicorn.error")
    u_err.setLevel(logging.WARNING if not is_debug_mode() else logging.DEBUG)
    u_err.handlers.clear()
    u_err.propagate = True

    logging.getLogger("uvicorn").setLevel(logging.WARNING if not is_debug_mode() else logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


log = logging.getLogger(LOGGER_NAME)


def log_info(**kwargs: Any):
    log.info(format_chinese_json("信息", **kwargs))


def log_warning(**kwargs: Any):
    log.warning(format_chinese_json("警告", **kwargs))


def log_error(**kwargs: Any):
    log.error(format_chinese_json("错误", **kwargs))


def log_debug(**kwargs: Any):
    if is_debug_mode():
        log.debug(format_chinese_json("调试", **kwargs))


def format_debug_performance(report: dict[str, Any]) -> dict[str, Any]:
    """Convert raw performance report into Chinese JSON fields for DEBUG logging."""
    timings = {k: v for k, v in report.get("耗时_ms", {}).items() if v is not None}
    sizes = {k: v for k, v in report.get("数据大小_KB", {}).items() if v is not None}
    counts = {k: v for k, v in report.get("计数", {}).items() if v is not None}
    status = {k: v for k, v in report.get("状态", {}).items() if v is not None}
    compression = {
        "完成帧压缩比例": report.get("压缩比例", {}).get("complete压缩比例"),
        "检讨JSON压缩比例": report.get("压缩比例", {}).get("gzip与原始检讨JSON比例"),
    }
    compression = {k: v for k, v in compression.items() if v is not None}

    return {
        "事件": "牌谱性能诊断",
        "请求编号": report.get("请求ID"),
        "总耗时毫秒": report.get("总耗时_ms"),
        "耗时明细毫秒": timings,
        "数据大小KB": sizes,
        "阶段计数": counts,
        "运行状态": status,
        "压缩比例": compression,
        "当前进行中阶段": report.get("当前进行中阶段", []),
    }
