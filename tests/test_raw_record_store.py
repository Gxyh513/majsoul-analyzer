import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from server import app
from server import record_store
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256
from server.majsoul.client import MajsoulClient, MajsoulError
from server.majsoul.proto import LiqiProto
from server.majsoul.session import SessionLock
from server.record_store import CorruptRecordError, RawRecord, RecordStorageError

U1 = "241026-11111111-2222-3333-4444-555555555555"
U2 = "241026-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VERSION = "0.11.252.w"
PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"


@pytest.fixture
def proto():
    return LiqiProto(json.loads((PROTOCOL_DIR / f"liqi-{VERSION}.json").read_text(encoding="utf8")))


def raw_record(proto, game_uuid):
    head = proto.get("RecordGame")()
    head.uuid = game_uuid
    for seat in range(4):
        account = head.accounts.add()
        account.seat = seat
        account.account_id = 10 + seat
        account.nickname = f"player{seat}"
    round_ = proto.get("RecordNewRound")()
    round_.dora = "1p"
    round_.scores.extend([25000] * 4)
    for seat in range(4):
        getattr(round_, f"tiles{seat}").extend(["1m"] * 13)
    detail = proto.get("GameDetailRecords")()
    detail.version = 210715
    detail.actions.add().result = proto.wrap(".lq.RecordNewRound", round_.SerializeToString())
    response = proto.get("ResGameRecord")()
    response.head.CopyFrom(head)
    response.data = proto.wrap(".lq.GameDetailRecords", detail.SerializeToString())
    return RawRecord(VERSION, response.SerializeToString())


@pytest.fixture
def isolated_app(monkeypatch, tmp_path):
    client = MajsoulClient(PROTOCOL_DIR)
    monkeypatch.setattr(app, "ms_client", client)
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "LEGACY_RECORDS_DIRS", ())
    monkeypatch.setattr(app, "load_config", lambda: {})
    return client


def fake_review(seat):
    return {"target_actor": seat, "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
            "weight_sha256": MODEL_SHA256, "revision": MODEL_REVISION, "version": 4, "steps": None, "q_source": "neural_network",
            "review_schema": app.REVIEW_SCHEMA}}


