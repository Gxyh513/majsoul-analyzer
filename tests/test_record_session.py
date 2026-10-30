"""Record storage and account session behavior after separating network failover."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import server.app as app
import server.account_pool as pool
import server.record_store as store
from server.majsoul.client import MajsoulError
from server.majsoul.session import SessionLock
from server.record_store import RawRecord


U1 = "241026-11111111-2222-3333-4444-555555555555"
U2 = "241026-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def record(uuid):
    return {"head": {"uuid": uuid, "accounts": [
        {"seat": 0, "account_id": 11, "nickname": "A"},
        {"seat": 1, "account_id": 22, "nickname": "B"},
    ]}, "actions": []}


def raw(uuid):
    return RawRecord("fixture-v1", f"response:{uuid}".encode(), b"detail")


def install_client(monkeypatch, client, accounts):
    monkeypatch.setattr(app, "ms_client", client)
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: accounts[:])
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda acc: pool.is_client_logged_in_as(acc, client))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "LEGACY_RECORDS_DIRS", ())
    monkeypatch.setattr(app, "_ms_client_lock", SessionLock())
    monkeypatch.setattr(app, "load_config", lambda: {})
    monkeypatch.setattr(pool, "_rate_limited_until", {})


@pytest.mark.anyio
async def test_stored_link_preserves_url_and_explicit_seat(monkeypatch, tmp_path):
    (tmp_path / f"{U1}.json").write_text(json.dumps(record(U1)), encoding="utf8")
    fetch = AsyncMock(side_effect=AssertionError("unexpected download"))
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    result = await app.resolve_game_data(paipu=f"https://game.maj-soul.com/?paipu={U1}_22")
    assert result[0] == U1 and result[3] == 1
    assert (await app.resolve_game_data(paipu=U1, target_actor=0))[3] == 0
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_fetch_persists_and_concurrent_calls_reuse_raw_record(monkeypatch, tmp_path):
    acc = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A", close=AsyncMock(),
                             fetch_record=AsyncMock(side_effect=lambda uuid: raw(uuid)))
    install_client(monkeypatch, client, [acc])
    login = AsyncMock()
    monkeypatch.setattr(app, "login_with_account_spec", login)
    results = await asyncio.gather(*(app.get_or_download_record(U1) for _ in range(3)))
    assert results == [raw(U1)] * 3
    assert store.read_record(tmp_path, U1) == raw(U1)
    assert (tmp_path / f"{U1}.pb").is_file() and not list(tmp_path.glob("*.tmp"))
    client.fetch_record.assert_awaited_once_with(U1)
    login.assert_not_awaited()
    assert await app.get_or_download_record(U1) == raw(U1)
    client.fetch_record.assert_awaited_once_with(U1)


@pytest.mark.anyio
async def test_fetched_record_is_decoded_and_converted_for_each_request(monkeypatch):
    acc = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A", close=AsyncMock(),
                             fetch_record=AsyncMock(return_value=raw(U1)),
                             decode_record=Mock(return_value=record(U1)))
    install_client(monkeypatch, client, [acc])
    converter = Mock(wraps=app.to_mjai)
    monkeypatch.setattr(app, "to_mjai", converter)
    for target in (0, 1):
        game_id, events, meta, seat = await app.resolve_game_data(paipu=U1, target_actor=target)
        assert (game_id, seat, meta["uuid"]) == (U1, target, U1)
        assert events
    assert converter.call_count == client.decode_record.call_count == 2
    client.fetch_record.assert_awaited_once_with(U1)


@pytest.mark.anyio
async def test_wrong_uuid_legacy_record_is_preserved_without_refetch(monkeypatch, tmp_path):
    path = tmp_path / f"{U1}.json"
    path.write_text(json.dumps(record(U2)), encoding="utf8")
    before = path.read_bytes()
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    with pytest.raises(app.HTTPException, match="损坏") as error:
        await app.resolve_game_data(paipu=U1)
    assert error.value.status_code == 500
    assert path.read_bytes() == before
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_live_backup_and_rate_limited_primary(monkeypatch):
    accounts = [{"account": "a", "account_id": 11}, {"account": "b", "account_id": 22}]
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A")

    async def close():
        client.logged_in = False
        client._ws = None

    async def login(acc):
        client.logged_in = True
        client._ws = SimpleNamespace(closed=False)
        client.account_id = acc["account_id"]
        client.current_account = acc["account"]

    async def fetch(uuid):
        if client.account_id == 11:
            raise MajsoulError("rate limit", code=540)
        return raw(uuid)

    client.close = AsyncMock(side_effect=close)
    client.fetch_record = AsyncMock(side_effect=fetch)
    login_mock = AsyncMock(side_effect=login)
    install_client(monkeypatch, client, accounts)
    monkeypatch.setattr(app, "login_with_account_spec", login_mock)
    lock = SessionLock()
    assert await pool.fetch_record_with_failover(U1, client, lock) == raw(U1)
    assert await pool.fetch_record_with_failover(U2, client, lock) == raw(U2)
    assert client.fetch_record.await_count == 3
    login_mock.assert_awaited_once_with(accounts[1])


@pytest.mark.anyio
async def test_disconnected_session_reconnects(monkeypatch):
    acc = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=False, _ws=None, account_id=11, current_account=None,
                             access_token=None, nickname="A", close=AsyncMock(),
                             fetch_record=AsyncMock(return_value=raw(U1)))

    async def login(_acc):
        client.logged_in = True
        client._ws = SimpleNamespace(closed=False)
        client.current_account = "a"

    install_client(monkeypatch, client, [acc])
    login_mock = AsyncMock(side_effect=login)
    monkeypatch.setattr(app, "login_with_account_spec", login_mock)
    assert await pool.fetch_record_with_failover(U1, client, SessionLock()) == raw(U1)
    login_mock.assert_awaited_once_with(acc)
    client.fetch_record.assert_awaited_once_with(U1)


@pytest.mark.anyio
async def test_storage_failure_does_not_retry_accounts(monkeypatch, tmp_path):
    acc = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token=None, nickname="A", close=AsyncMock(),
                             fetch_record=AsyncMock(return_value=raw(U1)))
    install_client(monkeypatch, client, [acc])

    def fail_save(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save_record", fail_save)
    with pytest.raises(app.HTTPException, match="保存失败") as error:
        await app.get_or_download_record(U1)
    assert error.value.status_code == 500
    client.fetch_record.assert_awaited_once_with(U1)
    client.close.assert_not_awaited()
    assert not (tmp_path / f"{U1}.pb").exists()


def test_atomic_write_failure_leaves_no_partial_record(monkeypatch, tmp_path):
    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(store.os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.save_record(tmp_path, U1, raw(U1))
    assert not (tmp_path / f"{U1}.pb").exists()
    assert not list(tmp_path.glob("*.tmp"))
