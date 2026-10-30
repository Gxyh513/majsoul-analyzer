"""Persist raw records, but re-run reviews across interpreter restarts."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parent.parent
UUID = "241026-11111111-2222-3333-4444-555555555555"
SEAT = 1
TEMPERATURE = 0.0
WEIGHT_SHA = "a" * 64
VERSION = "0.11.252.w"
PROTOCOL_DIR = ROOT / "data" / "cache"
HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"]


def raw_fixture():
    """Build real protobuf bytes so a fresh client must decode the stored record."""
    from server.majsoul.proto import LiqiProto
    from server.record_store import RawRecord

    proto = LiqiProto(json.loads((PROTOCOL_DIR / f"liqi-{VERSION}.json").read_text(encoding="utf8")))
    head = proto.get("RecordGame")()
    head.uuid, head.start_time, head.end_time = UUID, 1729900000, 1729903600
    for seat, name in enumerate(("东家", "南家", "西家", "北家")):
        account = head.accounts.add()
        account.seat, account.account_id, account.nickname = seat, (seat + 1) * 11, name
    round_ = proto.get("RecordNewRound")()
    round_.dora = "1m"
    round_.scores.extend([25000] * 4)
    for seat in range(4):
        getattr(round_, f"tiles{seat}").extend(HAND)
    deal = proto.get("RecordDealTile")()
    deal.seat, deal.tile = SEAT, "5p"
    discard = proto.get("RecordDiscardTile")()
    discard.seat, discard.tile, discard.moqie = SEAT, "5p", True
    detail = proto.get("GameDetailRecords")()
    detail.version = 210715
    for name, message in (("RecordNewRound", round_), ("RecordDealTile", deal), ("RecordDiscardTile", discard)):
        detail.actions.add().result = proto.wrap(f".lq.{name}", message.SerializeToString())
    response = proto.get("ResGameRecord")()
    response.head.CopyFrom(head)
    response.data = proto.wrap(".lq.GameDetailRecords", detail.SerializeToString())
    return RawRecord(VERSION, response.SerializeToString())


def configure(app, stage):
    from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA
    from server.majsoul.client import MajsoulClient

    source = raw_fixture()
    actual_decoder = MajsoulClient(PROTOCOL_DIR)
    record = actual_decoder.decode_record(source, UUID)
    assert record["head"]["uuid"] == UUID and len(record["actions"]) == 3
    app.load_config = lambda: {}
    app.LEGACY_RECORDS_DIRS = ()
    app.engine_mgr = SimpleNamespace(get_mortal=lambda _: (SimpleNamespace(weight_sha256=WEIGHT_SHA), MODEL_ID, None))
    app.review_semaphore = asyncio.Semaphore(1)
    app._review_tasks = set()
    app.models_download_state = {MODEL_ID: {"status": "idle"}}
    decoder = Mock(wraps=MajsoulClient(PROTOCOL_DIR).decode_record)
    client = SimpleNamespace(
        logged_in=True, _ws=SimpleNamespace(closed=False), account_id=11,
        current_account="fixture", nickname="Fixture", access_token=None,
        close=AsyncMock(), decode_record=decoder,
        fetch_record=AsyncMock(return_value=source),
    )
    app.ms_client = client
    app._ms_client_lock = asyncio.Lock()
    app.get_all_configured_accounts = lambda: [{"account": "fixture", "account_id": 11}]
    from server.account_pool import is_client_logged_in_as
    app.is_client_logged_in_as = lambda account: is_client_logged_in_as(account, client)
    app.login_with_account_spec = AsyncMock(side_effect=AssertionError("unexpected login"))
    calls = []

    def review(events, seat, manager, **kwargs):
        assert events == app.to_mjai(record)
        assert kwargs["meta"] == app.game_meta(record)
        assert seat == SEAT and kwargs["temperature"] == TEMPERATURE
        assert kwargs["force_engine"] == MODEL_ID
        calls.append(True)
        return {
            "target_actor": seat,
            "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
                       "weight_sha256": WEIGHT_SHA, "version": 4, "steps": 582500,
                       "q_source": "neural_network", "review_schema": REVIEW_SCHEMA},
            "summary": {"total_reviewed": 1, "match_rate": 100.0},
            "kyokus": [{"steps": [{"event": events[2]}], "notes": ["完整检讨"]}],
            "decisions": [{"actor": seat, "candidates": [{"tile": "5p", "q": 0.75}]}],
            "fixture_input": {"events": events, "meta": kwargs["meta"], "temperature": TEMPERATURE},
            "analysis_marker": f"{stage}-{len(calls)}",
        }

    app.review_game = Mock(side_effect=review)
    return source, record, client, decoder, calls


async def analyze(app):
    response = await app.api_analyze_stream(app.AnalyzeStreamReq(
        paipu=f"https://game.maj-soul.com/?paipu={UUID}_22", engine="akagi", temperature=TEMPERATURE,
    ))
    messages = [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in response.body_iterator]
    assert not [message for message in messages if message["type"] == "error"], messages
    complete = [message for message in messages if message["type"] == "complete"]
    assert len(complete) == 1 and "cache_persisted" not in complete[0]
    return complete[0]["result"]


async def run_stage(stage):
    import server.app as app
    import server.record_store as store

    source, record, client, decoder, calls = configure(app, stage)
    root = Path(os.environ["MAJSOUL_CACHE_ROOT"])
    assert (app.CACHE_DIR, app.RECORDS_DIR) == (root / "cache", root / "records")
    if stage == "read":
        assert store.read_record(app.RECORDS_DIR, UUID) == source
        app.fetch_record_with_failover = AsyncMock(side_effect=AssertionError("unexpected network fetch"))
    else:
        assert store.read_record(app.RECORDS_DIR, UUID) is None

    for number in (1, 2):
        result = await analyze(app)
        assert result["game_id"] == UUID and result["target_actor"] == SEAT
        assert result["analysis_marker"] == f"{stage}-{number}"
        assert result["fixture_input"]["events"] == app.to_mjai(record)
        assert result["fixture_input"]["meta"] == app.game_meta(record)
        assert result["fixture_input"]["meta"]["names"] == ["东家", "南家", "西家", "北家"]
        assert result["fixture_input"]["meta"]["account_ids"] == [11, 22, 33, 44]
    assert len(calls) == decoder.call_count == 2
    assert store.read_record(app.RECORDS_DIR, UUID) == source
    assert (app.RECORDS_DIR / f"{UUID}.pb").is_file()
    assert not list(app.RECORDS_DIR.glob("*.tmp"))
    assert not (root / "reviews").exists() and not (root / "metadata").exists()
    app.login_with_account_spec.assert_not_awaited()
    if stage == "write":
        client.fetch_record.assert_awaited_once_with(UUID)
    else:
        client.fetch_record.assert_not_awaited()
        app.fetch_record_with_failover.assert_not_awaited()


def run_child(stage, cache_root):
    env = {key: value for key, value in os.environ.items() if not key.startswith("MAJSOUL_")}
    env.pop("PYTHONOPTIMIZE", None)
    env.update({"MAJSOUL_CACHE_ROOT": str(cache_root), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), stage],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf8", timeout=120)
    assert result.returncode == 0, f"{stage} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.splitlines()[-1])


def test_record_persists_but_reviews_rerun_across_processes(tmp_path):
    root = tmp_path / "持久 cache"
    writer = run_child("write", root)
    reader = run_child("read", root)
    assert [writer["stage"], reader["stage"]] == ["write", "read"]
    assert all(child["pid"] != os.getpid() for child in (writer, reader))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    stage = sys.argv[1]
    asyncio.run(run_stage(stage))
    print(json.dumps({"stage": stage, "pid": os.getpid()}))
