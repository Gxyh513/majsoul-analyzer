"""Review persistence reporting, write failures and in-flight deduplication."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

import server.app as app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA


UUID = "241026-11111111-2222-3333-4444-555555555555"
SHA = "a" * 64
SEAT = 1
EVENTS = [{"type": "start_game", "names": ["A", "B", "C", "D"]}]
META = {"uuid": UUID, "account_ids": [11, 22, 33, 44]}


def review():
    return {
        "target_actor": SEAT,
        "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
                   "weight_sha256": SHA, "version": 4, "steps": 582500,
                   "q_source": "neural_network", "review_schema": REVIEW_SCHEMA},
        "summary": {"rating": 95},
        "kyokus": [{"steps": [{"event": EVENTS[0]}]}],
        "decisions": [{"candidates": [{"label": 1, "raw_q": 0.5}]}],
    }


@pytest.fixture
def cache_environment(monkeypatch, tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    monkeypatch.setattr(app, "REVIEWS_DIR", reviews)
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path / "records")
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.setattr(app, "_review_flights", {})
    monkeypatch.setattr(app, "_review_cache_readers", {})
    monkeypatch.setattr(app, "_review_progress_listeners", {})
    monkeypatch.setattr(app, "models_download_state", {MODEL_ID: {"status": "idle"}})
    monkeypatch.setattr(app, "resolve_engine_cache_info", lambda engine: (MODEL_ID, f"{REVIEW_SCHEMA}_{SHA}"))
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    monkeypatch.delenv("MAJSOUL_ALLOWED_ORIGINS", raising=False)

    async def resolve(**kwargs):
        return UUID, copy.deepcopy(EVENTS), copy.deepcopy(META), SEAT

    monkeypatch.setattr(app, "resolve_game_data", resolve)
    inference = Mock(side_effect=lambda *args, **kwargs: review())
    monkeypatch.setattr(app, "review_game", inference)
    return inference


def analyze_payload(route):
    if route in ("paipu", "stream"):
        return {"paipu": UUID, "target_actor": SEAT, "engine": MODEL_ID, "temperature": 0.0}
    if route == "json":
        return {"content": EVENTS, "target_actor": SEAT, "engine": MODEL_ID, "temperature": 0.0}
    return {"target_actor": SEAT, "engine": MODEL_ID, "temperature": 0.0}


def request_result(client, route):
    response = client.post(f"/api/analyze/{route}", json=analyze_payload(route))
    assert response.status_code == 200
    if route == "stream":
        messages = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
                    if line.startswith("data: ")]
        complete = [message for message in messages if message["type"] == "complete"]
        assert len(complete) == 1 and not [message for message in messages if message["type"] == "error"]
        return complete[0]["result"], complete[0]["cache_persisted"]
    return response.json(), response.headers["X-Review-Cache-Persisted"] == "true"


@pytest.mark.parametrize("route", ["paipu", "json", "demo", "stream"])
@pytest.mark.parametrize("fail_write", [False, True])
def test_api_reports_persistence_without_changing_review(cache_environment, monkeypatch, caplog, route, fail_write):
    inference = cache_environment
    index_writer = Mock(wraps=app._save_review_index)
    monkeypatch.setattr(app, "_save_review_index", index_writer)
    if fail_write:
        def reject_replace(source, destination):
            raise PermissionError(13, "private-password C:/private/cache")
        monkeypatch.setattr(Path, "replace", reject_replace)

    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    result, persisted = request_result(client, route)
    assert result == {**review(), "game_id": UUID}
    assert persisted is not fail_write
    inference.assert_called_once()
    assert not list(app.REVIEWS_DIR.glob("*.tmp"))
    if fail_write:
        assert not list(app.REVIEWS_DIR.glob("*.json"))
        index_writer.assert_not_called()
        assert "Failed to save review cache" in caplog.text
        assert "PermissionError" in caplog.text and "errno=13" in caplog.text
        assert "private-password" not in caplog.text and "C:/private/cache" not in caplog.text
    else:
        saved = list(app.REVIEWS_DIR.glob("*.json"))
        assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf8")) == result
        assert len(list(app.METADATA_DIR.glob("*.json"))) == 1
        assert request_result(client, route) == (result, True)
        inference.assert_called_once()


def test_tampered_review_is_not_reused_after_index_rejection(cache_environment, monkeypatch):
    monkeypatch.setattr(app, "_rejected_review_digests", {})
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    original, _ = request_result(client, "stream")
    path = next(app.REVIEWS_DIR.glob("review-*.json"))
    altered = json.loads(path.read_text(encoding="utf8"))
    altered["kyokus"] = []
    path.write_text(json.dumps(altered), encoding="utf8")
    result, persisted = request_result(client, "stream")
    assert result == original and persisted
    assert cache_environment.call_count == 2


def test_nonstream_cache_status_is_exposed_to_allowed_origins(cache_environment, monkeypatch):
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://allowed.example")
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "synthetic-api-token")
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    response = client.post("/api/analyze/paipu", json=analyze_payload("paipu"),
                           headers={"Origin": "https://allowed.example", "X-Api-Token": "synthetic-api-token"})
    assert response.status_code == 200
    assert response.headers["X-Review-Cache-Persisted"] == "true"
    assert response.headers["Access-Control-Expose-Headers"] == "X-Review-Cache-Persisted"
    assert response.headers["Access-Control-Allow-Origin"] == "https://allowed.example"


def test_atomic_replace_failure_reports_false_and_preserves_old_review(cache_environment, monkeypatch, caplog):
    key = "review-" + "a" * 64
    old = {**review(), "game_id": UUID}
    assert app.save_cached_review(key, old) is True
    before = (app.REVIEWS_DIR / f"{key}.json").read_bytes()
    attempts = []

    def reject_replace(source, destination):
        attempts.append(destination)
        error = PermissionError(13, "secret-token /private/path")
        error.winerror = 32
        raise error

    monkeypatch.setattr(Path, "replace", reject_replace)
    monkeypatch.setattr(app.time, "sleep", lambda _: None)
    assert app.save_cached_review(key, {**old, "summary": {"rating": 0}}) is False
    assert len(attempts) == 5
    assert (app.REVIEWS_DIR / f"{key}.json").read_bytes() == before
    assert app.get_cached_review(key) == old
    assert not list(app.REVIEWS_DIR.glob("*.tmp"))
    assert "secret-token" not in caplog.text and "/private/path" not in caplog.text
    assert "winerror=32" in caplog.text


def test_existing_review_can_be_read_when_cache_lock_is_read_only(cache_environment, monkeypatch):
    key = "review-" + "b" * 64
    old = {**review(), "game_id": UUID}
    assert app.save_cached_review(key, old)
    real_open = Path.open

    def read_only_lock(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.name == ".review-cache.lock" and mode != "rb":
            raise PermissionError(13, "read-only volume")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_only_lock)
    assert app.get_cached_review(key) == old
    assert app.save_cached_review(key, {**old, "summary": {"rating": 0}}) is False
    assert app.get_cached_review(key) == old


def test_legacy_read_only_cache_works_without_existing_lock(cache_environment, monkeypatch):
    key = "review-" + "e" * 64
    old = {**review(), "game_id": UUID}
    target = app.REVIEWS_DIR / f"{key}.json"
    target.write_text(json.dumps(old), encoding="utf8")
    real_open = Path.open

    def read_only_lock(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.name == ".review-cache.lock" and mode != "rb":
            raise PermissionError(13, "read-only volume")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_only_lock)
    assert app.get_cached_review(key) == old
    assert not (app.REVIEWS_DIR / ".review-cache.lock").exists()
    (app.REVIEWS_DIR / f".{key}.invalid").touch()
    assert app.get_cached_review(key) is None


def test_marker_fsync_failure_preserves_existing_cache_hit(cache_environment, monkeypatch, caplog):
    key = "review-" + "d" * 64
    old = {**review(), "game_id": UUID}
    assert app.save_cached_review(key, old)
    real_fsync = os.fsync
    calls = []

    def reject_marker_fsync(fd):
        calls.append(True)
        if len(calls) == 2:
            raise OSError(28, "private-disk-error")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", reject_marker_fsync)
    assert app.save_cached_review(key, {**old, "summary": {"rating": 0}}) is False
    assert app.get_cached_review(key) == old
    assert not (app.REVIEWS_DIR / f".{key}.invalid").exists()
    assert "private-disk-error" not in caplog.text


def test_marker_creation_failure_preserves_old_review(cache_environment, monkeypatch, caplog):
    key = "review-" + "a" * 64
    old = {**review(), "game_id": UUID}
    assert app.save_cached_review(key, old)
    old_bytes = (app.REVIEWS_DIR / f"{key}.json").read_bytes()
    real_open = Path.open

    def reject_marker(path, *args, **kwargs):
        if path.name == f".{key}.invalid":
            raise PermissionError(13, "private-marker-secret")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_marker)
    assert app.save_cached_review(key, {**old, "summary": {"rating": 0}}) is False
    assert (app.REVIEWS_DIR / f"{key}.json").read_bytes() == old_bytes
    assert app.get_cached_review(key) == old
    assert not list(app.REVIEWS_DIR.glob("*.tmp"))
    assert "private-marker-secret" not in caplog.text


@pytest.mark.parametrize("corrupt_readback", [False, True])
def test_cross_process_reader_waits_for_verified_atomic_replacement(cache_environment, tmp_path, corrupt_readback):
    key = "review-" + "c" * 64
    assert app.save_cached_review(key, {"version": "old"})
    ready = tmp_path / "writer-ready"
    reader_ready = tmp_path / "reader-ready"
    release = tmp_path / "release"
    env = os.environ.copy()
    env.update({"MAJSOUL_CACHE_ROOT": str(tmp_path), "PYTHONUTF8": "1"})
    writer_code = r'''
import json, sys, time
from pathlib import Path
import server.app as app
key, ready, release, corrupt = sys.argv[1:]
target = app.REVIEWS_DIR / (key + ".json")
real_replace, real_read = Path.replace, Path.read_text
def delayed_replace(source, destination):
    result = real_replace(source, destination)
    if destination == target:
        Path(ready).write_text("ready", encoding="utf8")
        deadline = time.monotonic() + 20
        while not Path(release).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("writer release timed out")
            time.sleep(0.01)
    return result
def readback(path, *args, **kwargs):
    if corrupt == "1" and path == target:
        return json.dumps({"version": "corrupt"})
    return real_read(path, *args, **kwargs)
Path.replace, Path.read_text = delayed_replace, readback
print(json.dumps({"saved": app.save_cached_review(key, {"version": "new"})}))
'''
    reader_code = r'''
import json, sys
from pathlib import Path
import server.app as app
Path(sys.argv[2]).write_text("ready", encoding="utf8")
print(json.dumps({"review": app.get_cached_review(sys.argv[1])}))
'''

    def wait_until(path, process):
        deadline = time.monotonic() + 20
        while not path.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError(f"subprocess did not reach {path.name}")
            time.sleep(0.01)

    writer = subprocess.Popen([sys.executable, "-c", writer_code, key, str(ready), str(release),
                               str(int(corrupt_readback))], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf8")
    reader = None
    try:
        wait_until(ready, writer)
        reader = subprocess.Popen([sys.executable, "-c", reader_code, key, str(reader_ready)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf8")
        wait_until(reader_ready, reader)
        time.sleep(0.15)
        assert reader.poll() is None
        release.write_text("continue", encoding="utf8")
        writer_out, writer_err = writer.communicate(timeout=15)
        reader_out, reader_err = reader.communicate(timeout=15)
        assert writer.returncode == 0, writer_err
        assert reader.returncode == 0, reader_err
        assert json.loads(writer_out.splitlines()[-1])["saved"] is not corrupt_readback
        assert json.loads(reader_out.splitlines()[-1])["review"] == (None if corrupt_readback else {"version": "new"})
        assert (app.REVIEWS_DIR / f".{key}.invalid").exists() is corrupt_readback
    finally:
        release.write_text("continue", encoding="utf8")
        for process in (writer, reader):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_cleanup_preserves_old_temp_being_written_in_another_process(cache_environment, monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(app, "CACHE_DIR", cache_dir)
    ready, release = tmp_path / "writer-ready", tmp_path / "release"
    env = os.environ.copy()
    env.update({"MAJSOUL_CACHE_ROOT": str(tmp_path), "PYTHONUTF8": "1"})
    code = r'''
import json, os, sys, time
from pathlib import Path
import server.app as app
ready, release = map(Path, sys.argv[1:])
original_dump = json.dump
def slow_dump(value, file, **kwargs):
    file.write('{"partial":')
    file.flush()
    old = time.time() - 8 * 86400
    os.utime(file.name, (old, old))
    ready.write_text("ready", encoding="utf8")
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("writer release timed out")
        time.sleep(0.01)
    file.seek(0)
    file.truncate()
    original_dump(value, file, **kwargs)
json.dump = slow_dump
assert app.save_cached_review("cross-process", {"version": "new"})
'''
    writer = subprocess.Popen([sys.executable, "-c", code, str(ready), str(release)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf8")
    try:
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert writer.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        temps = list(app.REVIEWS_DIR.glob(".review-????????.tmp"))
        assert len(temps) == 1 and not app._active_review_temp_files
        assert app.cleanup_old_caches(max_days=7, max_total_mb=100) == {"cleaned_count": 0, "cleaned_bytes": 0}
        assert temps[0].exists()
        release.write_text("continue", encoding="utf8")
        _, stderr = writer.communicate(timeout=15)
        assert writer.returncode == 0, stderr
        assert app.get_cached_review("cross-process") == {"version": "new"}
        assert not list(app.REVIEWS_DIR.glob("*.tmp"))
    finally:
        release.write_text("continue", encoding="utf8")
        if writer.poll() is None:
            writer.kill()
            writer.communicate(timeout=5)


def test_invalid_temp_review_is_not_published(cache_environment, monkeypatch, caplog):
    key = "review-" + "a" * 64
    old = {**review(), "game_id": UUID}
    assert app.save_cached_review(key, old) is True
    before = (app.REVIEWS_DIR / f"{key}.json").read_bytes()
    real_dump = json.dump

    def corrupt_dump(value, destination, **kwargs):
        altered = copy.deepcopy(value)
        altered["summary"]["rating"] = 1
        real_dump(altered, destination, **kwargs)

    monkeypatch.setattr(app.json, "dump", corrupt_dump)
    assert app.save_cached_review(key, {**old, "summary": {"rating": 99}}) is False
    assert (app.REVIEWS_DIR / f"{key}.json").read_bytes() == before
    assert app.get_cached_review(key) == old
    assert not list(app.REVIEWS_DIR.glob("*.tmp"))
    assert "Failed to save review cache" in caplog.text


def test_failed_replacement_does_not_index_preexisting_valid_review(cache_environment, monkeypatch):
    signature = f"{REVIEW_SCHEMA}_{SHA}"
    key = app.review_cache_key(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0, signature)
    old = {**review(), "game_id": UUID, "summary": {"rating": 1}}
    assert app.save_cached_review(key, old)
    index_writer = Mock(wraps=app._save_review_index)
    monkeypatch.setattr(app, "_save_review_index", index_writer)
    monkeypatch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    result = asyncio.run(app._review_job(game_id=UUID, mjai_events=EVENTS, meta=META, seat=SEAT,
                                         engine=MODEL_ID, temperature=0.0, cache_key=key, weight_sha256=SHA))
    assert result == {**review(), "game_id": UUID}
    assert result.cache_persisted is False
    assert app.get_cached_review(key) == old
    index_writer.assert_not_called()
    assert not app.METADATA_DIR.exists()


def test_post_write_validation_failure_cannot_create_index(cache_environment, monkeypatch):
    key = app.review_cache_key(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0, f"{REVIEW_SCHEMA}_{SHA}")
    target = app.REVIEWS_DIR / f"{key}.json"
    real_read = Path.read_text

    def corrupt_readback(path, *args, **kwargs):
        text = real_read(path, *args, **kwargs)
        if path == target:
            altered = json.loads(text)
            altered["summary"]["rating"] = 1
            return json.dumps(altered)
        return text

    monkeypatch.setattr(Path, "read_text", corrupt_readback)
    index_writer = Mock(wraps=app._save_review_index)
    monkeypatch.setattr(app, "_save_review_index", index_writer)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    result, persisted = request_result(client, "paipu")
    assert result == {**review(), "game_id": UUID} and persisted is False
    index_writer.assert_not_called()
    assert not app.METADATA_DIR.exists()
    assert app.get_cached_review(key) is None
    assert (app.REVIEWS_DIR / f".{key}.invalid").is_file()
    env = os.environ.copy()
    env["MAJSOUL_CACHE_ROOT"] = str(app.REVIEWS_DIR.parent)
    child = subprocess.run([sys.executable, "-c", "import server.app as app, sys; assert app.get_cached_review(sys.argv[1]) is None", key],
                           env=env, capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    monkeypatch.setattr(Path, "read_text", real_read)
    result, persisted = request_result(client, "paipu")
    assert result == {**review(), "game_id": UUID} and persisted is True
    assert cache_environment.call_count == 2
    assert not (app.REVIEWS_DIR / f".{key}.invalid").exists()
    assert app.get_cached_review(key) == result


def test_unexpected_cache_save_error_keeps_analysis(cache_environment, monkeypatch, caplog):
    def fail_save(*args, **kwargs):
        raise PermissionError(13, "private-secret C:/private/cache")

    monkeypatch.setattr(app, "save_cached_review", fail_save)
    key = app.review_cache_key(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0, f"{REVIEW_SCHEMA}_{SHA}")
    result = asyncio.run(app._review_job(game_id=UUID, mjai_events=EVENTS, meta=META, seat=SEAT,
                                         engine=MODEL_ID, temperature=0.0, cache_key=key, weight_sha256=SHA))
    assert result == {**review(), "game_id": UUID} and result.cache_persisted is False
    assert not app.METADATA_DIR.exists()
    assert "Failed to save review cache" in caplog.text
    assert "private-secret" not in caplog.text and "C:/private/cache" not in caplog.text


def test_unreadable_written_review_returns_result_without_index(cache_environment, monkeypatch, caplog):
    original_read = Path.read_text

    def reject_review_read(path, *args, **kwargs):
        if path.parent == app.REVIEWS_DIR and path.suffix == ".json":
            raise PermissionError(13, "private-token C:/private/cache")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_review_read)
    index_writer = Mock(wraps=app._save_review_index)
    monkeypatch.setattr(app, "_save_review_index", index_writer)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    result, persisted = request_result(client, "paipu")
    assert result == {**review(), "game_id": UUID} and persisted is False
    index_writer.assert_not_called()
    assert "Failed to save review cache" in caplog.text
    assert "private-token" not in caplog.text and "C:/private/cache" not in caplog.text


def test_failed_index_does_not_change_review_persistence_status(cache_environment, monkeypatch, caplog):
    real_replace = os.replace

    def reject_index(source, destination):
        if Path(destination).parent == app.METADATA_DIR:
            raise OSError("private index failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", reject_index)
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    result, persisted = request_result(client, "paipu")
    assert result == {**review(), "game_id": UUID} and persisted is True
    assert len(list(app.REVIEWS_DIR.glob("*.json"))) == 1
    assert not list(app.METADATA_DIR.glob("*.json")) and not list(app.METADATA_DIR.glob("*.tmp"))
    assert "Failed to save review index" in caplog.text and "private index failure" not in caplog.text


def test_index_requires_real_matching_review_and_does_not_store_credentials(cache_environment):
    signature = f"{REVIEW_SCHEMA}_{SHA}"
    key = app.review_cache_key(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0, signature)
    index_path = app._review_index_path(UUID, SEAT, MODEL_ID, 0.0, signature)
    app._save_review_index(UUID, META, SEAT, MODEL_ID, 0.0, signature, key)
    assert not index_path.exists()
    assert app.save_cached_review(key, {**review(), "game_id": UUID, "target_actor": 0})
    app._save_review_index(UUID, META, SEAT, MODEL_ID, 0.0, signature, key)
    assert not index_path.exists()
    assert app.save_cached_review(key, {**review(), "game_id": UUID})
    meta = {"account_ids": [11, {"access_token": "secret-token"}, 33, 44], "password": "secret-password"}
    app._save_review_index(UUID, meta, SEAT, MODEL_ID, 0.0, signature, key)
    text = index_path.read_text(encoding="utf8")
    assert "secret-token" not in text and "secret-password" not in text
    assert json.loads(text)["account_ids"] == [11, 0, 33, 44]


def test_same_review_requests_share_result_even_if_disk_write_fails(cache_environment, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        follower_joined = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        calls = []
        original_start = app._start_review_job
        starts = []

        def blocked_review(*args, **kwargs):
            calls.append(True)
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise RuntimeError("test did not release inference")
            return review()

        async def observe_start(**kwargs):
            starts.append(True)
            if len(starts) == 2:
                follower_joined.set()
            return await original_start(**kwargs)

        monkeypatch.setattr(app, "review_game", blocked_review)
        monkeypatch.setattr(app, "save_cached_review", lambda *args: False)
        monkeypatch.setattr(app, "_start_review_job", observe_start)
        try:
            first = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.wait_for(follower_joined.wait(), 2)
            assert not second.done()
            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 3)
            assert all(result == {**review(), "game_id": UUID} and result.cache_persisted is False for result in results)
            assert calls == [True]
            await asyncio.sleep(0)
            assert not app._review_tasks and not app._review_flights
            again = await app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0)
            assert again.cache_persisted is False and calls == [True, True]
        finally:
            release.set()

    asyncio.run(scenario())


def test_overlapping_cache_read_does_not_repeat_failed_write_review(cache_environment, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        inference_started = asyncio.Event()
        follower_reading = asyncio.Event()
        inference_release = threading.Event()
        read_release = threading.Event()
        delay_read = threading.Event()
        calls = []
        real_read = app._valid_review_for_game

        def blocked_review(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                delay_read.set()
                loop.call_soon_threadsafe(inference_started.set)
                if not inference_release.wait(5):
                    raise RuntimeError("test did not release inference")
            return review()

        def delayed_cache_read(*args):
            if delay_read.is_set():
                delay_read.clear()
                loop.call_soon_threadsafe(follower_reading.set)
                if not read_release.wait(5):
                    raise RuntimeError("test did not release cache read")
            return real_read(*args)

        monkeypatch.setattr(app, "review_game", blocked_review)
        monkeypatch.setattr(app, "save_cached_review", lambda *args: False)
        monkeypatch.setattr(app, "_valid_review_for_game", delayed_cache_read)
        try:
            first = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.wait_for(inference_started.wait(), 2)
            second = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.wait_for(follower_reading.wait(), 2)
            inference_release.set()
            first_result = await asyncio.wait_for(first, 2)
            await asyncio.sleep(0)
            assert not second.done()
            read_release.set()
            second_result = await asyncio.wait_for(second, 2)
            assert first_result == second_result == {**review(), "game_id": UUID}
            assert first_result.cache_persisted is second_result.cache_persisted is False
            assert calls == [True]
        finally:
            inference_release.set()
            read_release.set()

    asyncio.run(scenario())


@pytest.mark.parametrize("leader_stream", [False, True])
def test_same_review_streams_share_progress_after_owner_disconnect(cache_environment, monkeypatch, leader_stream):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        send_first = threading.Event()
        send_second = threading.Event()
        finish = threading.Event()
        calls = []

        def progress_review(*args, **kwargs):
            calls.append(True)
            loop.call_soon_threadsafe(started.set)
            if not send_first.wait(5):
                raise RuntimeError("test did not send first progress")
            kwargs["progress_callback"](42, "first progress")
            if not send_second.wait(5):
                raise RuntimeError("test did not send second progress")
            kwargs["progress_callback"](65, "second progress")
            if not finish.wait(5):
                raise RuntimeError("test did not finish inference")
            return review()

        async def next_message(stream):
            return json.loads((await asyncio.wait_for(anext(stream), 2)).removeprefix("data: ").strip())

        monkeypatch.setattr(app, "review_game", progress_review)
        request = app.AnalyzeStreamReq(**analyze_payload("stream"))
        owner = None
        leader = None
        follower = None
        try:
            if leader_stream:
                leader = (await app.api_analyze_stream(request)).body_iterator
                assert (await next_message(leader))["percent"] == 5
                assert (await next_message(leader))["percent"] == 15
            else:
                owner = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.wait_for(started.wait(), 2)
            follower = (await app.api_analyze_stream(request)).body_iterator
            assert (await next_message(follower))["percent"] == 5
            assert (await next_message(follower))["type"] == "queued"
            assert (await next_message(follower))["percent"] == 15
            send_first.set()
            if leader is not None:
                assert (await next_message(leader))["percent"] == 42
            assert (await next_message(follower))["percent"] == 42
            if leader is not None:
                await leader.aclose()
            else:
                owner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owner
            send_second.set()
            assert (await next_message(follower))["percent"] == 65
            finish.set()
            messages = [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in follower]
            complete = [message for message in messages if message["type"] == "complete"]
            assert len(complete) == 1 and complete[0]["result"] == {**review(), "game_id": UUID}
            assert complete[0]["cache_persisted"] is True and calls == [True]
            await asyncio.sleep(0)
            assert not app._review_tasks and not app._review_progress_listeners
        finally:
            send_first.set()
            send_second.set()
            finish.set()
            if leader is not None:
                await leader.aclose()
            if follower is not None:
                await follower.aclose()

    asyncio.run(scenario())


def test_real_demo_review_persists_without_changing_result(monkeypatch, tmp_path):
    from server.engine.reviewer import EngineManager

    models_dir = app.DATA_DIR / "models"
    if not (models_dir / MODEL_FILE).is_file():
        pytest.skip("published model not present")
    monkeypatch.setattr(app, "REVIEWS_DIR", tmp_path)
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(app, "engine_mgr", EngineManager(models_dir))
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.setattr(app, "_review_flights", {})
    monkeypatch.setattr(app, "_review_cache_readers", {})
    monkeypatch.setattr(app, "_review_progress_listeners", {})
    events = json.loads(app.DEMO_PATH.read_text(encoding="utf8"))
    meta = {"names": events[0].get("names", ["A", "B", "C", "D"])}

    async def scenario():
        result = await app._run_review_with_cache("demo", events, meta, 0, MODEL_ID, 0.1)
        assert result.cache_persisted is True
        saved = list(tmp_path.glob("review-*.json"))
        assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf8")) == result
        assert (await app._run_review_with_cache("demo", events, meta, 0, MODEL_ID, 0.1)) == result

    asyncio.run(scenario())


def test_cancelled_queued_owner_transfers_request_to_follower(cache_environment, monkeypatch):
    async def scenario():
        await app.review_semaphore.acquire()
        owner = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
        while not app._review_flights:
            await asyncio.sleep(0)
        follower = asyncio.create_task(app._run_review_with_cache(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
        await asyncio.sleep(0.05)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        app.review_semaphore.release()
        result = await asyncio.wait_for(follower, 3)
        assert result == {**review(), "game_id": UUID} and result.cache_persisted is True
        cache_environment.assert_called_once()
        await asyncio.sleep(0)
        assert not app._review_tasks and not app._review_flights

    asyncio.run(scenario())
