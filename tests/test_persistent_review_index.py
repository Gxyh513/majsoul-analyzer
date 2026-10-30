import asyncio
from unittest.mock import AsyncMock

import pytest

import server.app as app


UUID = "241026-11111111-2222-3333-4444-555555555555"


@pytest.fixture
def index_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(app, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path / "records")
    app.REVIEWS_DIR.mkdir()
    monkeypatch.setattr(app, "resolve_engine_cache_info", lambda engine: ("mortal", "schema_weight"))
    monkeypatch.setattr(app, "_valid_cached_review", lambda key, weight: {"key": key, "weight": weight, "game_id": UUID, "target_actor": 1})


def test_index_hit_without_record_and_isolates_seat_and_version(index_state, monkeypatch):
    key = "review-" + "a" * 64
    app._save_review_index(UUID, {"account_ids": [11, 22]}, 1, "mortal", 0.0, "schema_weight", key)
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0)["key"] == key
    assert app._lookup_indexed_review(UUID + "_22", None, "mortal", 0.0) is not None
    assert app._lookup_indexed_review(UUID, 0, "mortal", 0.0) is None
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.5) is None
    monkeypatch.setattr(app, "resolve_engine_cache_info", lambda engine: ("mortal", "schema_other"))
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0) is None
    assert "token" not in next(app.METADATA_DIR.iterdir()).read_text()


def test_index_remains_usable_when_raw_record_is_corrupt(index_state):
    key = "review-" + "a" * 64
    app._save_review_index(UUID, {"account_ids": [11, 22]}, 1, "mortal", 0.0, "schema_weight", key)
    app.RECORDS_DIR.mkdir()
    (app.RECORDS_DIR / f"{UUID}.json").write_text("{broken", encoding="utf8")
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0)["key"] == key


def test_index_rejects_wrong_review_identity(index_state, monkeypatch):
    key = "review-" + "a" * 64
    monkeypatch.setattr(app, "_valid_cached_review", lambda *args: {"game_id": UUID, "target_actor": 0})
    app._save_review_index(UUID, {}, 0, "mortal", 0.0, "schema_weight", key)
    assert app._lookup_indexed_review(UUID, 0, "mortal", 0.0) is not None
    monkeypatch.setattr(app, "_valid_cached_review", lambda *args: {"game_id": UUID, "target_actor": 1})
    assert app._lookup_indexed_review(UUID, 0, "mortal", 0.0) is None


def test_verified_index_skips_record_conversion_and_upgrades_legacy(index_state, monkeypatch):
    import json
    from unittest.mock import Mock
    from server.account_pool import _save_record

    record = {"head": {"uuid": UUID, "accounts": [{"seat": 1, "account_id": 22}]}, "actions": []}
    _save_record(app.RECORDS_DIR, UUID, record)
    events, meta = app.to_mjai(record), app.game_meta(record)
    key = app.review_cache_key(UUID, events, meta, 1, "mortal", 0.0, "schema_weight")
    app._save_review_index(UUID, meta, 1, "mortal", 0.0, "schema_weight", key)
    reader = Mock(wraps=app._read_cached_game_input)
    monkeypatch.setattr(app, "_read_cached_game_input", reader)
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0)["key"] == key
    reader.assert_not_called()
    path = app._review_index_path(UUID, 1, "mortal", 0.0, "schema_weight")
    index = json.loads(path.read_text(encoding="utf8"))
    index.pop("record_identity")
    index.pop("review_digest")
    path.write_text(json.dumps(index), encoding="utf8")
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0)["key"] == key
    reader.reset_mock()
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0)["key"] == key
    reader.assert_not_called()
    monkeypatch.setattr(app, "_valid_cached_review", lambda *args: {"game_id": UUID, "target_actor": 1})
    assert app._lookup_indexed_review(UUID, 1, "mortal", 0.0) is None


def test_broken_index_falls_back(index_state):
    path = app._review_index_path(UUID, 0, "mortal", 0.0, "schema_weight")
    path.parent.mkdir()
    path.write_text("broken", encoding="utf8")
    assert app._lookup_indexed_review(UUID, 0, "mortal", 0.0) is None


