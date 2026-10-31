"""Verification tests for unified Chinese JSON logging requirements."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server.logger import (
    format_chinese_json,
    format_debug_performance,
    is_debug_mode,
    log_debug,
    log_error,
    log_info,
    log_warning,
)


def _is_all_chinese_keys(obj: dict) -> bool:
    """Recursively verify all dictionary keys contain no pure-English keys."""
    chinese_char_pattern = re.compile(r"[\u4e00-\u9fff]")
    for k, v in obj.items():
        if not chinese_char_pattern.search(str(k)):
            return False
        if isinstance(v, dict):
            if not _is_all_chinese_keys(v):
                return False
    return True


def test_format_chinese_json_and_keys():
    line = format_chinese_json(
        "信息",
        事件="牌谱分析",
        请求编号="f48f3af3bd544dd88f273652d62cc4de",
        状态="成功",
        获取方式="本地读取",
        本地读取耗时毫秒=12.5,
        AI检讨耗时毫秒=913.5,
        ONNX推理耗时毫秒=661.9,
        结果序列化耗时毫秒=17.4,
        原始结果字节数=1633005,
        压缩后字节数=153143,
        压缩比例=0.094,
        总耗时毫秒=6449.7,
    )
    data = json.loads(line)
    assert _is_all_chinese_keys(data)
    assert data["级别"] == "信息"
    assert data["事件"] == "牌谱分析"
    assert data["状态"] == "成功"
    assert data["获取方式"] == "本地读取"
    assert data["本地读取耗时毫秒"] == 12.5
    assert data["AI检讨耗时毫秒"] == 913.5
    assert data["ONNX推理耗时毫秒"] == 661.9
    assert "\n" not in line


def test_server_startup_log_in_info_mode(monkeypatch, caplog):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    fake_engine = Mock(status=lambda: {
        "device": "CPUExecutionProvider",
        "has_mortal_weights": True,
    })
    monkeypatch.setattr(server_app, "engine_mgr", fake_engine)
    monkeypatch.setattr(server_app, "_review_tasks", set())
    monkeypatch.setattr(server_app.ms_client, "preload_protocol", lambda: None)

    with caplog.at_level("INFO", logger="majsoul-analyzer"):
        caplog.clear()

        async def run_lifespan():
            async with server_app.lifespan(server_app.app):
                pass

        asyncio.run(run_lifespan())

    records = [r for r in caplog.records if r.name == "majsoul-analyzer"]
    startup_logs = [json.loads(r.getMessage()) for r in records if '"事件":"服务启动"' in r.getMessage()]
    assert len(startup_logs) == 1
    startup = startup_logs[0]
    assert _is_all_chinese_keys(startup)
    assert startup["级别"] == "信息"
    assert startup["事件"] == "服务启动"
    assert startup["状态"] == "就绪"
    assert startup["模型"] == "Mortal S42 ONNX"
    assert startup["推理设备"] == "CPUExecutionProvider"
    assert startup["权重校验"] == "通过"


def test_api_status_polling_produces_no_logs(monkeypatch, caplog):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))

    with caplog.at_level("INFO", logger="majsoul-analyzer"):
        caplog.clear()
        res = client.get("/api/status")
        assert res.status_code == 200

    # Ensure no logs are emitted for /api/status in INFO mode
    assert len(caplog.records) == 0


def test_paipu_analysis_success_emits_single_summary_json(monkeypatch, caplog):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    request_id = "f48f3af3bd544dd88f273652d62cc4de"
    stats = {
        "request_id": request_id,
        "mode": "按需分析",
        "牌谱获取方式": "本地直接读取",
        "是否命中本地记录": True,
        "本地文件读取": 25.4,
        "AI检讨流程耗时": 850.2,
        "ONNX实际推理耗时": 612.3,
        "完成结果序列化耗时": 15.1,
        "原始complete事件字节数": 500000,
        "gzip压缩后字节数": 50000,
        "complete事件生成时间": 900.0,
    }

    with caplog.at_level("INFO", logger="majsoul-analyzer"):
        caplog.clear()
        t0 = time.perf_counter()
        report = server_app._log_performance(stats, t0)

    assert report is not None
    summary_records = [r for r in caplog.records if '"事件":"牌谱分析"' in r.getMessage()]
    assert len(summary_records) == 1
    log_data = json.loads(summary_records[0].getMessage())

    assert _is_all_chinese_keys(log_data)
    assert log_data["级别"] == "信息"
    assert log_data["事件"] == "牌谱分析"
    assert log_data["请求编号"] == request_id
    assert log_data["状态"] == "成功"
    assert log_data["获取方式"] == "本地读取"
    assert log_data["本地读取耗时毫秒"] == 25.4
    assert log_data["AI检讨耗时毫秒"] == 850.2
    assert log_data["ONNX推理耗时毫秒"] == 612.3
    assert log_data["结果序列化耗时毫秒"] == 15.1
    assert log_data["原始结果字节数"] == 500000
    assert log_data["压缩后字节数"] == 50000
    assert log_data["压缩比例"] == 0.1
    assert "总耗时毫秒" in log_data
    # No English keys and no N/A placeholders
    for v in log_data.values():
        assert v != "N/A"


def test_paipu_analysis_failure_emits_error_summary_json(monkeypatch, caplog):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    request_id = "f48f3af3bd544dd88f273652d62cc4de"
    stats = {
        "request_id": request_id,
        "牌谱获取结果": "失败",
        "最后失败阶段": "雀魂牌谱请求",
        "最近雀魂失败类型": "网络失败",
        "雀魂牌谱请求失败次数": 3,
    }

    with caplog.at_level("INFO", logger="majsoul-analyzer"):
        caplog.clear()
        t0 = time.perf_counter()
        server_app._log_performance(stats, t0)

    failure_records = [r for r in caplog.records if '"事件":"牌谱分析"' in r.getMessage()]
    assert len(failure_records) == 1
    log_data = json.loads(failure_records[0].getMessage())

    assert _is_all_chinese_keys(log_data)
    assert log_data["级别"] == "错误"
    assert log_data["事件"] == "牌谱分析"
    assert log_data["请求编号"] == request_id
    assert log_data["状态"] == "失败"
    assert log_data["失败阶段"] == "雀魂牌谱请求"
    assert log_data["错误类型"] == "网络失败"
    assert log_data["重试次数"] == 3
    assert "耗时毫秒" in log_data


def test_debug_mode_restores_detailed_diagnostics(monkeypatch, caplog):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    request_id = "f48f3af3bd544dd88f273652d62cc4de"
    stats = {
        "request_id": request_id,
        "mode": "按需分析",
        "牌谱获取方式": "雀魂下载",
        "雀魂抓取": 4500.0,
        "AI检讨流程耗时": 900.0,
        "ONNX实际推理耗时": 650.0,
        "完成结果序列化耗时": 18.0,
        "原始complete事件字节数": 1000000,
        "gzip压缩后字节数": 100000,
        "complete事件生成时间": 5500.0,
    }

    with caplog.at_level("DEBUG", logger="majsoul-analyzer"):
        caplog.clear()
        t0 = time.perf_counter()
        server_app._log_performance(stats, t0)

    messages = [r.getMessage() for r in caplog.records]
    debug_records = [json.loads(m) for m in messages if '"事件":"牌谱性能诊断"' in m]
    assert len(debug_records) == 1
    diag = debug_records[0]
    assert _is_all_chinese_keys(diag)
    assert diag["级别"] == "调试"
    assert diag["事件"] == "牌谱性能诊断"
    assert diag["请求编号"] == request_id
    assert "耗时明细毫秒" in diag
    assert "数据大小KB" in diag
    assert "阶段计数" in diag
    assert "运行状态" in diag
    assert "压缩比例" in diag


def test_account_credentials_not_leaked(caplog):
    from server.account_pool import _mask_account, _redact_error

    password = "SuperSecretPassword123"
    token = "Access-Token-Secret-456"
    email = "player_one@example.com"

    masked = _mask_account(email)
    assert masked != email
    assert "player_one" not in masked
    assert "@example.com" in masked

    error = Exception(f"Failed connection with token={token} and password={password}")
    redacted = _redact_error(error, token, password)
    assert token not in redacted
    assert password not in redacted
    assert "[已隐藏]" in redacted