def test_real_model_api_reanalyses_without_persisting_results(monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    from server.engine.reviewer import EngineManager

    models = app.DATA_DIR / "models"
    if not (models / MODEL_FILE).is_file():
        pytest.skip("published model not present")
    monkeypatch.setattr(app, "engine_mgr", EngineManager(models))
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    reviewer = Mock(wraps=app.review_game)
    monkeypatch.setattr(app, "review_game", reviewer)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    first = client.post("/api/analyze/demo", json={"target_actor": 0})
    second = client.post("/api/analyze/stream", json={"paipu": "demo", "target_actor": 0})
    assert first.status_code == second.status_code == 200
    engine = first.json()["engine"]
    assert engine["q_source"] == "neural_network"
    assert engine["id"] == MODEL_ID and engine["weight_file"] == MODEL_FILE
    assert engine["revision"] == MODEL_REVISION and engine["weight_sha256"] == MODEL_SHA256
    assert engine["version"] == 4 and engine["steps"] is None
    assert engine["review_schema"] == app.REVIEW_SCHEMA
    complete = next(json.loads(line.removeprefix("data: ")) for line in second.text.splitlines()
                    if line.startswith("data: ") and json.loads(line.removeprefix("data: ")).get("type") == "complete")
    assert complete["result"]["engine"] == engine
    assert '"cache_persisted"' not in second.text
    assert "X-Review-Cache-Persisted" not in first.headers
    assert reviewer.call_count == 2
    assert not list(tmp_path.iterdir())


def test_raw_protobuf_to_real_model_api_twice(monkeypatch, tmp_path, proto):
    from starlette.testclient import TestClient
    from server.engine.reviewer import EngineManager

    models = app.DATA_DIR / "models"
    if not (models / MODEL_FILE).is_file():
        pytest.skip("published model not present")
    source = raw_record(proto, U1)
    response = proto.decode("ResGameRecord", source.response)
    demo = json.loads(app.DEMO_PATH.read_text(encoding="utf8"))
    first_round = next(event for event in demo if event["type"] == "start_kyoku")
    honors = {"E": "1z", "S": "2z", "W": "3z", "N": "4z", "P": "5z", "F": "6z", "C": "7z"}
    def to_majsoul(tile):
        return honors.get(tile, "0" + tile[1] if tile.endswith("r") else tile)
    round_ = proto.get("RecordNewRound")()
    round_.dora = to_majsoul(first_round["dora_marker"])
    round_.scores.extend(first_round["scores"])
    for seat, hand in enumerate(first_round["tehais"]):
        getattr(round_, f"tiles{seat}").extend(map(to_majsoul, hand + (["2s"] if seat == 0 else [])))
    discard = proto.get("RecordDiscardTile")()
    discard.seat, discard.tile, discard.moqie = 0, "2s", True
    abort = proto.get("RecordLiuJu")()
    abort.type, abort.seat = 1, 0
    detail = proto.get("GameDetailRecords")()
    detail.version = 210715
    for name, action in (("RecordNewRound", round_), ("RecordDiscardTile", discard), ("RecordLiuJu", abort)):
        detail.actions.add().result = proto.wrap(".lq." + name, action.SerializeToString())
    response.data = proto.wrap(".lq.GameDetailRecords", detail.SerializeToString())
    fetched = RawRecord(VERSION, response.SerializeToString())
    download = AsyncMock(return_value=fetched)
    monkeypatch.setattr(app, "fetch_record_with_failover", download)
    monkeypatch.setattr(app, "ms_client", MajsoulClient(PROTOCOL_DIR))
    monkeypatch.setattr(app, "engine_mgr", EngineManager(models))
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "LEGACY_RECORDS_DIRS", ())
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    reviewer = Mock(wraps=app.review_game)
    monkeypatch.setattr(app, "review_game", reviewer)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    for seat in (0, 1):
        result = client.post("/api/analyze/paipu", json={"paipu": f"{U1}_{seat}"})
        assert result.status_code == 200, result.text
        assert result.json()["target_actor"] == seat
        assert result.json()["engine"]["q_source"] == "neural_network"
    download.assert_awaited_once_with(U1)
    assert reviewer.call_count == 2
    assert (tmp_path / f"{U1}.pb").exists()
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.anyio
async def test_first_and_second_analysis_redecode_and_reanalyse(monkeypatch, isolated_app, proto, tmp_path):
    source = raw_record(proto, U1)
    fetch = AsyncMock(return_value=source)
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    decoder = Mock(wraps=isolated_app.decode_record)
    converter = Mock(wraps=app.to_mjai)
    monkeypatch.setattr(isolated_app, "decode_record", decoder)
    monkeypatch.setattr(app, "to_mjai", converter)
    monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(get_mortal=lambda _: (SimpleNamespace(weight_sha256=MODEL_SHA256), MODEL_ID, None)))
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    reviewer = Mock(side_effect=lambda events, seat, manager, **kw: fake_review(seat))
    monkeypatch.setattr(app, "review_game", reviewer)

    for seat in (0, 1):
        game_id, events, meta, selected = await app.resolve_game_data(paipu=U1, target_actor=seat)
        result = await app._run_review(game_id, events, meta, selected, MODEL_ID, 0.1)
        assert result["target_actor"] == seat
    fetch.assert_awaited_once_with(U1)
    assert (tmp_path / f"{U1}.pb").is_file()
    assert record_store.read_record(tmp_path, U1) == source
    assert decoder.call_count == converter.call_count == reviewer.call_count == 2
    assert not (tmp_path / "reviews").exists()
    assert not (tmp_path / "metadata").exists()


