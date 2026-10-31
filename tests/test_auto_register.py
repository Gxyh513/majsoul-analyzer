"""Unit and regression tests for Majsoul auto-registration and account pool replenishment."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server.account_register import (
    AutoRegisterScheduler,
    RegisterRunner,
    is_platform_restriction_error,
    mask_account,
    redact_secrets,
    register_and_ingest,
)
from server.admin_accounts import AccountScheduler, AccountStore, start_manager, stop_manager


@pytest.fixture
def store(tmp_path: Path) -> AccountStore:
    db_file = tmp_path / "test_admin.sqlite3"
    return AccountStore(db_file)


class DummyMockRunner(RegisterRunner):
    def __init__(self, account_data: dict | None = None, raise_error: Exception | None = None):
        super().__init__()
        self.account_data = account_data or {
            "account": "mj_test123@nqmo.com",
            "password": "Password123!",
            "access_token": "mock-token-uuid-12345",
            "nickname": "国服",
        }
        self.raise_error = raise_error

    async def execute(self) -> dict:
        if self.raise_error:
            raise self.raise_error
        return dict(self.account_data)


def test_mask_account_and_redact_secrets():
    assert mask_account("mj_123456@nqmo.com") == "mj***6@nqmo.com"
    assert mask_account("user@example.com") == "us***r@example.com"
    assert mask_account("ab@c.com") == "a***@c.com"
    assert mask_account(None) == "未知账号"

    text = "Error with token mock-secret-123 and password secretpass!"
    redacted = redact_secrets(text, "mock-secret-123", "secretpass!")
    assert "mock-secret-123" not in redacted
    assert "secretpass!" not in redacted
    assert "[已隐藏]" in redacted


def test_auto_register_success_adds_to_pool(store: AccountStore, tmp_path: Path):
    """测试注册成功后自动验证并加入账号池，更新统计数据。"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    dummy_runner = DummyMockRunner()

    with patch("server.account_register.MajsoulClient") as mock_client_cls:
        client_inst = MagicMock()
        client_inst.connect = AsyncMock()
        client_inst.login_token = AsyncMock()
        client_inst.close = AsyncMock()
        client_inst.account_id = 99887766
        client_inst.nickname = "测试雀士"
        client_inst.access_token = "mock-token-uuid-12345"
        mock_client_cls.return_value = client_inst

        res = asyncio.run(register_and_ingest(store, cache_dir, runner=dummy_runner))

    assert res["ok"] is True
    assert res["status"] == "success"

    # 验证账号已写入数据库
    accounts = store.all()
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc["account"] == "mj_test123@nqmo.com"
    assert acc["account_id"] == 99887766
    assert acc["nickname"] == "测试雀士"
    assert acc["enabled"] == 1
    assert acc["auth_failed"] == 0

    # 验证状态和历史日志
    state = store.get_auto_register_state()
    assert state["success_count"] == 1
    assert state["failure_count"] == 0
    assert state["last_status"] == "成功"
    assert "mj***" in state["last_account_masked"]

    logs = store.get_auto_register_logs()
    assert len(logs) == 1
    assert logs[0]["status"] == "成功"
    assert "mj***" in logs[0]["account_masked"]


def test_auto_register_failure_does_not_pollute_pool(store: AccountStore, tmp_path: Path):
    """测试注册失败时不污染现有账号池，并记录错误日志。"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    dummy_runner = DummyMockRunner(raise_error=RuntimeError("Mail.td 收取验证码超时"))

    res = asyncio.run(register_and_ingest(store, cache_dir, runner=dummy_runner))

    assert res["ok"] is False
    assert res["status"] == "failed"
    assert "收取验证码超时" in res["error"]

    # 账号池保持干净
    assert len(store.all()) == 0

    state = store.get_auto_register_state()
    assert state["success_count"] == 0
    assert state["failure_count"] == 1
    assert state["last_status"] == "失败"

    logs = store.get_auto_register_logs()
    assert len(logs) == 1
    assert logs[0]["status"] == "失败"
    assert "收取验证码超时" in logs[0]["error_message"]


def test_auto_register_duplicate_identification(store: AccountStore, tmp_path: Path):
    """测试重复注册结果能够正确识别并跳过，防止账号池重复。"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 预先在数据库中添加一个账号
    store.add({
        "account": "mj_existing@nqmo.com",
        "password": "Password123!",
        "access_token": "existing-token-abc",
    })
    assert len(store.all()) == 1

    dummy_runner = DummyMockRunner(account_data={
        "account": "mj_existing@nqmo.com",
        "password": "Password123!",
        "access_token": "new-token-xyz",
    })

    res = asyncio.run(register_and_ingest(store, cache_dir, runner=dummy_runner))

    assert res["ok"] is True
    assert res["status"] == "duplicate"
    # 账号数量依然为 1，无重复插入
    assert len(store.all()) == 1


