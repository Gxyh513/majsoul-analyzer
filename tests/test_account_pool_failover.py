"""Unit and regression tests for Majsoul Multi-Account Pool and Failover Logic.

Verifies:
1. Primary account 540 -> failover to backup account succeeds.
2. All accounts return 540 -> HTTP 429 quota exhaustion.
3. 540 mixed with login failure -> HTTP 400 with accurate breakdown, not misreported as all 540.
4. 540 mixed with network error -> HTTP 400 with accurate breakdown.
5. Error 1203 (paipu not found/forbidden) -> immediate abort, no further accounts wasted.
6. Duplicate accounts deduplication -> only attempted once per unique account.
7. Persisted record hit -> immediate return with 0 network calls.
8. Shared sessions -> concurrent requests without crosstalk or deadlock.
9. Config read/write -> updates token safely without losing existing custom settings.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from server.app import (
    RECORDS_DIR,
    _mask_account,
    _persist_updated_token,
    fetch_record_with_failover,
    get_all_configured_accounts,
    is_client_logged_in_as,
    ms_client,
    resolve_game_data,
)
from server.majsoul.client import MajsoulError
from server.majsoul.session import SessionLock
from server.record_store import RawRecord


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_pool_state(monkeypatch, tmp_path):
    monkeypatch.setattr("server.account_pool._rate_limited_until", {})
    monkeypatch.setattr("server.app.RECORDS_DIR", tmp_path)
    monkeypatch.setattr("server.app.LEGACY_RECORDS_DIRS", ())
    monkeypatch.setattr("server.app._ms_client_lock", SessionLock())
    for attr, value in (("logged_in", False), ("_ws", None), ("account_id", None),
                        ("nickname", None), ("current_account", None), ("access_token", None)):
        monkeypatch.setattr(ms_client, attr, value)
    for name in ("MAJSOUL_ACCOUNTS_JSON", "MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)


async def mock_connect():
    ms_client._ws = MagicMock(closed=False)


# ---------------------------------------------------------------------------
# Test Case 1: Primary 540, Backup Succeeds
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_failover_primary_540_backup_success():
    """Verify that when primary account encounters 540, it cleanly fails over to backup account."""
    mock_cfg = {
        "account": "primary@example.com",
        "password": "pass_primary",
        "nickname": "主账号",
        "account_id": 1001,
        "backup_accounts": [
            {
                "account": "backup1@example.com",
                "password": "pass_backup",
                "nickname": "备用1号",
                "account_id": 1002,
            }
        ],
    }

    test_uuid = "241026-11111111-2222-3333-4444-555555555555"
    expected_record = RawRecord("fixture-v1", b"raw response", b"raw detail")

    attempts = []

    async def mock_login_password(account, password):
        if account == "primary@example.com":
            ms_client.logged_in = True
            ms_client.account_id = 1001
            ms_client.nickname = "主账号"
            ms_client.current_account = account
        elif account == "backup1@example.com":
            ms_client.logged_in = True
            ms_client.account_id = 1002
            ms_client.nickname = "备用1号"
            ms_client.current_account = account

    async def mock_fetch_record(game_uuid):
        attempts.append(ms_client.account_id)
        if ms_client.account_id == 1001:
            raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540 (单账号额度上限)", code=540)
        return expected_record

    with (
        patch("server.app.load_config", return_value=mock_cfg),
        patch.object(ms_client, "connect", new=AsyncMock(side_effect=mock_connect)),
        patch.object(ms_client, "login_password", side_effect=mock_login_password),
        patch.object(ms_client, "fetch_record", side_effect=mock_fetch_record),
    ):
        ms_client.logged_in = False
        ms_client._ws = MagicMock(closed=False)

        res = await fetch_record_with_failover(test_uuid)

        assert res == expected_record
        assert attempts == [1001, 1002]


# ---------------------------------------------------------------------------
# Test Case 2: All Accounts Return 540 -> HTTP 429
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_all_accounts_540_raises_429():
    """Verify that when ALL configured accounts return 540, HTTP 429 is raised with clear explanation."""
    mock_cfg = {
        "account": "acc1@example.com",
        "password": "p1",
        "account_id": 1001,
        "backup_accounts": [
            {"account": "acc2@example.com", "password": "p2", "account_id": 1002},
            {"account": "acc3@example.com", "password": "p3", "account_id": 1003},
        ],
    }

    test_uuid = "241026-22222222-3333-4444-5555-666666666666"

    async def mock_login_password(account, password):
        ms_client.logged_in = True
        ms_client.account_id = 1001 if "acc1" in account else (1002 if "acc2" in account else 1003)
        ms_client.current_account = account

    async def mock_fetch_record(game_uuid):
        raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540", code=540)

    with (
        patch("server.app.load_config", return_value=mock_cfg),
        patch.object(ms_client, "connect", new=AsyncMock(side_effect=mock_connect)),
        patch.object(ms_client, "login_password", side_effect=mock_login_password),
        patch.object(ms_client, "fetch_record", side_effect=mock_fetch_record),
    ):
        ms_client.logged_in = False
        ms_client._ws = MagicMock(closed=False)

        with pytest.raises(HTTPException) as exc_info:
            await fetch_record_with_failover(test_uuid)

        assert exc_info.value.status_code == 429
        assert "抓谱额度暂时耗尽" in exc_info.value.detail
        assert "540" in exc_info.value.detail
        assert "1001" not in exc_info.value.detail and "3 个" not in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test Case 3: 540 Mixed with Login Failure
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_mixed_540_and_login_failure():
    """Verify that mixed outcomes (540 + login failure) are NOT reported as 429 or all quota exhausted."""
    mock_cfg = {
        "account": "acc1@example.com",
        "password": "p1",
        "nickname": "账号1",
        "account_id": 1001,
        "backup_accounts": [
            {"account": "acc2@example.com", "password": "wrong_password", "nickname": "账号2", "account_id": 1002},
            {"account": "acc3@example.com", "password": "p3", "nickname": "账号3", "account_id": 1003},
        ],
    }

    test_uuid = "241026-33333333-4444-5555-6666-777777777777"

    async def mock_login_password(account, password):
        if account == "acc2@example.com":
            raise MajsoulError("账号或密码错误 (错误码 1004)", code=1004)
        ms_client.logged_in = True
        ms_client.account_id = 1001 if "acc1" in account else 1003
        ms_client.current_account = account

    async def mock_fetch_record(game_uuid):
        raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540", code=540)

    with (
        patch("server.app.load_config", return_value=mock_cfg),
        patch.object(ms_client, "connect", new=AsyncMock(side_effect=mock_connect)),
        patch.object(ms_client, "login_password", side_effect=mock_login_password),
        patch.object(ms_client, "fetch_record", side_effect=mock_fetch_record),
    ):
        ms_client.logged_in = False
        ms_client._ws = MagicMock(closed=False)

        with pytest.raises(HTTPException) as exc_info:
            await fetch_record_with_failover(test_uuid)

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert "部分抓谱额度已满" in detail
        assert "其他连接失败" in detail
        assert all(value not in detail for value in ("账号1", "账号2", "账号3", "1001", "1002", "1003", "wrong_password"))


# ---------------------------------------------------------------------------
# Test Case 4: 540 Mixed with Network Error
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_mixed_540_and_network_error():
    """Verify that mixed outcomes (540 + network timeout) accurately report network issue."""
    mock_cfg = {
        "account": "acc1@example.com",
        "password": "p1",
        "nickname": "账号1",
        "account_id": 1001,
        "backup_accounts": [
            {"account": "acc2@example.com", "password": "p2", "nickname": "账号2", "account_id": 1002},
        ],
    }

    test_uuid = "241026-44444444-5555-6666-7777-888888888888"

    async def mock_login_password(account, password):
        if account == "acc2@example.com":
            raise TimeoutError("Connection to gateway timed out")
        ms_client.logged_in = True
        ms_client.account_id = 1001
        ms_client.current_account = account

    async def mock_fetch_record(game_uuid):
        raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540", code=540)

    with (
        patch("server.app.load_config", return_value=mock_cfg),
        patch.object(ms_client, "connect", new=AsyncMock(side_effect=mock_connect)),
        patch.object(ms_client, "login_password", side_effect=mock_login_password),
        patch.object(ms_client, "fetch_record", side_effect=mock_fetch_record),
    ):
        ms_client.logged_in = False
        ms_client._ws = MagicMock(closed=False)

        with pytest.raises(HTTPException) as exc_info:
            await fetch_record_with_failover(test_uuid)

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert "部分抓谱额度已满" in detail
        assert "其他连接失败" in detail
        assert all(value not in detail for value in ("账号1", "账号2", "1001", "1002", "Connection to gateway"))


# ---------------------------------------------------------------------------
# Test Case 5: Error 1203 Terminates Failover Immediately
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_error_1203_stops_failover_immediately():
    """Verify that error 1203 terminates failover without trying subsequent accounts."""
    mock_cfg = {
        "account": "acc1@example.com",
        "password": "p1",
        "account_id": 1001,
        "backup_accounts": [
            {"account": "acc2@example.com", "password": "p2", "account_id": 1002},
            {"account": "acc3@example.com", "password": "p3", "account_id": 1003},
        ],
    }

    test_uuid = "241026-55555555-6666-7777-8888-999999999999"
    attempts = []

    async def mock_login_password(account, password):
        ms_client.logged_in = True
        ms_client.account_id = 1001 if "acc1" in account else (1002 if "acc2" in account else 1003)
        ms_client.current_account = account

    async def mock_fetch_record(game_uuid):
        attempts.append(ms_client.account_id)
        raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 1203（牌谱不存在或无权查看）", code=1203)

    with (
        patch("server.app.load_config", return_value=mock_cfg),
        patch.object(ms_client, "connect", new=AsyncMock(side_effect=mock_connect)),
        patch.object(ms_client, "login_password", side_effect=mock_login_password),
        patch.object(ms_client, "fetch_record", side_effect=mock_fetch_record),
    ):
        ms_client.logged_in = False
        ms_client._ws = MagicMock(closed=False)

        with pytest.raises(HTTPException) as exc_info:
            await fetch_record_with_failover(test_uuid)

        assert exc_info.value.status_code == 400
        assert "1203" in exc_info.value.detail
        assert "牌谱不存在" in exc_info.value.detail
        # Critically: ONLY the first account was called. Failover aborted immediately!
        assert attempts == [1001]


# ---------------------------------------------------------------------------
# Test Case 6: Duplicate Accounts Deduplicated
# ---------------------------------------------------------------------------
def test_account_deduplication():
    """Verify that accounts with identical email, account_id, or token are deduplicated."""
    raw_cfg = {
        "account": "user1@example.com",
        "password": "pw1",
        "account_id": 2001,
        "access_token": "token-a",
        "backup_accounts": [
            # Exact duplicate
            {"account": "user1@example.com", "password": "pw1", "account_id": 2001},
            # Matches by account_id
            {"account": "different_alias@example.com", "account_id": 2001},
            # Matches by access_token
            {"access_token": "token-a"},
            # Matches by uppercase email
            {"account": "USER1@EXAMPLE.COM"},
            # Genuinely unique second account
            {"account": "user2@example.com", "account_id": 2002, "access_token": "token-b"},
            # Genuinely unique third account (only token)
            {"access_token": "token-c"},
        ],
    }

    accs = get_all_configured_accounts(raw_cfg)
    # Expected: exactly 3 unique accounts
    assert len(accs) == 3
    assert accs[0]["account_id"] == 2001
    assert accs[1]["account_id"] == 2002
    assert accs[2]["access_token"] == "token-c"


# ---------------------------------------------------------------------------
# Test Case 7: Stored Records and Network-Only Failover
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_cache_hit_does_not_fetch(tmp_path, monkeypatch):
    """Verify that if paipu JSON is already on disk, it is directly returned without network activity."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    monkeypatch.setattr("server.app.RECORDS_DIR", records_dir)
    existing_uuid = "241026-11111111-2222-3333-4444-555555555555"
    target_file = records_dir / f"{existing_uuid}.json"
    target_file.write_text(json.dumps({
        "head": {
            "uuid": existing_uuid,
            "accounts": [{"account_id": 1, "nickname": "PlayerA"}],
            "result": {"players": [{"total_point": 10000}]},
        },
        "actions": [],
    }), encoding="utf-8")

    with patch("server.app.fetch_record_with_failover", new=AsyncMock()) as mock_failover:
        game_id, mjai_events, meta, seat = await resolve_game_data(paipu=existing_uuid)
        assert game_id == existing_uuid
        assert len(mjai_events) > 0
        mock_failover.assert_not_called()