def test_stream_reports_download_then_local_record(monkeypatch, isolated_app, proto, tmp_path):
    from starlette.testclient import TestClient

    source = raw_record(proto, U1)
    fetch = AsyncMock(return_value=source)
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=MODEL_SHA256), MODEL_ID, None)))
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.setattr(app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(app, "review_game", lambda events, seat, manager, **kw: fake_review(seat))
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    reports = []
    for index in range(2):
        request_id = str(index + 1) * 32
        response = client.post("/api/analyze/stream", json={"paipu": U1, "target_actor": 1},
                               headers={"X-Request-ID": request_id})
        assert response.status_code == 200
        events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        complete = next(event for event in events if event["type"] == "complete")
        assert complete["result"]["game_id"] == U1
        assert complete["result"]["target_actor"] == 1
        report = next(event["performance"] for event in events if event["type"] == "performance")
        assert report["请求ID"] == request_id
        assert report["状态"]["牌谱获取结果"] == "成功"
        size = len(source.response) + len(source.detail)
        assert report["数据大小_KB"]["原始数据大小"] == ((size * 100 + 512) // 1024) / 100
        assert report["耗时_ms"]["Protobuf解码"] is not None
        assert report["耗时_ms"]["MJAI转换"] is not None
        reports.append(report)
    assert reports[0]["状态"]["牌谱获取方式"] == "雀魂下载"
    assert reports[0]["状态"]["是否命中本地记录"] is False
    assert reports[0]["耗时_ms"]["雀魂抓取"] is not None
    assert reports[1]["状态"]["牌谱获取方式"] == "本地直接读取"
    assert reports[1]["状态"]["是否命中本地记录"] is True
    assert reports[1]["耗时_ms"]["雀魂抓取"] is None
    fetch.assert_awaited_once_with(U1)
    assert (tmp_path / f"{U1}.pb").is_file()


@pytest.mark.anyio
async def test_same_uuid_concurrent_seats_download_once(monkeypatch, isolated_app, proto):
    async def download(game_uuid):
        await asyncio.sleep(0.05)
        return raw_record(proto, game_uuid)
    fetch = AsyncMock(side_effect=download)
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    converter = Mock(wraps=app.to_mjai)
    monkeypatch.setattr(app, "to_mjai", converter)
    results = await asyncio.gather(*(app.resolve_game_data(paipu=f"{U1}_{seat}") for seat in range(4)))
    assert [result[3] for result in results] == [0, 1, 2, 3]
    fetch.assert_awaited_once_with(U1)
    assert converter.call_count == 4


@pytest.mark.anyio
async def test_encoded_small_account_id_is_not_treated_as_seat(monkeypatch, isolated_app, tmp_path):
    from server.majsoul.client import decode_account_id

    encoded = ((7 + 1117113) ^ 86216345) + 1358437
    assert decode_account_id(encoded) == 1
    legacy = {"head": {"uuid": U1, "accounts": [{"seat": 3, "account_id": 1}]}, "actions": []}
    (tmp_path / f"{U1}.json").write_text(json.dumps(legacy), encoding="utf8")
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    assert (await app.resolve_game_data(paipu=f"{U1}_a{encoded}"))[3] == 3
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_new_client_reads_saved_record_without_login(monkeypatch, isolated_app, proto, tmp_path):
    record_store.save_record(tmp_path, U1, raw_record(proto, U1))
    fetch = AsyncMock(side_effect=AssertionError("must not download"))
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    monkeypatch.setattr(app, "ms_client", MajsoulClient(PROTOCOL_DIR))
    game_id, events, meta, seat = await app.resolve_game_data(paipu=U1, target_actor=3)
    assert game_id == U1 and meta["uuid"] == U1 and seat == 3 and events
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_symlinked_lock_cannot_write_outside_records(monkeypatch, isolated_app, tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    outside = tmp_path.parent / "outside-lock-target"
    outside.write_bytes(b"")
    try:
        (lock_dir / f"{U1}.lock").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    with pytest.raises(HTTPException, match="符号链接"):
        await app.get_or_download_record(U1)
    assert outside.read_bytes() == b""
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_corrupt_record_is_preserved_and_never_downloaded(monkeypatch, isolated_app, proto, tmp_path):
    record_store.save_record(tmp_path, U1, raw_record(proto, U1))
    path = tmp_path / f"{U1}.pb"
    damaged = bytearray(path.read_bytes())
    damaged[-1] ^= 1
    path.write_bytes(damaged)
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    with pytest.raises(HTTPException, match="损坏"):
        await app.resolve_game_data(paipu=U1)
    assert path.read_bytes() == damaged
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_invalid_protobuf_is_not_replaced_or_redownloaded(monkeypatch, isolated_app, tmp_path):
    record_store.save_record(tmp_path, U1, RawRecord(VERSION, b"bad protobuf"))
    path = tmp_path / f"{U1}.pb"
    before = path.read_bytes()
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    with pytest.raises(HTTPException, match="无法解码"):
        await app.resolve_game_data(paipu=U1)
    assert path.read_bytes() == before
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_failure_does_not_mark_record_downloaded(monkeypatch, isolated_app, tmp_path):
    fetch = AsyncMock(side_effect=HTTPException(status_code=503, detail="download failed"))
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    with pytest.raises(HTTPException, match="download failed"):
        await app.resolve_game_data(paipu=U1)
    assert not (tmp_path / f"{U1}.pb").exists()
    fetch.assert_awaited_once()


@pytest.mark.anyio
async def test_write_failure_does_not_mark_success(monkeypatch, isolated_app, proto, tmp_path):
    fetch = AsyncMock(return_value=raw_record(proto, U1))
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(record_store.os, "replace", fail)
    with pytest.raises(HTTPException, match="保存失败"):
        await app.resolve_game_data(paipu=U1)
    assert not (tmp_path / f"{U1}.pb").exists()
    assert not list(tmp_path.glob("*.tmp"))
    fetch.assert_awaited_once()


@pytest.mark.anyio
async def test_legacy_json_is_used_without_fetch(monkeypatch, isolated_app, tmp_path):
    legacy = {"head": {"uuid": U1, "accounts": [{"seat": 1, "account_id": 22}]}, "actions": []}
    (tmp_path / f"{U1}.json").write_text(json.dumps(legacy), encoding="utf8")
    fetch = AsyncMock()
    monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
    _, _, _, seat = await app.resolve_game_data(paipu=f"{U1}_22")
    assert seat == 1
    fetch.assert_not_awaited()
    assert not (tmp_path / f"{U1}.pb").exists()


@pytest.mark.anyio
async def test_different_uuid_storage_downloads_overlap(monkeypatch, isolated_app, proto, tmp_path):
    started = set()
    both_started = asyncio.Event()
    async def fetch(game_uuid):
        started.add(game_uuid)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 2)
        return raw_record(proto, game_uuid)
    download = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(app, "fetch_record_with_failover", download)
    first, second = await asyncio.gather(app.get_or_download_record(U1), app.get_or_download_record(U2))
    assert first == raw_record(proto, U1) and second == raw_record(proto, U2)
    assert download.await_count == 2
    assert (tmp_path / f"{U1}.pb").is_file() and (tmp_path / f"{U2}.pb").is_file()


@pytest.mark.anyio
async def test_different_uuids_share_one_session_and_download_in_parallel(monkeypatch):
    from server.account_pool import fetch_record_with_failover, is_client_logged_in_as

    account = {"account": "a", "account_id": 11}
    client = SimpleNamespace(logged_in=False, _ws=None, account_id=11, current_account=None,
                             access_token=None, nickname="A", close=AsyncMock())
    started = set()
    both_started = asyncio.Event()
    async def fetch(game_uuid):
        started.add(game_uuid)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 2)
        return game_uuid
    async def login(_):
        client.logged_in = True
        client._ws = object()
        client.current_account = "a"
    client.fetch_record = AsyncMock(side_effect=fetch)
    login_mock = AsyncMock(side_effect=login)
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: [account])
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda acc: is_client_logged_in_as(acc, client))
    monkeypatch.setattr(app, "login_with_account_spec", login_mock)
    gate = SessionLock()
    assert await asyncio.gather(fetch_record_with_failover(U1, client, gate),
                                fetch_record_with_failover(U2, client, gate)) == [U1, U2]
    login_mock.assert_awaited_once()
    assert client.fetch_record.await_count == 2