def test_platform_restriction_circuit_breaker(store: AccountStore, tmp_path: Path):
    """测试遇到雀魂平台频控/受限错误（如115、人机验证）时，自动熔断暂停。"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    dummy_runner = DummyMockRunner(raise_error=RuntimeError("请求验证码被拒绝: [115] 获取验证码过于频繁，请稍后重试"))

    res = asyncio.run(register_and_ingest(store, cache_dir, runner=dummy_runner))

    assert res["ok"] is False
    assert res["paused"] is True

    state = store.get_auto_register_state()
    # 自动暂停生效
    assert state["enabled"] == 0
    assert "自动暂停" in state["last_error"]


def test_platform_restriction_keywords():
    assert is_platform_restriction_error("错误码: 115 过于频繁") is True
    assert is_platform_restriction_error("注册通道已关闭 1018") is True
    assert is_platform_restriction_error("触发人机验证 captcha") is True
    assert is_platform_restriction_error("网络连接超时") is False


def test_task_lock_prevents_duplicate_executions(store: AccountStore):
    """测试任务锁防止多个实例同时执行注册任务。"""
    inst1 = "inst-node-1"
    inst2 = "inst-node-2"

    # 实例1获取锁成功
    assert store.acquire_auto_register_lock(inst1, timeout_sec=10) is True
    # 实例2在锁未过期时获取失败
    assert store.acquire_auto_register_lock(inst2, timeout_sec=10) is False

    # 实例1释放锁
    store.release_auto_register_lock(inst1)
    # 实例2获取锁成功
    assert store.acquire_auto_register_lock(inst2, timeout_sec=10) is True
    store.release_auto_register_lock(inst2)


def test_server_restart_recovery_and_hourly_limit(store: AccountStore, tmp_path: Path):
    """测试服务重启后根据上次注册时间正确恢复下次执行时间，绝不立即重复执行。"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    # 模拟在 1200 秒前（20分钟前）刚刚执行过注册
    last_run = now - 1200
    store.update_auto_register_state(last_run_at=last_run, next_run_at=last_run + 3600)

    scheduler = AutoRegisterScheduler(store, cache_dir)
    state = store.get_auto_register_state()

    # 距上次执行不足1小时，计划下次执行时间应为 last_run + 3600
    assert state["last_run_at"] == last_run
    assert state["next_run_at"] == last_run + 3600

    overview = scheduler.overview()
    assert overview["next_run_at"] == last_run + 3600