@pytest.mark.anyio
async def test_failover_fetches_even_when_a_record_is_already_stored(tmp_path, monkeypatch):
    """Network failover does not read, mutate, or replace on-disk records."""
    game_uuid = "241026-11111111-2222-3333-4444-555555555555"
    path = tmp_path / f"{game_uuid}.json"
    stored = {"head": {"uuid": game_uuid, "accounts": []}, "actions": []}
    path.write_text(json.dumps(stored), encoding="utf8")
    source = RawRecord("fixture-v1", b"network response")
    monkeypatch.setattr("server.app.get_all_configured_accounts", lambda: [{"account": "live", "account_id": 11}])
    ms_client.logged_in = True
    ms_client._ws = MagicMock(closed=False)
    ms_client.account_id = 11
    ms_client.current_account = "live"
    fetch = AsyncMock(return_value=source)
    monkeypatch.setattr(ms_client, "fetch_record", fetch)

    assert await fetch_record_with_failover(game_uuid) == source
    fetch.assert_awaited_once_with(game_uuid)
    assert json.loads(path.read_text(encoding="utf8")) == stored
    assert not (tmp_path / f"{game_uuid}.pb").exists()


# ---------------------------------------------------------------------------
# Test Case 8: Concurrent RPCs Share a Session Without Crosstalk
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_concurrent_failover_shares_session_without_crosstalk():
    active = 0
    max_concurrent = 0
    both_started = asyncio.Event()
    mock_cfg = {"account": "concurrent@example.com", "password": "pw", "account_id": 3001}

    async def mock_login_password(account, password):
        ms_client.logged_in = True
        ms_client.account_id = 3001
        ms_client.current_account = account

    async def mock_fetch_record(game_uuid):
        nonlocal active, max_concurrent
        active += 1
        max_concurrent = max(max_concurrent, active)
        if active >= 2:
            both_started.set()
        try:
            await asyncio.wait_for(both_started.wait(), 2)
            await asyncio.sleep(0.01)
            return RawRecord("fixture-v1", game_uuid.encode())
        finally:
            active -= 1

    with (
        patch("server.app.load_config", return_value=mock_cfg),
        patch.object(ms_client, "connect", new=AsyncMock(side_effect=mock_connect)),
        patch.object(ms_client, "login_password", side_effect=mock_login_password) as login,
        patch.object(ms_client, "fetch_record", side_effect=mock_fetch_record) as fetch,
    ):
        uuids = [f"241026-{i:08x}-0000-0000-0000-000000000000" for i in range(5)]
        results = await asyncio.wait_for(asyncio.gather(*(fetch_record_with_failover(uuid) for uuid in uuids)), 3)
        assert results == [RawRecord("fixture-v1", uuid.encode()) for uuid in uuids]
        assert max_concurrent >= 2 and active == 0
        assert login.await_count == 1 and fetch.await_count == len(uuids)