def test_atomic_index_failure_preserves_previous(index_state, monkeypatch):
    monkeypatch.setattr(app, "_valid_cached_review", lambda *args: {"game_id": UUID, "target_actor": 0})
    app._save_review_index(UUID, {}, 0, "mortal", 0.0, "schema_weight", "review-" + "a" * 64)
    path = next(app.METADATA_DIR.iterdir())
    before = path.read_text()
    monkeypatch.setattr(app.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("failure")))
    app._save_review_index(UUID, {}, 0, "mortal", 0.0, "schema_weight", "review-" + "b" * 64)
    assert path.read_text() == before
    assert not list(app.METADATA_DIR.glob("*.tmp"))


@pytest.mark.anyio
async def test_stream_review_hit_does_not_resolve_or_login(index_state, monkeypatch):
    key = "review-" + "a" * 64
    app._save_review_index(UUID, {"account_ids": [11, 22]}, 1, "mortal", 0.0, "schema_weight", key)
    resolve = AsyncMock(side_effect=AssertionError("must not fetch"))
    monkeypatch.setattr(app, "resolve_game_data", resolve)
    response = await app.api_analyze_stream(app.AnalyzeStreamReq(paipu=UUID, target_actor=1, temperature=0.0))
    chunks = [chunk async for chunk in response.body_iterator]
    assert any('"type": "complete"' in chunk for chunk in chunks)
    resolve.assert_not_called()


@pytest.mark.anyio
async def test_changed_disk_record_invalidates_index_and_memory(monkeypatch, tmp_path):
    import copy
    from unittest.mock import Mock
    from server.account_pool import _save_record
    from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA

    sha = "a" * 64
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path / "records")
    monkeypatch.setattr(app, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "metadata")
    app.REVIEWS_DIR.mkdir()
    monkeypatch.setattr(app, "_MEMORY_LOGS", {})
    monkeypatch.setattr(app, "_MEMORY_LOG_FILES", {})
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_flights", {})
    monkeypatch.setattr(app, "_review_cache_readers", {})
    monkeypatch.setattr(app, "resolve_engine_cache_info", lambda _: (MODEL_ID, f"{REVIEW_SCHEMA}_{sha}"))
    monkeypatch.setattr(app, "fetch_record_with_failover", AsyncMock(side_effect=AssertionError("unexpected network fetch")))
    inference = Mock(side_effect=lambda events, seat, manager, **kwargs: {
        "target_actor": seat, "meta": kwargs["meta"],
        "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
                   "weight_sha256": sha, "version": 4, "steps": 582500,
                   "q_source": "neural_network", "review_schema": REVIEW_SCHEMA},
    })
    monkeypatch.setattr(app, "review_game", inference)
    old_record = {"head": {"uuid": UUID, "accounts": [{"seat": 0, "account_id": 11, "nickname": "Old"}]}, "actions": []}
    _save_record(app.RECORDS_DIR, UUID, old_record)
    game_id, events, meta, seat = await app.resolve_game_data(paipu=UUID, target_actor=0)
    assert app._MEMORY_LOG_FILES[UUID][0] is app._MEMORY_LOGS[UUID]
    disk_reader = Mock(wraps=app._read_cached_game_input)
    monkeypatch.setattr(app, "_read_cached_game_input", disk_reader)
    assert (await app.resolve_game_data(paipu=UUID, target_actor=1))[2]["names"][0] == "Old"
    calls_after_validation = disk_reader.call_count
    assert (await app.resolve_game_data(paipu=UUID, target_actor=1))[2]["names"][0] == "Old"
    assert disk_reader.call_count == calls_after_validation
    old_result = await app._run_review_with_cache(game_id, events, meta, seat, MODEL_ID, 0.0)
    assert old_result["meta"]["names"][0] == "Old"
    assert app._lookup_indexed_review(UUID, 0, MODEL_ID, 0.0) == old_result
    assert UUID in app._MEMORY_LOGS

    new_record = copy.deepcopy(old_record)
    new_record["head"]["accounts"][0]["nickname"] = "New"
    _save_record(app.RECORDS_DIR, UUID, new_record)
    assert app._lookup_indexed_review(UUID, 1, MODEL_ID, 0.0) is None
    assert UUID in app._MEMORY_LOGS
    _, _, updated_meta, updated_seat = await app.resolve_game_data(paipu=UUID, target_actor=1)
    assert updated_seat == 1 and updated_meta["names"][0] == "New"
    assert app._lookup_indexed_review(UUID, 0, MODEL_ID, 0.0) is None
    game_id, events, meta, seat = await app.resolve_game_data(paipu=UUID, target_actor=0)
    new_result = await app._run_review_with_cache(game_id, events, meta, seat, MODEL_ID, 0.0)
    assert new_result["meta"]["names"][0] == "New"
    assert inference.call_count == 2
    assert app._lookup_indexed_review(UUID, 0, MODEL_ID, 0.0) == new_result
    app.fetch_record_with_failover.assert_not_called()