def test_admin_api_endpoints_and_auth(monkeypatch, tmp_path: Path):
    """测试管理后台各自动注册接口的鉴权、状态获取、开关切换及手动触发。"""
    monkeypatch.setenv("MAJSOUL_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "test-api-token")
    monkeypatch.setenv("MAJSOUL_ADMIN_DB_PATH", str(tmp_path / "admin_test.sqlite3"))

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(start_manager(cache_dir, []))
    try:
        c = TestClient(server_app.app, base_url="http://127.0.0.1")
        headers = {"X-Admin-Key": "test-admin-key", "X-Api-Token": "test-api-token"}

        # 1. 未授权访问拒绝
        unauth = c.get("/api/admin/register/status")
        assert unauth.status_code == 401

        # 2. 获取注册状态
        st_resp = c.get("/api/admin/register/status", headers=headers)
        assert st_resp.status_code == 200
        data = st_resp.json()
        assert "enabled" in data
        assert "next_run_at" in data
        assert "success_count" in data
        assert "failure_count" in data

        # 3. 切换自动注册开关
        toggle_resp = c.post("/api/admin/register/toggle", headers=headers, json={"enabled": False})
        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["enabled"] is False

        toggle_back = c.post("/api/admin/register/toggle", headers=headers, json={"enabled": True})
        assert toggle_back.status_code == 200
        assert toggle_back.json()["enabled"] is True

        # 4. 查看历史日志列表
        hist_resp = c.get("/api/admin/register/history", headers=headers)
        assert hist_resp.status_code == 200
        assert isinstance(hist_resp.json(), list)

        # 5. 总览中同时包含 auto_register 字段
        overview_resp = c.get("/api/admin/status", headers=headers)
        assert overview_resp.status_code == 200
        assert "auto_register" in overview_resp.json()
        assert overview_resp.json()["auto_register"]["enabled"] is True

        # 6. 添加账号及通过 POST action 编辑、删除测试
        add_resp = c.post(
            "/api/admin/accounts",
            headers=headers,
            json={
                "account": "test_act@example.com",
                "password": "pass",
                "access_token": "token123",
                "nickname": "测试账号",
            },
        )
        assert add_resp.status_code == 200
        acc_id = add_resp.json()["id"]

        edit_resp = c.post(
            f"/api/admin/accounts/{acc_id}/edit",
            headers=headers,
            json={"nickname": "已修改备注", "enabled": False},
        )
        assert edit_resp.status_code == 200

        del_resp = c.post(
            f"/api/admin/accounts/{acc_id}/delete",
            headers=headers,
            json={},
        )
        assert del_resp.status_code == 200

        del_again = c.post(
            f"/api/admin/accounts/{acc_id}/delete",
            headers=headers,
            json={},
        )
        assert del_again.status_code == 404

    finally:
        asyncio.run(stop_manager())


def test_download_repo_zipball_extracts_files(tmp_path: Path):
    """测试通过 zipball 下载接口能正常解压出 majsoul_register.py。"""
    import io
    import zipfile
    from server.account_register import _download_repo_zipball

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Gxyh513-majsoul-register-hash/majsoul_register.py", "#!/usr/bin/env python\nprint('ok')\n")
        z.writestr("Gxyh513-majsoul-register-hash/majsoul/__init__.py", "")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = buf.getvalue()

    with patch("httpx.Client.get", return_value=mock_resp):
        ok, err = _download_repo_zipball("test_token", "Gxyh513/majsoul-register", tmp_path)

    assert ok is True
    assert err == ""
    assert (tmp_path / "majsoul_register.py").is_file()
def test_ensure_private_repo_force_update(monkeypatch, tmp_path: Path):
    """测试 force_update=True 能强制重新拉取最新代码并覆盖本地旧文件。"""
    import io
    import zipfile
    from server.account_register import ensure_private_repo

    target_dir = tmp_path / "register"
    target_dir.mkdir(parents=True, exist_ok=True)
    old_file = target_dir / "majsoul_register.py"
    old_file.write_text("# old version\n", encoding="utf-8")

    monkeypatch.setenv("MAJSOUL_REGISTER_DIR", str(target_dir))
    monkeypatch.setenv("MAJSOUL_REGISTER_GITHUB_TOKEN", "mock_token")

    # 1. 默认 force_update=False，直接复用本地已有文件
    reg_dir, err = ensure_private_repo(force_update=False)
    assert reg_dir == target_dir
    assert old_file.read_text(encoding="utf-8") == "# old version\n"

    # 2. force_update=True，触发下载并覆盖
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hash/majsoul_register.py", "# new version updated\n")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = buf.getvalue()

    with patch("httpx.Client.get", return_value=mock_resp):
        reg_dir, err = ensure_private_repo(force_update=True)

    assert reg_dir == target_dir
    assert old_file.read_text(encoding="utf-8") == "# new version updated\n"
