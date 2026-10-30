"""Previously indexed reviews must now be derived from current record input."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import server.app as app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA


UUID = "241026-11111111-2222-3333-4444-555555555555"
SHA = MODEL_SHA256


def test_changed_legacy_record_is_read_and_reanalysed(monkeypatch, tmp_path):
    async def scenario():
        records = tmp_path / "records"
        records.mkdir()
        monkeypatch.setattr(app, "RECORDS_DIR", records)
        monkeypatch.setattr(app, "LEGACY_RECORDS_DIRS", ())
        monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(app, "_review_tasks", set())
        monkeypatch.setattr(app, "load_config", lambda: {})
        monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(
            get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
        ))
        fetch = AsyncMock(side_effect=AssertionError("unexpected network fetch"))
        monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
        converter = Mock(wraps=app.to_mjai)
        monkeypatch.setattr(app, "to_mjai", converter)
        inference = Mock(side_effect=lambda events, seat, manager, **kwargs: {
            "target_actor": seat, "meta": kwargs["meta"],
            "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
                       "weight_sha256": SHA, "version": 4, "steps": None, "revision": MODEL_REVISION,
                       "q_source": "neural_network", "review_schema": REVIEW_SCHEMA},
        })
        monkeypatch.setattr(app, "review_game", inference)
        old = {"head": {"uuid": UUID, "accounts": [{"seat": 0, "account_id": 11, "nickname": "Old"}]}, "actions": []}
        new = copy.deepcopy(old)
        new["head"]["accounts"][0]["nickname"] = "New"
        path = records / f"{UUID}.json"
        results = []
        for record in (old, new):
            # Simulate an externally updated legacy file, not an overwrite by the record store.
            path.write_text(json.dumps(record), encoding="utf8")
            game_id, events, meta, seat = await app.resolve_game_data(paipu=UUID, target_actor=0)
            results.append(await app._run_review(game_id, events, meta, seat, MODEL_ID, 0.0))
        assert [result["meta"]["names"][0] for result in results] == ["Old", "New"]
        assert converter.call_count == inference.call_count == 2
        fetch.assert_not_awaited()
        assert not (tmp_path / "reviews").exists()
        assert not (tmp_path / "metadata").exists()

    asyncio.run(scenario())


def test_concurrent_different_seats_resolve_independently(monkeypatch, tmp_path):
    async def scenario():
        record = {"head": {"uuid": UUID, "accounts": [
            {"seat": 0, "account_id": 11, "nickname": "A"},
            {"seat": 1, "account_id": 22, "nickname": "B"},
        ]}, "actions": []}
        (tmp_path / f"{UUID}.json").write_text(json.dumps(record), encoding="utf8")
        monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
        monkeypatch.setattr(app, "LEGACY_RECORDS_DIRS", ())
        monkeypatch.setattr(app, "load_config", lambda: {})
        fetch = AsyncMock(side_effect=AssertionError("unexpected network fetch"))
        monkeypatch.setattr(app, "fetch_record_with_failover", fetch)
        converter = Mock(wraps=app.to_mjai)
        monkeypatch.setattr(app, "to_mjai", converter)
        first, second = await asyncio.gather(
            app.resolve_game_data(paipu=UUID, target_actor=0),
            app.resolve_game_data(paipu=UUID + "_22"),
        )
        assert (first[3], second[3]) == (0, 1)
        assert first[1:3] == second[1:3]
        assert converter.call_count == 2
        fetch.assert_not_awaited()

    asyncio.run(scenario())


def test_startup_does_not_log_in(monkeypatch):
    async def scenario():
        login = AsyncMock()
        monkeypatch.setattr(app, "ensure_ms_logged_in", login)
        monkeypatch.setattr(app, "_review_tasks", set())
        monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(status=lambda: {}, selected_model=MODEL_ID))
        async with app.lifespan(app.app):
            await asyncio.sleep(0)
        login.assert_not_awaited()

    asyncio.run(scenario())
