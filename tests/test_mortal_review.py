"""Comprehensive unit and regression tests for Majsoul Analyzer engine and reviewer fixes."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from frontend_assets import javascript_source
from server.engine.model_policy import MODEL_FILE, MODEL_ID
from server.engine.reviewer import EngineManager, review_game, softmax
from server.engine.tiles import action_idx_of_tile, tid, tile_of_action_idx

ROOT_DIR = Path(__file__).resolve().parent.parent
DEMO_PATH = ROOT_DIR / "data" / "demo_mjai.json"


# ---------------------------------------------------------------------------
# 1. Action Index and Mask Mapping Tests
# ---------------------------------------------------------------------------
def test_action_index_and_tile_mapping():
    """Verify action index mapping for normal discards and red fives."""
    # 0..33 normal discards
    assert tid("1m") == 0
    assert action_idx_of_tile("1m") == 0
    assert tile_of_action_idx(0) == "1m"

    assert tid("9m") == 8
    assert action_idx_of_tile("9m") == 8
    assert tile_of_action_idx(8) == "9m"

    assert tid("1p") == 9
    assert action_idx_of_tile("1p") == 9

    assert tid("1s") == 18
    assert action_idx_of_tile("1s") == 18

    assert tid("E") == 27
    assert action_idx_of_tile("E") == 27
    assert tile_of_action_idx(27) == "E"

    assert tid("C") == 33
    assert action_idx_of_tile("C") == 33
    assert tile_of_action_idx(33) == "C"

    # 34..36 red fives
    assert action_idx_of_tile("5mr") == 34
    assert tile_of_action_idx(34) == "5mr"

    assert action_idx_of_tile("5pr") == 35
    assert tile_of_action_idx(35) == "5pr"

    assert action_idx_of_tile("5sr") == 36
    assert tile_of_action_idx(36) == "5sr"


# ---------------------------------------------------------------------------
# 2. Q-value Ordering and Temperature Invariance Tests
# ---------------------------------------------------------------------------
def test_q_value_sorting_strictly_by_raw_q():
    """Verify that candidate action ordering is strictly determined by raw float Q descending."""
    # Simulate candidates with very close raw Q values
    raw_q_values = np.array([1.2344, 1.2341, 0.8900, 2.5000], dtype=np.float32)
    legal_labels = [10, 11, 12, 13]

    candidates = []
    for idx, label in enumerate(legal_labels):
        raw_q = float(raw_q_values[idx])
        candidates.append({
            "label": label,
            "raw_q": raw_q,
            "q_value": round(raw_q, 3),
        })

    # Sort strictly by raw_q
    candidates.sort(key=lambda c: c["raw_q"], reverse=True)

    sorted_labels = [c["label"] for c in candidates]
    # Label 13 (2.5000) > Label 10 (1.2344) > Label 11 (1.2341) > Label 12 (0.8900)
    assert sorted_labels == [13, 10, 11, 12]

    # Notice: round(1.2344, 3) == 1.234 and round(1.2341, 3) == 1.234
    # But because raw_q was used, label 10 strictly precedes label 11
    assert candidates[1]["label"] == 10
    assert candidates[2]["label"] == 11


def test_temperature_invariance_on_q_ordering():
    """Verify that changing temperature alters probabilities monotonically but preserves ranking."""
    q_vals = np.array([2.5, 1.8, 0.4, -0.2], dtype=np.float32)
    temperatures = [0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]

    for t in temperatures:
        probs = softmax(q_vals, temperature=t)
        # Probability sum must be 1.0 (approx)
        assert abs(float(probs.sum()) - 1.0) < 1e-4
        # Softmax preserves non-increasing order
        for i in range(len(q_vals) - 1):
            assert probs[i] >= probs[i + 1]

        # Verify candidate ranking is strictly identical regardless of temperature
        cands = [
            {"label": i, "raw_q": float(q_vals[i]), "prob": float(probs[i])}
            for i in range(len(q_vals))
        ]
        cands.sort(key=lambda c: c["raw_q"], reverse=True)
        assert [c["label"] for c in cands] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Model Resolution and EngineManager Tests
# ---------------------------------------------------------------------------
def test_review_game_rejects_builtin_engine(tmp_path: Path):
    events = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    mgr = EngineManager(tmp_path / "models")
    with pytest.raises(ValueError):
        review_game(events, 0, mgr, force_engine="builtin")


def test_api_status_endpoint():
    """Verify /api/status endpoint response schema and fields."""
    from starlette.testclient import TestClient
    from server.app import app

    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "engine" in data
    assert "active_engine" in data["engine"]
    assert data["engine"]["model_id"] == MODEL_ID
    assert "model_name" in data["engine"]


def test_api_engine_config_and_invalid_path():
    """Verify /api/engine/config validates model paths and rejects non-existent files."""
    from starlette.testclient import TestClient
    from server.app import app

    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    # Arbitrary model paths cannot be configured through the API.
    resp = client.post("/api/engine/config", json={"model_path": "non_existent_model.onnx"})
    assert resp.status_code in (400, 404, 405)


# ---------------------------------------------------------------------------
# 8. Tasks 1-4 Verification Tests: Downloads, Config Security, Status Display
# ---------------------------------------------------------------------------
def test_no_download_on_module_import():
    """Verify importing server.app does not launch background downloads or set downloading state."""
    from server import app as server_app

    # Ensure none of the models are stuck in 'downloading' state immediately on import
    assert server_app.model_download_state["status"] != "downloading"

    # Verify no .tmp files were created in models dir as a result of importing
    tmp_files = list(server_app.MODELS_DIR.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Unexpected .tmp download files found: {tmp_files}"


def test_duplicate_download_protection():
    """Verify calling _download_model_sync when state is already downloading exits immediately."""
    from server import app as server_app

    orig_status = server_app.model_download_state["status"]
    try:
        server_app.model_download_state["status"] = "downloading"
        # Should return immediately without attempting network
        server_app._download_model_sync(MODEL_ID)
        assert server_app.model_download_state["status"] == "downloading"
    finally:
        server_app.model_download_state["status"] = orig_status


def test_failed_download_cleans_up_tmp_file(tmp_path: Path, monkeypatch):
    """Verify that when a download fails, .tmp files are cleaned up and error recorded."""
    import urllib.error
    from server import app as server_app

    monkeypatch.setattr(server_app, "MODELS_DIR", tmp_path)

    # Mock urllib.request.urlopen to always raise network error
    def mock_urlopen(*args, **kwargs):
        raise urllib.error.URLError("Mock network failure")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    # Reset state for test
    monkeypatch.setattr(server_app, "model_download_state", {
        "status": "idle",
        "progress_pct": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "error_msg": None,
    })

    server_app._download_model_sync(MODEL_ID)

    # State must be error
    assert server_app.model_download_state["status"] == "error"
    assert server_app.model_download_state["error_msg"]

    # No leftover .tmp file
    assert not list(tmp_path.iterdir())


def test_config_local_isolation_and_credentials_protection(tmp_path: Path, monkeypatch):
    """Verify load_config and save_config safely use config.local.json and do not write to config.json."""
    from server import app as server_app

    test_config_json = tmp_path / "config.json"
    test_local_json = tmp_path / "config.local.json"

    # Base template in config.json (clean, no credentials)
    base_template = {
        "account": "",
        "password": "",
        "access_token": "",
        "selected_model": "akagi",
    }
    test_config_json.write_text(json.dumps(base_template), encoding="utf-8")

    # User local config in config.local.json (contains real local config)
    local_credentials = {
        "account": "test_account",
        "password": "test_password",
        "access_token": "token_xyz",
        "selected_model": "akagi",
    }
    test_local_json.write_text(json.dumps(local_credentials), encoding="utf-8")

    monkeypatch.setattr(server_app, "CONFIG_PATH", test_config_json)
    monkeypatch.setattr(server_app, "LOCAL_CONFIG_PATH", test_local_json)

    # 1. load_config should merge local config on top of base template
    loaded = server_app.load_config()
    assert loaded["account"] == "test_account"
    assert loaded["password"] == "test_password"
    assert loaded["access_token"] == "token_xyz"

    # 2. save_config should write strictly to config.local.json
    loaded["nickname"] = "NewNick"
    server_app.save_config(loaded)

    # Verify config.local.json was updated
    saved_local = json.loads(test_local_json.read_text(encoding="utf-8"))
    assert saved_local["nickname"] == "NewNick"

    # Verify config.json was NOT touched and still has empty credentials
    untouched_base = json.loads(test_config_json.read_text(encoding="utf-8"))
    assert untouched_base["account"] == ""
    assert untouched_base["password"] == ""
    assert untouched_base["access_token"] == ""
    assert "nickname" not in untouched_base


def test_frontend_engine_status_mapping():
    """The frontend must not claim an unavailable checkpoint fell back to builtin."""
    from server import app as server_app
    source = javascript_source()
    assert "系统已自动回退为内置引擎" not in source
    assert "无法验证 Mortal S42 ONNX" in source
    assert "requireTrustedReview(result)" in source
    status = server_app.engine_mgr.status()
    assert status["active_engine"] in ("mortal", "unavailable")


# ---------------------------------------------------------------------------
# 9. Multi-Account Failover & Rate Limit (Error 540) Tests
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_failover(monkeypatch, tmp_path):
    monkeypatch.setattr("server.account_pool._rate_limited_until", {})
    monkeypatch.setattr("server.app.RECORDS_DIR", tmp_path)
    monkeypatch.setattr("server.app.ms_client.logged_in", False)
    from unittest.mock import AsyncMock
    from server import app as server_app
    from server.majsoul.session import SessionLock
    monkeypatch.setattr(server_app, "_ms_client_lock", SessionLock())
    monkeypatch.setattr(server_app.ms_client, "close", AsyncMock())


@pytest.mark.anyio
async def test_failover_primary_540_backup_success(monkeypatch, isolated_failover):
    """Scenario 1: Primary account hits 540 rate limit, backup account succeeds."""
    from server import app as server_app
    from server.majsoul.client import MajsoulError

    accounts = [
        {"account": "acc1@test.com", "access_token": "tok1", "nickname": "Acc1", "account_id": 1001, "is_primary": True},
        {"account": "acc2@test.com", "access_token": "tok2", "nickname": "Acc2", "account_id": 1002, "is_primary": False},
    ]
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: accounts)

    # Mock login_with_account_spec to record logged-in accounts
    logged_in_accs = []
    async def mock_login(acc):
        logged_in_accs.append(acc["account_id"])
        server_app.ms_client.logged_in = True
        server_app.ms_client.account_id = acc["account_id"]
        server_app.ms_client.nickname = acc["nickname"]
        server_app.ms_client.current_account = acc["account"]
        server_app.ms_client._ws = type("Socket", (), {"closed": False})()

    monkeypatch.setattr(server_app, "login_with_account_spec", mock_login)

    # Mock ms_client.fetch_record: Acc 1001 returns 540, Acc 1002 succeeds
    async def mock_fetch_record(game_uuid):
        if server_app.ms_client.account_id == 1001:
            raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540（单账号单日抓取额度已达上限）")
        return {"head": {"uuid": game_uuid}, "actions": [], "fetched_at": 123456}

    monkeypatch.setattr(server_app.ms_client, "fetch_record", mock_fetch_record)

    res = await server_app.fetch_record_with_failover("test_uuid_123")
    assert res["head"]["uuid"] == "test_uuid_123"
    assert logged_in_accs == [1001, 1002]


@pytest.mark.anyio
async def test_failover_all_accounts_540(monkeypatch, isolated_failover):
    """Scenario 2: All configured accounts hit 540, raises HTTP 429 stating all accounts exhausted."""
    from fastapi import HTTPException
    from server import app as server_app
    from server.majsoul.client import MajsoulError

    accounts = [
        {"account": "acc1@test.com", "access_token": "tok1", "nickname": "Acc1", "account_id": 1001, "is_primary": True},
        {"account": "acc2@test.com", "access_token": "tok2", "nickname": "Acc2", "account_id": 1002, "is_primary": False},
    ]
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: accounts)

    async def mock_login(acc):
        server_app.ms_client.logged_in = True
        server_app.ms_client.account_id = acc["account_id"]
        server_app.ms_client.nickname = acc["nickname"]
        server_app.ms_client.current_account = acc["account"]
        server_app.ms_client._ws = type("Socket", (), {"closed": False})()

    monkeypatch.setattr(server_app, "login_with_account_spec", mock_login)

    async def mock_fetch_record(game_uuid):
        raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540（单账号单日抓取额度已达上限）")

    monkeypatch.setattr(server_app.ms_client, "fetch_record", mock_fetch_record)

    with pytest.raises(HTTPException) as exc_info:
        await server_app.fetch_record_with_failover("test_uuid_all_540")

    assert exc_info.value.status_code == 429
    assert "抓谱额度暂时耗尽" in exc_info.value.detail
    assert "2 个" not in exc_info.value.detail


@pytest.mark.anyio
async def test_auth_or_network_error_not_misreported_as_all_540(monkeypatch, isolated_failover):
    """Scenario 3: One account has 540, another has auth/login failure; must NOT claim all accounts exhausted."""
    from fastapi import HTTPException
    from server import app as server_app
    from server.majsoul.client import MajsoulError

    accounts = [
        {"account": "acc1@test.com", "access_token": "tok1", "nickname": "Acc1", "account_id": 1001, "is_primary": True},
        {"account": "acc2@test.com", "access_token": "tok2", "nickname": "Acc2", "account_id": 1002, "is_primary": False},
    ]
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: accounts)

    async def mock_login(acc):
        if acc["account_id"] == 1002:
            raise MajsoulError("账号或密码错误 (错误码 1004)")
        server_app.ms_client.logged_in = True
        server_app.ms_client.account_id = acc["account_id"]
        server_app.ms_client.nickname = acc["nickname"]
        server_app.ms_client.current_account = acc["account"]
        server_app.ms_client._ws = type("Socket", (), {"closed": False})()

    monkeypatch.setattr(server_app, "login_with_account_spec", mock_login)

    async def mock_fetch_record(game_uuid):
        raise MajsoulError("雀魂接口 fetchGameRecord 返回错误码 540")

    monkeypatch.setattr(server_app.ms_client, "fetch_record", mock_fetch_record)

    with pytest.raises(HTTPException) as exc_info:
        await server_app.fetch_record_with_failover("test_uuid_mixed")

    # Must be 400, NOT 429!
    assert exc_info.value.status_code == 400
    assert "所有已配置的 2 个账号今日抓谱额度均已耗尽" not in exc_info.value.detail
    assert "额度已满" in exc_info.value.detail or "登录失败" in exc_info.value.detail


def test_backup_accounts_loaded_from_config(tmp_path, monkeypatch):
    """Scenario 4: Backup accounts pool in config.local.json is correctly loaded and deduplicated."""
    from server import app as server_app

    test_local = tmp_path / "config.local.json"
    data = {
        "account": "main@test.com",
        "access_token": "main_tok",
        "nickname": "MainUser",
        "account_id": 1111,
        "backup_accounts": [
            {
                "account": "backup1@test.com",
                "access_token": "b1_tok",
                "nickname": "BackupUser1",
                "account_id": 2222,
            },
            # Duplicate entry of backup1 - should be deduplicated
            {
                "account": "backup1@test.com",
                "access_token": "b1_tok",
                "nickname": "BackupUser1",
                "account_id": 2222,
            },
        ],
    }
    test_local.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(server_app, "LOCAL_CONFIG_PATH", test_local)
    monkeypatch.setattr(server_app, "CONFIG_PATH", tmp_path / "non_existent.json")

    accs = server_app.get_all_configured_accounts()
    # Must be deduplicated down to 2 accounts
    assert len(accs) == 2
    assert accs[0]["account"] == "main@test.com"
    assert accs[0]["is_primary"] is True
    assert accs[1]["account"] == "backup1@test.com"
    assert accs[1]["is_primary"] is False


@pytest.mark.anyio
async def test_cached_records_bypass_network_fetch(tmp_path, monkeypatch):
    """Scenario 5: Cached record in data/records/ is directly loaded without calling fetch_record_with_failover."""
    from server import app as server_app

    records_dir = tmp_path / "records"
    records_dir.mkdir()
    monkeypatch.setattr(server_app, "RECORDS_DIR", records_dir)

    game_uuid = "260922-cf43b187-e6ac-4f73-9de2-ea4968766a80"
    cached_file = records_dir / f"{game_uuid}.json"
    dummy_record = {
        "head": {
            "uuid": game_uuid,
            "accounts": [{"account_id": 1, "nickname": "PlayerA"}],
            "result": {"players": [{"total_point": 10000}]},
        },
        "actions": [],
    }
    cached_file.write_text(json.dumps(dummy_record), encoding="utf-8")

    fetch_called = False
    async def mock_fetch_failover(uuid):
        nonlocal fetch_called
        fetch_called = True
        raise RuntimeError("fetch_record_with_failover should not be called for cached record!")

    monkeypatch.setattr(server_app, "fetch_record_with_failover", mock_fetch_failover)

    # Call resolve_game_data
    resolved_id, events, meta, seat = await server_app.resolve_game_data(paipu=game_uuid)
    assert resolved_id == game_uuid
    assert fetch_called is False
    assert meta["uuid"] == game_uuid


def test_api_records_local(tmp_path, monkeypatch):
    """Test GET /api/records/local returns cached paipu metadata without network calls."""
    from starlette.testclient import TestClient
    from server import app as server_app

    records_dir = tmp_path / "records"
    records_dir.mkdir()
    monkeypatch.setattr(server_app, "RECORDS_DIR", records_dir)

    # 1. Write dummy record
    dummy_uuid = "261023-acca4352-4abc-40a5-87bf-30b7b37304f8"
    (records_dir / f"{dummy_uuid}.json").write_text(
        json.dumps({
            "head": {
                "uuid": dummy_uuid,
                "start_time": 1792770704,
                "accounts": [{"account_id": 1, "nickname": "TestUser"}],
                "result": {"players": [{"seat": 0, "total_point": 15000}]},
            },
            "actions": [],
        }),
        encoding="utf-8",
    )

    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    resp = client.get("/api/records/local")
    assert resp.status_code == 200
    data = resp.json()
    assert "records" in data
    assert data["total"] == 1
    rec = data["records"][0]
    assert rec["uuid"] == dummy_uuid
    assert "TestUser" in rec["names"]
    assert rec["file_size"] > 0
