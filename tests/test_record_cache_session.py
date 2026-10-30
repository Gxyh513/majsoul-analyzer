import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server.app as app
import server.account_pool as pool


U1 = "241026-11111111-2222-3333-4444-555555555555"
U2 = "241026-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def record(uuid):
    return {"head": {"uuid": uuid, "accounts": [
        {"seat": 0, "account_id": 11, "nickname": "A"},
        {"seat": 1, "account_id": 22, "nickname": "B"},
    ]}, "actions": []}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(app, "_MEMORY_LOGS", {})
    monkeypatch.setattr(pool, "_rate_limited_until", {})


@pytest.mark.anyio
async def test_link_memory_hit_preserves_url_and_explicit_seat(monkeypatch):
    events, meta = app.to_mjai(record(U1)), app.game_meta(record(U1))
    app.set_memory_log(U1, events, meta)
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    result = await app.resolve_game_data(paipu=f"https://game.maj-soul.com/?paipu={U1}_22")
    assert result[0] == U1 and result[3] == 1
    assert (await app.resolve_game_data(paipu=U1, target_actor=0))[3] == 0
    fetch.assert_not_called()


@pytest.mark.anyio
async def test_fetch_persists_and_concurrent_calls_reuse_disk(monkeypatch, tmp_path):
    acc = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A", close=AsyncMock())
    client.fetch_record = AsyncMock(side_effect=lambda uuid: record(uuid))
    login = AsyncMock()
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: [acc])
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda a: pool.is_client_logged_in_as(a, client))
    monkeypatch.setattr(app, "login_with_account_spec", login)
    lock = asyncio.Lock()
    results = await asyncio.gather(*(pool.fetch_record_with_failover(U1, client, tmp_path, lock) for _ in range(3)))
    assert all(r == record(U1) for r in results)
    assert json.loads((tmp_path / f"{U1}.json").read_text(encoding="utf8")) == record(U1)
    assert not list(tmp_path.glob("*.tmp"))
    assert client.fetch_record.await_count == 1
    login.assert_not_called()
    app._MEMORY_LOGS.clear()
    monkeypatch.setattr(app, "fetch_record_with_failover", AsyncMock())
    assert (await app.resolve_game_data(paipu=U1, target_actor=1))[3] == 1
    app.fetch_record_with_failover.assert_not_called()


@pytest.mark.anyio
async def test_invalid_or_wrong_uuid_cache_refetched(monkeypatch, tmp_path):
    (tmp_path / f"{U1}.json").write_text(json.dumps(record(U2)), encoding="utf8")
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: [{"account": "a", "account_id": 11}])
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A", close=AsyncMock(),
                             fetch_record=AsyncMock(return_value=record(U1)))
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda a: pool.is_client_logged_in_as(a, client))
    assert await pool.fetch_record_with_failover(U1, client, tmp_path, asyncio.Lock()) == record(U1)
    assert json.loads((tmp_path / f"{U1}.json").read_text(encoding="utf8"))["head"]["uuid"] == U1
    client.fetch_record.assert_awaited_once()


@pytest.mark.anyio
async def test_live_backup_and_rate_limited_primary(monkeypatch, tmp_path):
    from server.majsoul.client import MajsoulError
    accounts = [{"account": "a", "account_id": 11}, {"account": "b", "account_id": 22}]
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A")
    async def close():
        client.logged_in = False
    async def login(acc):
        client.logged_in = True
        client._ws = SimpleNamespace(closed=False)
        client.account_id = acc["account_id"]
        client.current_account = acc["account"]
    async def fetch(uuid):
        if client.account_id == 11:
            raise MajsoulError("rate limit", code=540)
        return record(uuid)
    client.close = AsyncMock(side_effect=close)
    client.fetch_record = AsyncMock(side_effect=fetch)
    login_mock = AsyncMock(side_effect=login)
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: accounts[:])
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda a: pool.is_client_logged_in_as(a, client))
    monkeypatch.setattr(app, "login_with_account_spec", login_mock)
    lock = asyncio.Lock()
    await pool.fetch_record_with_failover(U1, client, tmp_path, lock)
    await pool.fetch_record_with_failover(U2, client, tmp_path, lock)
    assert client.fetch_record.await_count == 3
    assert login_mock.await_count == 1
    assert login_mock.await_args.args[0]["account"] == "b"


@pytest.mark.anyio
async def test_disconnected_session_reconnects(monkeypatch, tmp_path):
    acc = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=False, _ws=None, account_id=11, current_account=None,
                             access_token=None, nickname="A", close=AsyncMock(),
                             fetch_record=AsyncMock(return_value=record(U1)))
    login = AsyncMock()
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: [acc])
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda a: pool.is_client_logged_in_as(a, client))
    monkeypatch.setattr(app, "login_with_account_spec", login)
    await pool.fetch_record_with_failover(U1, client, tmp_path, asyncio.Lock())
    login.assert_awaited_once()
    client.fetch_record.assert_awaited_once_with(U1)


@pytest.mark.anyio
async def test_cached_review_uses_disk_record_without_fetch(monkeypatch, tmp_path):
    (tmp_path / f"{U1}.json").write_text(json.dumps(record(U1)), encoding="utf8")
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    monkeypatch.setattr(app, "resolve_engine_cache_info", lambda engine: ("mortal", "weight_hash"))
    monkeypatch.setattr(app, "_valid_cached_review", lambda key, weight: {"cached": True})
    start = AsyncMock()
    monkeypatch.setattr(app, "_start_review_job", start)
    game_id, events, meta, seat = await app.resolve_game_data(paipu=U1, target_actor=1)
    assert await app._run_review_with_cache(game_id, events, meta, seat, "mortal", 0.0) == {"cached": True}
    fetch.assert_not_called()
    start.assert_not_called()


def test_atomic_write_failure_preserves_old_file(monkeypatch, tmp_path):
    path = tmp_path / f"{U1}.json"
    path.write_text("previous", encoding="utf8")
    def fail_replace(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(pool.os, "replace", fail_replace)
    with pytest.raises(OSError):
        pool._save_record(tmp_path, U1, record(U1))
    assert path.read_text(encoding="utf8") == "previous"
    assert not list(tmp_path.glob("*.tmp"))


def test_cleanup_does_not_remove_replaced_record(monkeypatch, tmp_path):
    path = tmp_path / f"{U1}.json"
    path.write_text("old", encoding="utf8")
    observed = path.stat()
    pool._save_record(tmp_path, U1, record(U1))
    assert not app._remove_review_cache_if_unchanged(path, observed)
    assert json.loads(path.read_text(encoding="utf8"))["head"]["uuid"] == U1


def test_review_keys_isolate_game_and_seat():
    events = app.to_mjai(record(U1))
    meta = app.game_meta(record(U1))
    key = app.review_cache_key(U1, events, meta, 0, "mortal", 0.0, "weight")
    assert key != app.review_cache_key(U2, events, meta, 0, "mortal", 0.0, "weight")
    assert key != app.review_cache_key(U1, events, meta, 1, "mortal", 0.0, "weight")