@pytest.mark.anyio
async def test_cancelled_waiter_does_not_cancel_download(monkeypatch, isolated_app, proto, tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    async def fetch(game_uuid):
        started.set()
        await release.wait()
        return raw_record(proto, game_uuid)
    download = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(app, "fetch_record_with_failover", download)
    first = asyncio.create_task(app.get_or_download_record(U1))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(app.get_or_download_record(U1))
    release.set()
    await second
    assert (tmp_path / f"{U1}.pb").exists()
    download.assert_awaited_once()


def test_process_restart_reads_existing_protobuf(proto, tmp_path):
    import os
    root = tmp_path / "persisted"
    record_store.save_record(root / "records", U1, raw_record(proto, U1))
    env = os.environ.copy()
    env["MAJSOUL_CACHE_ROOT"] = str(root)
    code = '''
import asyncio, sys
from server import app
async def forbidden(*args): raise AssertionError("unexpected download")
app.fetch_record_with_failover = forbidden
async def main():
    game_id, events, meta, seat = await app.resolve_game_data(paipu=sys.argv[1], target_actor=2)
    assert game_id == sys.argv[1] and meta["uuid"] == game_id and seat == 2 and events
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, "-c", code, U1], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_cross_process_requests_download_once(tmp_path):
    root = tmp_path / "records"
    root.mkdir()
    gate = tmp_path / "start"
    log = tmp_path / "fetches"
    code = '''
import asyncio, pathlib, sys
from server.record_store import RawRecord, get_or_download_record
root, gate, log, uuid = map(pathlib.Path, sys.argv[1:])
async def fetch(key):
    with log.open("a", encoding="utf8") as f: f.write("download\\n")
    await asyncio.sleep(0.4)
    return RawRecord("v1", b"payload")
async def main():
    while not gate.exists(): await asyncio.sleep(0.01)
    result = await get_or_download_record(str(uuid), root, fetch)
    assert result.response == b"payload"
asyncio.run(main())
'''
    args = [sys.executable, "-c", code, str(root), str(gate), str(log), U1]
    processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    gate.touch()
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, (stdout, stderr)
    assert log.read_text(encoding="utf8").splitlines() == ["download"]
    assert record_store.read_record(root, U1) == RawRecord("v1", b"payload")


@pytest.mark.anyio
async def test_temporary_live_session_is_reused_with_configured_accounts(monkeypatch):
    from server.account_pool import fetch_record_with_failover, is_client_logged_in_as

    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="temporary", access_token="token", nickname="A",
                             close=AsyncMock(), fetch_record=AsyncMock(return_value="raw"))
    login = AsyncMock()
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: [{"account": "other", "account_id": 22}])
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda acc: is_client_logged_in_as(acc, client))
    monkeypatch.setattr(app, "login_with_account_spec", login)
    assert await fetch_record_with_failover(U1, client, SessionLock()) == "raw"
    client.fetch_record.assert_awaited_once_with(U1)
    client.close.assert_not_awaited()
    login.assert_not_awaited()


@pytest.mark.anyio
async def test_repeated_explicit_login_reuses_same_live_token(monkeypatch):
    client = SimpleNamespace(logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
                             current_account="a", access_token="same-token", nickname="A",
                             close=AsyncMock(), connect=AsyncMock(), login_token=AsyncMock())
    monkeypatch.setattr(app, "ms_client", client)
    monkeypatch.setattr(app, "_ms_client_lock", SessionLock())
    monkeypatch.setattr(app, "load_config", lambda: {})
    monkeypatch.setattr(app, "_build_api_status", lambda **kwargs: {"ok": True})
    assert await app.api_login(app.LoginReq(access_token="same-token", remember=False)) == {"ok": True}
    client.close.assert_not_awaited()
    client.connect.assert_not_awaited()
    client.login_token.assert_not_awaited()


@pytest.mark.anyio
async def test_websocket_multiplexes_out_of_order_responses(proto, tmp_path):
    client = MajsoulClient(tmp_path)
    client.proto = proto
    client.protocol_version = VERSION

    class Socket:
        def __init__(self):
            self.sent = []
            self.incoming = asyncio.Queue()
        async def send(self, packet):
            self.sent.append(packet)
            if len(self.sent) == 2:
                for packet in reversed(self.sent):
                    _, data = proto.unwrap(packet[3:])
                    request = proto.decode("ReqGameRecord", data)
                    raw = raw_record(proto, request.game_uuid).response
                    await self.incoming.put(bytes([3]) + packet[1:3] + proto.wrap(".lq.Lobby.fetchGameRecord", raw))
        def __aiter__(self): return self
        async def __anext__(self): return await self.incoming.get()
        async def close(self): pass

    socket = Socket()
    client._ws = socket
    client._reader = asyncio.create_task(client._read_loop())
    try:
        first, second = await asyncio.wait_for(asyncio.gather(client.fetch_record(U1), client.fetch_record(U2)), 3)
        assert proto.decode("ResGameRecord", first.response).head.uuid == U1
        assert proto.decode("ResGameRecord", second.response).head.uuid == U2
        assert not client._pending
    finally:
        await client.close()


@pytest.mark.anyio
async def test_failover_waits_for_other_session_requests(monkeypatch):
    from server.account_pool import fetch_record_with_failover, is_client_logged_in_as

    accounts = [{"account": "a", "account_id": 11}, {"account": "b", "account_id": 22}]
    client = SimpleNamespace(logged_in=True, _ws=object(), account_id=11, current_account="a",
                             access_token=None, nickname="A")
    first_started, release_first, failure_seen = asyncio.Event(), asyncio.Event(), asyncio.Event()
    switched = []

    async def fetch(game_uuid):
        if game_uuid == U1:
            first_started.set()
            await release_first.wait()
            assert client.account_id == 11
            return "first"
        if client.account_id == 11:
            failure_seen.set()
            raise MajsoulError("rate limit", code=540)
        return "second"

    async def close():
        switched.append("close")
        client.logged_in = False
        client._ws = None

    async def login(account):
        switched.append(account["account"])
        client.logged_in = True
        client._ws = object()
        client.account_id = account["account_id"]
        client.current_account = account["account"]

    client.fetch_record, client.close = fetch, close
    monkeypatch.setattr(app, "get_all_configured_accounts", lambda: accounts)
    monkeypatch.setattr(app, "is_client_logged_in_as", lambda acc: is_client_logged_in_as(acc, client))
    monkeypatch.setattr(app, "login_with_account_spec", login)
    monkeypatch.setattr("server.account_pool._rate_limited_until", {})
    gate = SessionLock()
    first = asyncio.create_task(fetch_record_with_failover(U1, client, gate))
    await first_started.wait()
    second = asyncio.create_task(fetch_record_with_failover(U2, client, gate))
    await failure_seen.wait()
    await asyncio.sleep(0)
    assert not switched
    release_first.set()
    assert await asyncio.gather(first, second) == ["first", "second"]
    assert switched == ["close", "b"]


@pytest.mark.anyio
async def test_external_detail_preserves_response_and_body(monkeypatch, proto, tmp_path):
    client = MajsoulClient(tmp_path)
    client.proto = proto
    client.protocol_version = VERSION
    source = raw_record(proto, U1)
    response = proto.decode("ResGameRecord", source.response)
    detail = response.data
    response.data = b""
    response.data_url = "https://example.test/record"
    original = response.SerializeToString()
    client.call = AsyncMock(return_value=(response, original))

    class HttpClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs):
            return SimpleNamespace(content=detail, raise_for_status=lambda: None)

    monkeypatch.setattr("server.majsoul.client.httpx.AsyncClient", HttpClient)
    result = await client.fetch_record(U1)
    assert result.response == original and result.detail == detail
    assert client.decode_record(result, U1)["head"]["uuid"] == U1


@pytest.mark.anyio
async def test_fetch_preserves_original_api_payload(proto, tmp_path):
    client = MajsoulClient(tmp_path)
    client.proto = proto
    client.protocol_version = VERSION
    source = raw_record(proto, U1)
    response = proto.decode("ResGameRecord", source.response)
    client.call = AsyncMock(return_value=(response, source.response))
    assert await client.fetch_record(U1) == source
    assert client.call.await_args.kwargs["return_payload"] is True
