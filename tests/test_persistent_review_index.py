import asyncio
from unittest.mock import AsyncMock

import pytest

import server.app as app


UUID = "241026-11111111-2222-3333-4444-555555555555"


@pytest.fixture
def index_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(app, "REVIEWS_DIR", tmp_path / "reviews")
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


def test_index_rejects_wrong_review_identity(index_state, monkeypatch):
    key = "review-" + "a" * 64
    app._save_review_index(UUID, {}, 0, "mortal", 0.0, "schema_weight", key)
    monkeypatch.setattr(app, "_valid_cached_review", lambda *args: {"game_id": UUID, "target_actor": 1})
    assert app._lookup_indexed_review(UUID, 0, "mortal", 0.0) is None


def test_broken_index_falls_back(index_state):
    path = app._review_index_path(UUID, 0, "mortal", 0.0, "schema_weight")
    path.parent.mkdir()
    path.write_text("broken", encoding="utf8")
    assert app._lookup_indexed_review(UUID, 0, "mortal", 0.0) is None


def test_atomic_index_failure_preserves_previous(index_state, monkeypatch):
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
async def test_startup_does_not_log_in(monkeypatch):
    login = AsyncMock()
    monkeypatch.setattr(app, "ensure_ms_logged_in", login)
    async with app.lifespan(app.app):
        await asyncio.sleep(0)
    login.assert_not_called()