@pytest.mark.anyio
async def test_memory_identity_does_not_bind_replaced_file_to_old_data(monkeypatch, tmp_path):
    import copy
    import server.account_pool as pool

    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "_MEMORY_LOGS", {})
    monkeypatch.setattr(app, "_MEMORY_LOG_FILES", {})
    old = {"head": {"uuid": UUID, "accounts": [{"seat": 0, "account_id": 11, "nickname": "Old"}]}, "actions": []}
    new = copy.deepcopy(old)
    new["head"]["accounts"][0]["nickname"] = "New"
    pool._save_record(tmp_path, UUID, old)
    original_load = pool.load_cached_record
    replaced = []

    def replace_after_read(directory, game_id, **kwargs):
        result = original_load(directory, game_id, **kwargs)
        if not replaced:
            pool._save_record(directory, game_id, new)
            replaced.append(True)
        return result

    monkeypatch.setattr(pool, "load_cached_record", replace_after_read)
    assert (await app.resolve_game_data(paipu=UUID, target_actor=0))[2]["names"][0] == "Old"
    assert app._MEMORY_LOG_FILES[UUID][1] is None
    assert (await app.resolve_game_data(paipu=UUID, target_actor=0))[2]["names"][0] == "New"


@pytest.mark.anyio
async def test_memory_entry_evicted_during_file_stat_is_safe(monkeypatch, tmp_path):
    import server.account_pool as pool

    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "_MEMORY_LOGS", {})
    monkeypatch.setattr(app, "_MEMORY_LOG_FILES", {})
    record = {"head": {"uuid": UUID, "accounts": [{"seat": 0, "account_id": 11, "nickname": "Old"}]}, "actions": []}
    pool._save_record(tmp_path, UUID, record)
    real_to_thread = asyncio.to_thread
    calls = []

    async def evict_during_second_stat(function, *args, **kwargs):
        if function is app._record_file_identity:
            calls.append(True)
            if len(calls) == 2:
                for number in range(app.MAX_MEMORY_LOGS):
                    app.set_memory_log(f"other-{number}", [], {})
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(app.asyncio, "to_thread", evict_during_second_stat)
    result = await app.resolve_game_data(paipu=UUID, target_actor=0)
    assert result[0] == UUID and result[2]["names"][0] == "Old"
    assert UUID not in app._MEMORY_LOGS and UUID not in app._MEMORY_LOG_FILES


@pytest.mark.anyio
async def test_disk_record_is_converted_once(monkeypatch, tmp_path):
    from unittest.mock import Mock
    import server.account_pool as pool
    import server.majsoul.convert as convert

    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(app, "_MEMORY_LOGS", {})
    monkeypatch.setattr(app, "_MEMORY_LOG_FILES", {})
    monkeypatch.setattr(app, "_game_resolve_flights", {})
    record = {"head": {"uuid": UUID, "accounts": []}, "actions": []}
    pool._save_record(tmp_path, UUID, record)
    converter = Mock(wraps=convert.to_mjai)
    monkeypatch.setattr(convert, "to_mjai", converter)
    monkeypatch.setattr(app, "to_mjai", converter)
    await app.resolve_game_data(paipu=UUID, target_actor=0)
    converter.assert_called_once()


@pytest.mark.anyio
async def test_concurrent_different_seats_share_game_resolution(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def resolve(paipu, content, target_actor):
        calls.append(paipu)
        started.set()
        await release.wait()
        return UUID, [{"type": "start_game"}], {"account_ids": [11, 22, 33, 44]}, 0

    monkeypatch.setattr(app, "_game_resolve_flights", {})
    monkeypatch.setattr(app, "_resolve_game_data", resolve)
    first = asyncio.create_task(app.resolve_game_data(paipu=UUID, target_actor=0))
    await started.wait()
    second = asyncio.create_task(app.resolve_game_data(paipu=UUID + "_22"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert (await second)[3] == 1
    assert calls == [UUID]


@pytest.mark.anyio
async def test_startup_does_not_log_in(monkeypatch):
    login = AsyncMock()
    monkeypatch.setattr(app, "ensure_ms_logged_in", login)
    async with app.lifespan(app.app):
        await asyncio.sleep(0)
    login.assert_not_called()