# ---------------------------------------------------------------------------
# Test Case 9: Config Read/Save Preserves Existing Settings
# ---------------------------------------------------------------------------
def test_persist_token_preserves_custom_settings(tmp_path):
    """Verify that saving an updated access_token never loses existing keys or settings."""
    test_config_file = tmp_path / "config.local.json"
    initial_config = {
        "account": "my_account@test.com",
        "password": "secret_password",
        "access_token": "old_token",
        "nickname": "原有昵称",
        "account_id": 5555,
        "selected_model": "akagi",
        "custom_port": 8080,
        "backup_accounts": [
            {
                "account": "backup_acc@test.com",
                "password": "backup_secret",
                "access_token": "old_backup_token",
                "nickname": "备用",
                "account_id": 6666,
                "custom_meta": "preserve_this",
            }
        ],
    }

    test_config_file.write_text(json.dumps(initial_config, ensure_ascii=False), encoding="utf8")

    def mock_load():
        return json.loads(test_config_file.read_text(encoding="utf8"))

    def mock_save(c):
        test_config_file.write_text(json.dumps(c, ensure_ascii=False), encoding="utf8")

    with (
        patch("server.app.load_config", side_effect=mock_load),
        patch("server.app.save_config", side_effect=mock_save),
    ):
        # Update backup account token
        target_acc = {"account": "backup_acc@test.com", "account_id": 6666, "is_primary": False}
        _persist_updated_token(target_acc, "new_shiny_backup_token")

        saved = mock_load()

        # Token updated
        assert saved["backup_accounts"][0]["access_token"] == "new_shiny_backup_token"
        # All original keys preserved
        assert saved["selected_model"] == "akagi"
        assert saved["custom_port"] == 8080
        assert saved["password"] == "secret_password"
        assert saved["backup_accounts"][0]["custom_meta"] == "preserve_this"
        assert saved["backup_accounts"][0]["password"] == "backup_secret"
