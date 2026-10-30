"""Exercise persistent record, review and index caches across interpreter restarts."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parent.parent
UUID = "241026-11111111-2222-3333-4444-555555555555"
OTHER_UUID = "241026-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SEAT = 1
TEMPERATURE = 0.0
WEIGHT_SHA = "a" * 64


# Include real actions and nested data so a partial or incorrect record cannot pass.
HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"]
RECORD = {
    "head": {
        "uuid": UUID,
        "start_time": 1729900000,
        "end_time": 1729903600,
        "accounts": [
            {"seat": 0, "account_id": 11, "nickname": "东家"},
            {"seat": 1, "account_id": 22, "nickname": "南家"},
            {"seat": 2, "account_id": 33, "nickname": "西家"},
            {"seat": 3, "account_id": 44, "nickname": "北家"},
        ],
        "config": {"mode": {"mode": 2}, "category": 1},
        "result": {"players": [{"seat": 1, "part_point_1": 26000, "total_point": 1000}]},
    },
    "actions": [
        {"name": "RecordNewRound", "data": {
            **{f"tiles{i}": HAND[:] for i in range(4)},
            "chang": 0, "ju": 0, "scores": [25000] * 4, "dora": "1m",
        }},
        {"name": "RecordDealTile", "data": {"seat": 1, "tile": "5p"}},
        {"name": "RecordDiscardTile", "data": {"seat": 1, "tile": "5p", "moqie": True}},
        {"name": "RecordNoTile", "data": {"scores": [{"delta_scores": [0, 0, 0, 0]}]}},
    ],
    "fetched_at": 1729904000,
    "extra": {"nested": ["原始数据", {"value": 42}]},
}


def review_result(events, meta, seat=SEAT):
    from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA

    return {
        "target_actor": seat,
        "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
                   "weight_sha256": WEIGHT_SHA, "version": 4, "steps": 582500,
                   "q_source": "neural_network", "review_schema": REVIEW_SCHEMA},
        "summary": {"total_reviewed": 1, "match_rate": 100.0},
        "kyokus": [{"steps": [{"event": events[2]}], "notes": ["完整检讨"]}],
        "decisions": [{"actor": seat, "candidates": [{"tile": "5p", "q": 0.75}]}],
        "fixture_input": {"events": events, "meta": meta, "temperature": TEMPERATURE},
    }


def configure_engine(app):
    from server.engine.model_policy import normalize_model_id

    model = SimpleNamespace(weight_sha256=WEIGHT_SHA)
    app.engine_mgr = SimpleNamespace(
        get_mortal=lambda engine: (model, normalize_model_id(engine), None)
    )
    # Never let a developer's credentials or selected account affect seat choice.
    app.load_config = lambda: {}
    return model


def expected_review(app):
    result = review_result(app.to_mjai(RECORD), app.game_meta(RECORD))
    result["game_id"] = UUID
    return result


def fake_review(app):
    def review(events, seat, engine_mgr, **kwargs):
        assert events == app.to_mjai(RECORD)
        assert kwargs["meta"] == app.game_meta(RECORD)
        assert seat == SEAT and kwargs["temperature"] == TEMPERATURE
        assert kwargs["force_engine"] == "akagi"
        return review_result(events, kwargs["meta"], seat)
    return Mock(side_effect=review)


def forbid_online_work(app):
    """Use explicit failure mocks, including entry points below the failover layer."""
    def fail(name):
        return AssertionError(f"unexpected {name} in an offline cache reader")

    client = SimpleNamespace(
        account_id=None, nickname=None, access_token=None, logged_in=False, _ws=None,
        login_password=AsyncMock(side_effect=fail("password login")),
        login_token=AsyncMock(side_effect=fail("token login")),
        fetch_record=AsyncMock(side_effect=fail("network record fetch")),
    )
    app.ms_client = client
    app.ensure_ms_logged_in = AsyncMock(side_effect=fail("login"))
    app.login_with_account_spec = AsyncMock(side_effect=fail("account login"))
    app.fetch_record_with_failover = AsyncMock(side_effect=fail("network failover fetch"))
    return [app.ensure_ms_logged_in, app.login_with_account_spec,
            app.fetch_record_with_failover, client.login_password, client.login_token,
            client.fetch_record]


def assert_not_called(mocks):
    for mock in mocks:
        mock.assert_not_called()


async def analyze_messages(app, paipu=UUID, seat=SEAT):
    response = await app.api_analyze_stream(app.AnalyzeStreamReq(
        paipu=paipu, target_actor=seat, engine="akagi", temperature=TEMPERATURE,
    ))
    messages = []
    async for chunk in response.body_iterator:
        assert chunk.startswith("data: ")
        messages.append(json.loads(chunk.removeprefix("data: ").strip()))
    return messages


async def analyze(app, paipu=UUID, seat=SEAT):
    messages = await analyze_messages(app, paipu, seat)
    assert not [message for message in messages if message["type"] == "error"], messages
    complete = [message["result"] for message in messages if message["type"] == "complete"]
    assert len(complete) == 1, messages
    return complete[0]


def index_and_review_paths(app):
    from server.engine.model_policy import MODEL_ID, REVIEW_SCHEMA

    signature = f"{REVIEW_SCHEMA}_{WEIGHT_SHA}"
    index_path = app._review_index_path(UUID, SEAT, MODEL_ID, TEMPERATURE, signature)
    index = json.loads(index_path.read_text(encoding="utf8"))
    expected_index = {
        "game_id": UUID, "seat": SEAT, "engine": MODEL_ID, "temperature": TEMPERATURE,
        "weight_sig": signature, "cache_key": index["cache_key"],
        "account_ids": [11, 22, 33, 44],
    }
    assert index == expected_index
    assert index["cache_key"] == app.review_cache_key(
        UUID, app.to_mjai(RECORD), app.game_meta(RECORD), SEAT, MODEL_ID, TEMPERATURE, signature,
    )
    return index_path, app.REVIEWS_DIR / f"{index['cache_key']}.json", index


async def write_cache():
    """Process A fetches and reviews offline through the real production paths."""
    import server.app as app
    import server.account_pool as pool

    configure_engine(app)
    client = SimpleNamespace(
        logged_in=False, _ws=None, account_id=11, nickname="测试", access_token=None,
        fetch_record=AsyncMock(return_value=copy.deepcopy(RECORD)), close=AsyncMock(),
    )
    app.ms_client = client
    app.get_all_configured_accounts = lambda: [{"account": "offline-fixture", "account_id": 11}]
    app.login_with_account_spec = AsyncMock()
    app.review_game = fake_review(app)

    assert not app._MEMORY_LOGS
    assert await analyze(app) == expected_review(app)
    client.fetch_record.assert_awaited_once_with(UUID)
    app.login_with_account_spec.assert_awaited_once()
    app.review_game.assert_called_once()
    assert pool.load_cached_record(app.RECORDS_DIR, UUID) == RECORD
    assert json.loads((app.RECORDS_DIR / f"{UUID}.json").read_text(encoding="utf8")) == RECORD
    index_path, review_path, index = index_and_review_paths(app)
    assert index_path.is_file() and review_path.is_file()
    assert json.loads(review_path.read_text(encoding="utf8")) == expected_review(app)
    assert app._valid_cached_review(index["cache_key"], WEIGHT_SHA) == expected_review(app)
    assert not list(app.RECORDS_DIR.glob("*.tmp"))
    assert not list(app.REVIEWS_DIR.glob("*.tmp"))
    assert not list(app.METADATA_DIR.glob("*.tmp"))


async def read_cache():
    """Process B can only succeed through on-disk hits, never login/fetch/inference."""
    import server.app as app
    import server.account_pool as pool
    from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA

    model = configure_engine(app)
    forbidden = forbid_online_work(app)
    app.review_game = Mock(side_effect=AssertionError("unexpected Mortal inference"))
    app._start_review_job = AsyncMock(side_effect=AssertionError("unexpected review job"))

    root = Path(os.environ["MAJSOUL_CACHE_ROOT"])
    assert (app.CACHE_DIR, app.RECORDS_DIR, app.REVIEWS_DIR, app.METADATA_DIR) == (
        root / "cache", root / "records", root / "reviews", root / "metadata",
    )
    assert not app._MEMORY_LOGS
    assert pool.load_cached_record(app.RECORDS_DIR, UUID) == RECORD
    with patch.object(pool, "load_cached_record", wraps=pool.load_cached_record) as load:
        game_id, events, meta, seat = await app.resolve_game_data(paipu=f"{UUID}_22")
        load.assert_called_once_with(app.RECORDS_DIR, UUID)
    assert (game_id, seat) == (UUID, SEAT)
    assert events == app.to_mjai(RECORD) and len(events) > 2
    assert meta == app.game_meta(RECORD) and meta["uuid"] == UUID
    assert meta["account_ids"] == [11, 22, 33, 44]

    # A record filed under the requested UUID is not a hit if its head disagrees.
    record_path = app.RECORDS_DIR / f"{UUID}.json"
    wrong_record = copy.deepcopy(RECORD)
    wrong_record["head"]["uuid"] = OTHER_UUID
    record_path.write_text(json.dumps(wrong_record), encoding="utf8")
    assert pool.load_cached_record(app.RECORDS_DIR, UUID) is None
    app._MEMORY_LOGS.clear()
    try:
        await app.resolve_game_data(paipu=UUID, target_actor=SEAT)
    except AssertionError as error:
        assert str(error) == "unexpected network failover fetch in an offline cache reader"
    else:
        raise AssertionError("wrong record UUID was treated as a cache hit")
    app.fetch_record_with_failover.assert_awaited_once_with(UUID)
    app.fetch_record_with_failover.reset_mock()
    record_path.write_text(json.dumps(RECORD, ensure_ascii=False), encoding="utf8")

    index_path, review_path, index = index_and_review_paths(app)
    expected = expected_review(app)
    assert json.loads(review_path.read_text(encoding="utf8")) == expected
    assert app._valid_cached_review(index["cache_key"], WEIGHT_SHA) == expected
    assert expected["game_id"] == UUID and expected["target_actor"] == SEAT
    assert expected["engine"]["id"] == MODEL_ID and expected["engine"]["weight_file"] == MODEL_FILE
    assert expected["engine"]["version"] == 4 and expected["engine"]["steps"] == 582500
    assert expected["engine"]["review_schema"] == REVIEW_SCHEMA
    assert expected["engine"]["weight_sha256"] == WEIGHT_SHA
    assert index["weight_sig"] == f"{REVIEW_SCHEMA}_{WEIGHT_SHA}"

    # Even resolving the raw record is forbidden here: the persisted index must hit.
    app._MEMORY_LOGS.clear()
    with patch.object(app, "resolve_game_data", new=AsyncMock(side_effect=AssertionError("index missed"))) as resolve:
        assert await analyze(app, paipu=f"https://game.maj-soul.com/?paipu={UUID}_22", seat=None) == expected
        resolve.assert_not_called()
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) == expected
    assert app._lookup_indexed_review(OTHER_UUID, SEAT, MODEL_ID, TEMPERATURE) is None
    assert app._lookup_indexed_review(UUID, 0, MODEL_ID, TEMPERATURE) is None
    assert app._lookup_indexed_review(f"{UUID}_11", None, MODEL_ID, TEMPERATURE) is None
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, 0.5) is None
    assert app._lookup_indexed_review(UUID, SEAT, "builtin", TEMPERATURE) is None
    model.weight_sha256 = "b" * 64
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None
    model.weight_sha256 = WEIGHT_SHA
    with patch.object(app, "REVIEW_SCHEMA", "stale-schema"):
        assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None

    # Reject misplaced/tampered indexes, even when they point to a real review.
    for field, value in (("game_id", OTHER_UUID), ("seat", 0), ("engine", "builtin"),
                         ("temperature", 0.5), ("weight_sig", "stale_" + WEIGHT_SHA),
                         ("cache_key", "../../outside")):
        altered = {**index, field: value}
        index_path.write_text(json.dumps(altered), encoding="utf8")
        assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None, field
    index_path.write_text(json.dumps(index), encoding="utf8")

    # A valid index is insufficient if the persisted review's identity or provenance is stale.
    for field, value in (("game_id", OTHER_UUID), ("target_actor", 0)):
        altered = {**expected, field: value}
        review_path.write_text(json.dumps(altered), encoding="utf8")
        assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None, field

        # Neither the streaming endpoint nor the non-streaming cache helper may
        # return this wrong identity after the index rejects it. On a miss, the
        # B process's review-job mock fails explicitly instead of running Mortal.
        app._MEMORY_LOGS.clear()
        with patch.object(pool, "load_cached_record", wraps=pool.load_cached_record) as load:
            messages = await analyze_messages(app)
            load.assert_called_once_with(app.RECORDS_DIR, UUID)
        assert not [message for message in messages if message["type"] == "complete"], (field, messages)
        app._start_review_job.assert_awaited_once()
        app._start_review_job.reset_mock()
        try:
            await app._run_review_with_cache(UUID, events, meta, SEAT, MODEL_ID, TEMPERATURE)
        except AssertionError as error:
            assert str(error) == "unexpected review job"
        else:
            raise AssertionError(f"non-stream cache returned wrong {field}")
        app._start_review_job.assert_awaited_once()
        app._start_review_job.reset_mock()
    for field, value in (("id", "builtin"), ("type", "builtin"), ("weight_file", "other.pth"),
                         ("weight_sha256", "b" * 64), ("version", 3), ("steps", 1),
                         ("q_source", "heuristic"), ("review_schema", "old")):
        altered = copy.deepcopy(expected)
        altered["engine"][field] = value
        review_path.write_text(json.dumps(altered), encoding="utf8")
        assert app._valid_cached_review(index["cache_key"], WEIGHT_SHA) is None, field
        assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None, field
        if field in ("weight_sha256", "version", "review_schema"):
            app._MEMORY_LOGS.clear()
            with patch.object(pool, "load_cached_record", wraps=pool.load_cached_record) as load:
                messages = await analyze_messages(app)
                load.assert_called_once_with(app.RECORDS_DIR, UUID)
            assert not [message for message in messages if message["type"] == "complete"], (field, messages)
            app._start_review_job.assert_awaited_once()
            app._start_review_job.reset_mock()
    review_path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf8")

    # Corrupt index falls back to the real disk record + content-bound review cache.
    index_path.write_text("{not-json", encoding="utf8")
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None
    app._MEMORY_LOGS.clear()
    with patch.object(pool, "load_cached_record", wraps=pool.load_cached_record) as load:
        assert await analyze(app) == expected
        load.assert_called_once_with(app.RECORDS_DIR, UUID)
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) == expected
    assert_not_called(forbidden + [app.review_game, app._start_review_job])


async def recover_cache():
    """A separate process shows a corrupt index also permits a fresh offline review."""
    import server.app as app
    import server.account_pool as pool
    from server.engine.model_policy import MODEL_ID

    configure_engine(app)
    forbidden = forbid_online_work(app)
    app.review_game = fake_review(app)
    index_path, review_path, _ = index_and_review_paths(app)
    index_path.write_text("{not-json", encoding="utf8")
    review_path.unlink()
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) is None
    assert not app._MEMORY_LOGS
    with patch.object(pool, "load_cached_record", wraps=pool.load_cached_record) as load:
        assert await analyze(app) == expected_review(app)
        load.assert_called_once_with(app.RECORDS_DIR, UUID)
    app.review_game.assert_called_once()
    assert_not_called(forbidden)
    assert json.loads(review_path.read_text(encoding="utf8")) == expected_review(app)
    assert app._lookup_indexed_review(UUID, SEAT, MODEL_ID, TEMPERATURE) == expected_review(app)


def run_child(stage, cache_root):
    env = {key: value for key, value in os.environ.items() if not key.startswith("MAJSOUL_")}
    env.pop("PYTHONOPTIMIZE", None)
    env.update({"MAJSOUL_CACHE_ROOT": str(cache_root), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), stage],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf8", timeout=120)
    assert result.returncode == 0, f"{stage} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.splitlines()[-1])


def test_record_review_and_index_persist_across_processes(tmp_path):
    root = tmp_path / "持久 cache"
    writer = run_child("write", root)
    reader = run_child("read", root)
    recovery = run_child("recover", root)
    assert [writer["stage"], reader["stage"], recovery["stage"]] == ["write", "read", "recover"]
    # Exited child PIDs may be reused; each process only needs to differ from
    # the still-running parent. Fresh interpreter state is checked in each child.
    assert all(child["pid"] != os.getpid() for child in (writer, reader, recovery))


if __name__ == "__main__":
    # A direct script starts with tests/ on sys.path rather than the repository root.
    sys.path.insert(0, str(ROOT))
    stage = sys.argv[1]
    asyncio.run({"write": write_cache, "read": read_cache, "recover": recover_cache}[stage]())
    print(json.dumps({"stage": stage, "pid": os.getpid()}))
