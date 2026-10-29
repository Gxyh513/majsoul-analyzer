"""Controlled regressions for review cancellation and public API privacy."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, REVIEW_SCHEMA, valid_review_source
from server.majsoul.client import MajsoulError


SHA = "a" * 64


def valid_result(sha=SHA):
    return {
        "engine": {
            "type": "mortal",
            "id": MODEL_ID,
            "weight_file": MODEL_FILE,
            "weight_sha256": sha,
            "version": 4,
            "steps": 582500,
            "q_source": "neural_network",
            "review_schema": REVIEW_SCHEMA,
        },
        "summary": {},
    }


class ControlledReviews:
    """Run inference in real threads, released only by test-owned events."""

    def __init__(self, loop, *, emit_progress=False, invalid_first=False):
        self.loop = loop
        self.emit_progress = emit_progress
        self.invalid_first = invalid_first
        self.started = {name: asyncio.Event() for name in ("first", "other")}
        self.release = {name: threading.Event() for name in self.started}
        self.lock = threading.Lock()
        self.calls = []
        self.active = 0
        self.max_active = 0

    def review(self, events, seat, mgr, **kwargs):
        name = events[0]["name"]
        with self.lock:
            self.calls.append(name)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            call_number = len(self.calls)
        self.loop.call_soon_threadsafe(self.started[name].set)
        try:
            callback = kwargs.get("progress_callback")
            if callback and self.emit_progress:
                callback(35, "正在检讨本局")
            if not self.release[name].wait(10):
                raise RuntimeError("test did not release review")
            return valid_result("b" * 64 if self.invalid_first and call_number == 1 else SHA)
        finally:
            with self.lock:
                self.active -= 1

    def release_all(self):
        for event in self.release.values():
            event.set()


@pytest.fixture
def review_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())
    monkeypatch.setattr(server_app, "resolve_engine_cache_info", lambda _: (MODEL_ID, f"{REVIEW_SCHEMA}_{SHA}"))

    async def resolve(paipu=None, content=None, target_actor=None):
        name = paipu or "first"
        return name, [{"name": name}], {"names": ["A", "B", "C", "D"]}, 0

    monkeypatch.setattr(server_app, "resolve_game_data", resolve)


async def nonstream(name):
    return await server_app._run_review_with_cache(
        name, [{"name": name}], {"names": ["A", "B", "C", "D"]}, 0, MODEL_ID, 0.1
    )


def event_from_chunk(chunk):
    return json.loads(chunk.removeprefix("data: ").strip())


async def wait_started(control, name):
    await asyncio.wait_for(control.started[name].wait(), timeout=2)


async def assert_queued_and_finish(control, owner):
    """Same-key waiter uses the completed cache; different-key work stays serialized."""
    same = asyncio.create_task(nonstream("first"))
    other = asyncio.create_task(nonstream("other"))
    await asyncio.sleep(0.03)
    assert not control.started["other"].is_set()
    assert server_app.review_semaphore.locked()
    assert len(server_app._review_tasks) == 1

    control.release["first"].set()
    same_result = await asyncio.wait_for(same, timeout=2)
    assert same_result["game_id"] == "first"
    await wait_started(control, "other")
    assert control.calls.count("first") == 1
    assert control.max_active == 1
    control.release["other"].set()
    assert (await asyncio.wait_for(other, timeout=2))["game_id"] == "other"
    await asyncio.sleep(0)
    assert not server_app._review_tasks
    assert not server_app.review_semaphore.locked()
    if owner is not None:
        assert owner.cancelled()


@pytest.mark.parametrize("disconnect_at", ["startup_yield", "queue_wait", "progress_yield"])
def test_stream_disconnect_keeps_permit_and_saves(review_environment, monkeypatch, disconnect_at):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop(), emit_progress=disconnect_at == "progress_yield")
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"))
            stream = response.body_iterator
            assert event_from_chunk(await anext(stream))["percent"] == 5
            assert event_from_chunk(await anext(stream))["percent"] == 15
            await wait_started(control, "first")

            if disconnect_at == "queue_wait":
                waiting = asyncio.create_task(anext(stream))
                await asyncio.sleep(0.03)
                assert not waiting.done()
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
            elif disconnect_at == "progress_yield":
                assert event_from_chunk(await asyncio.wait_for(anext(stream), timeout=2))["percent"] == 35
                await stream.aclose()
            else:
                await stream.aclose()

            await assert_queued_and_finish(control, None)
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_disconnect_during_asgi_send_closes_listener_but_not_worker(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        blocked_send = asyncio.Event()
        try:
            response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"))

            async def send(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    event = event_from_chunk(message["body"].decode())
                    if event.get("percent") == 15:
                        blocked_send.set()
                        await asyncio.Event().wait()

            sender = asyncio.create_task(response.stream_response(send))
            await asyncio.wait_for(blocked_send.wait(), timeout=2)
            await wait_started(control, "first")
            sender.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sender
            assert response.body_iterator.ag_frame is None
            await assert_queued_and_finish(control, None)
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_nonstream_cancellation_and_event_loop_responsiveness(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            started_at = time.monotonic()
            owner = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            # The inference thread is still blocked; the loop must keep running.
            await asyncio.wait_for(asyncio.sleep(0.02), timeout=0.5)
            assert time.monotonic() - started_at < 1.0
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            await assert_queued_and_finish(control, owner)
        finally:
            control.release_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["nonstream", "stream", "inside_semaphore"])
def test_slow_cache_read_does_not_block_event_loop(review_environment, monkeypatch, path):
    async def scenario():
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        calls = []
        result = valid_result()

        def read_cache(_key):
            calls.append(True)
            if path == "inside_semaphore" and len(calls) == 1:
                return None
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise RuntimeError("test did not release slow cache read")
            return result

        monkeypatch.setattr(server_app, "get_cached_review", read_cache)
        try:
            if path == "stream":
                response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"))
                stream = response.body_iterator
                assert event_from_chunk(await anext(stream))["percent"] == 5
                owner = asyncio.create_task(anext(stream))
            else:
                owner = asyncio.create_task(nonstream("first"))
            await asyncio.wait_for(started.wait(), 2)
            ticks = []
            await asyncio.wait_for(asyncio.sleep(0.03), 0.5)
            ticks.append(True)
            assert ticks and not owner.done()
            if path == "inside_semaphore":
                assert server_app.review_semaphore.locked()
            release.set()
            value = await asyncio.wait_for(owner, 2)
            if path == "stream":
                assert event_from_chunk(value)["percent"] == 100
                assert event_from_chunk(await anext(stream))["result"] == result
                await stream.aclose()
            else:
                assert value == result
            assert not server_app._review_tasks
            assert not server_app.review_semaphore.locked()
        finally:
            release.set()

    asyncio.run(scenario())


def test_cancel_during_semaphore_cache_recheck_releases_permit(review_environment, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        calls = []

        def read_cache(_key):
            calls.append(True)
            if len(calls) == 1:
                return None
            loop.call_soon_threadsafe(started.set)
            release.wait(5)
            return None

        monkeypatch.setattr(server_app, "get_cached_review", read_cache)
        try:
            owner = asyncio.create_task(nonstream("first"))
            await asyncio.wait_for(started.wait(), 2)
            assert server_app.review_semaphore.locked()
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert not server_app.review_semaphore.locked()
            assert not server_app._review_tasks
        finally:
            release.set()

    asyncio.run(scenario())


def test_stream_and_nonstream_complete_normally(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop(), emit_progress=True)
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"))
            stream = response.body_iterator
            assert event_from_chunk(await anext(stream))["percent"] == 5
            assert event_from_chunk(await anext(stream))["percent"] == 15
            await wait_started(control, "first")
            assert event_from_chunk(await asyncio.wait_for(anext(stream), timeout=2))["percent"] == 35
            control.release["first"].set()
            messages = [event_from_chunk(chunk) async for chunk in stream]
            assert next(event["result"] for event in messages if event["type"] == "complete")["game_id"] == "first"
            assert (await nonstream("first"))["game_id"] == "first"
            assert control.calls == ["first"]
            await asyncio.sleep(0)
            assert not server_app._review_tasks
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_cancelling_queued_stream_never_starts_job(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            owner = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="other"))
            stream = response.body_iterator
            assert event_from_chunk(await anext(stream))["percent"] == 5
            assert event_from_chunk(await anext(stream))["type"] == "queued"
            waiter = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.03)
            assert not waiter.done()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            control.release["first"].set()
            await asyncio.wait_for(owner, timeout=2)
            await asyncio.sleep(0)
            assert control.calls == ["first"]
            assert not server_app._review_tasks
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_shutdown_drains_detached_inference(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(status=lambda: {}, selected_model=MODEL_ID))

        async def fake_cleanup():
            await asyncio.Event().wait()

        async def fake_login():
            await asyncio.Event().wait()

        monkeypatch.setattr(server_app, "_cache_cleanup_loop", fake_cleanup)
        monkeypatch.setattr(server_app, "ensure_ms_logged_in", fake_login)
        monkeypatch.setattr(server_app, "_ms_client_lock", asyncio.Lock())
        lifespan = server_app.lifespan(server_app.app)
        try:
            await lifespan.__aenter__()
            owner = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await asyncio.sleep(0.03)
            assert not closing.done()
            assert server_app.review_semaphore.locked()
            control.release["first"].set()
            await asyncio.wait_for(closing, timeout=2)
            assert not server_app._review_tasks
            assert (await nonstream("first"))["game_id"] == "first"
            assert control.calls == ["first"]
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_cancelled_queued_request_never_starts_background_work(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            first = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            queued = asyncio.create_task(nonstream("other"))
            await asyncio.sleep(0.03)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert len(server_app._review_tasks) == 1
            control.release["first"].set()
            await asyncio.wait_for(first, timeout=2)
            await asyncio.sleep(0)
            assert control.calls == ["first"]
            assert not server_app._review_tasks
        finally:
            control.release_all()

    asyncio.run(scenario())


class MemoryASGIRequest:
    def __init__(self, path, payload):
        self.messages = asyncio.Queue()
        self.messages.put_nowait({"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False})
        self.sent = []
        self.scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "scheme": "http", "method": "POST", "path": path,
            "raw_path": path.encode(), "root_path": "", "query_string": b"",
            "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 80),
        }

    async def receive(self):
        return await self.messages.get()

    async def send(self, message):
        self.sent.append(message)

    def start(self):
        return asyncio.create_task(server_app.app(self.scope, self.receive, self.send))

    def disconnect(self):
        self.messages.put_nowait({"type": "http.disconnect"})


@pytest.mark.parametrize("route", ["paipu", "json", "demo"])
def test_asgi_nonstream_disconnect_while_queued_does_not_start_review(review_environment, monkeypatch, route):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
        monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
        entered = asyncio.Event()

        async def resolve(**_kwargs):
            entered.set()
            return "other", [{"name": "other"}], {"names": ["A", "B", "C", "D"]}, 0

        monkeypatch.setattr(server_app, "resolve_game_data", resolve)
        payloads = {"paipu": {"paipu": "other"}, "json": {"content": [{"type": "start_game"}]}, "demo": {}}
        try:
            first = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            request = MemoryASGIRequest(f"/api/analyze/{route}", payloads[route])
            request_task = request.start()
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0.03)
            assert not control.started["other"].is_set()
            request.disconnect()
            await asyncio.wait_for(asyncio.gather(request_task, return_exceptions=True), 2)
            assert len(server_app._review_tasks) == 1
            control.release["first"].set()
            await asyncio.wait_for(first, 2)
            await asyncio.sleep(0)
            assert control.calls == ["first"]
            assert not server_app._review_tasks
            assert not server_app.review_semaphore.locked()
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_asgi_nonstream_disconnect_during_download_wait_stops_preparation(review_environment, monkeypatch):
    async def scenario():
        monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
        monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "downloading"}})
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def waiting(_engine):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(server_app, "_wait_for_model_download", waiting)
        request = MemoryASGIRequest("/api/analyze/paipu", {"paipu": "first"})
        request_task = request.start()
        await asyncio.wait_for(entered.wait(), 2)
        request.disconnect()
        await asyncio.wait_for(asyncio.gather(request_task, return_exceptions=True), 2)
        assert cancelled.is_set()
        assert not server_app._review_tasks
        assert not server_app.review_semaphore.locked()

    asyncio.run(scenario())


def test_asgi_nonstream_disconnect_after_start_preserves_worker_and_cache(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
        monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
        try:
            request = MemoryASGIRequest("/api/analyze/paipu", {"paipu": "first"})
            request_task = request.start()
            await wait_started(control, "first")
            request.disconnect()
            await asyncio.wait_for(asyncio.gather(request_task, return_exceptions=True), 2)
            await assert_queued_and_finish(control, None)
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_asgi_nonstream_success_stops_disconnect_watcher(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
        monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
        try:
            request = MemoryASGIRequest("/api/analyze/paipu", {"paipu": "first"})
            request_task = request.start()
            await wait_started(control, "first")
            control.release["first"].set()
            await asyncio.wait_for(request_task, 2)
            assert any(message["type"] == "http.response.start" and message["status"] == 200 for message in request.sent)
            assert not server_app._review_tasks
            assert not server_app.review_semaphore.locked()
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_cancelling_job_itself_waits_for_thread_and_cache(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            owner = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            job = next(iter(server_app._review_tasks))
            job.cancel()
            await asyncio.sleep(0)
            job.cancel()
            await asyncio.sleep(0)
            assert server_app.review_semaphore.locked()
            same = asyncio.create_task(nonstream("first"))
            await asyncio.sleep(0.03)
            assert not same.done()
            control.release["first"].set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(owner, timeout=2)
            result = await asyncio.wait_for(same, timeout=2)
            assert result["game_id"] == "first"
            assert control.calls == ["first"]
            await asyncio.sleep(0)
            assert not server_app._review_tasks
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_worker_cancelled_error_does_not_spin_or_leak(tmp_path):
    # Run in a subprocess so a regression cannot freeze the entire suite's
    # event loop and prevent an asyncio timeout from firing.
    script = textwrap.dedent("""
        import asyncio
        import pathlib

        original_is_file = pathlib.Path.is_file
        pathlib.Path.is_file = lambda self: False if self.name in ('config.json', 'config.local.json') else original_is_file(self)
        from server import app
        pathlib.Path.is_file = original_is_file

        app.REVIEWS_DIR = pathlib.Path(__import__('sys').argv[1])
        app.review_semaphore = asyncio.Semaphore(1)
        app._review_tasks = set()
        app.resolve_engine_cache_info = lambda _: ('akagi', 'schema_' + 'a' * 64)
        def cancelled_worker(*args, **kwargs):
            raise asyncio.CancelledError('worker failed')
        app.review_game = cancelled_worker

        async def scenario():
            try:
                await asyncio.wait_for(app._run_review_with_cache('game', [], {}, 0, 'akagi', 0.1), 3)
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError('worker cancellation was not propagated')
            await asyncio.sleep(0)
            assert not app._review_tasks
            assert not app.review_semaphore.locked()
            assert not list(app.REVIEWS_DIR.glob('*.json'))
            await asyncio.wait_for(asyncio.sleep(0.01), 1)

        asyncio.run(scenario())
    """)
    env = {key: value for key, value in os.environ.items() if not key.startswith("MAJSOUL_")}
    process = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True,
        encoding="utf8", errors="replace", env=env, timeout=20,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_detached_invalid_result_is_never_cached(review_environment, monkeypatch, tmp_path):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop(), invalid_first=True)
        monkeypatch.setattr(server_app, "review_game", control.review)
        try:
            owner = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            job = next(iter(server_app._review_tasks))
            control.release["first"].set()
            result = await asyncio.gather(job, return_exceptions=True)
            assert isinstance(result[0], RuntimeError)
            await asyncio.sleep(0)
            assert not list(tmp_path.glob("*.json"))
            assert not server_app._review_tasks
            # A later valid attempt must actually run and can populate the cache.
            assert (await asyncio.wait_for(nonstream("first"), timeout=2))["game_id"] == "first"
            assert control.calls == ["first", "first"]
            assert len(list(tmp_path.glob("*.json"))) == 1
        finally:
            control.release_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("fields", "old_summary", "new_summary"),
    [
        ({"yiman": True, "title_id": 5, "han": 1, "fu": 50}, "50符1番", "役满（大三元）"),
        ({"yiman": False, "title_id": 11, "han": 13, "fu": 30}, "30符13番", "累计役满 30符13番"),
    ],
)
def test_old_yakuman_review_cache_is_invalidated(review_environment, monkeypatch, tmp_path, streaming, fields, old_summary, new_summary):
    async def scenario():
        events = [{"type": "hora", "actor": 0, "target": 1, **fields}]
        meta = {"names": ["A", "B", "C", "D"]}
        weight_sig = f"{REVIEW_SCHEMA}_{SHA}"
        old_parts = ["first", events, meta, 0, MODEL_ID, 0.1, weight_sig]
        payload = json.dumps(old_parts, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        old_key = "review-" + hashlib.sha256(payload.encode("utf8")).hexdigest()
        new_key = server_app.review_cache_key("first", events, meta, 0, MODEL_ID, 0.1, weight_sig)
        assert old_key != new_key
        ordinary = [{"type": "hora", "yiman": False, "han": 1, "fu": 50}]
        ordinary_payload = json.dumps(["first", ordinary, meta, 0, MODEL_ID, 0.1, weight_sig], sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        assert server_app.review_cache_key("first", ordinary, meta, 0, MODEL_ID, 0.1, weight_sig) == "review-" + hashlib.sha256(ordinary_payload.encode("utf8")).hexdigest()

        stale = valid_result()
        stale["kyokus"] = [{"end_summaries": [f"A 荣和 B {old_summary} +32000"]}]
        server_app.save_cached_review(old_key, stale)
        calls = []

        def review(*args, **kwargs):
            calls.append(True)
            fresh = valid_result()
            fresh["kyokus"] = [{"end_summaries": [f"A 荣和 B {new_summary} +32000"]}]
            return fresh

        async def resolve(**kwargs):
            return "first", events, meta, 0

        monkeypatch.setattr(server_app, "review_game", review)
        monkeypatch.setattr(server_app, "resolve_game_data", resolve)
        if streaming:
            response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"))
            messages = [event_from_chunk(chunk) async for chunk in response.body_iterator]
            result = next(message["result"] for message in messages if message["type"] == "complete")
        else:
            result = await server_app._run_review_with_cache("first", events, meta, 0, MODEL_ID, 0.1)
        assert calls == [True]
        assert new_summary in result["kyokus"][0]["end_summaries"][0]
        assert result["kyokus"][0]["end_summaries"][0] != stale["kyokus"][0]["end_summaries"][0]
        assert result["engine"]["review_schema"] == REVIEW_SCHEMA
        assert server_app.get_cached_review(new_key) == result
        assert server_app.get_cached_review(old_key) == stale

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("event", "invalidates"),
    [
        ({"type": "hora", "yiman": True, "title_id": 5, "han": 1, "fu": 50}, True),
        ({"type": "hora", "yiman": True, "title_id": 10, "han": 6, "fu": 50}, True),
        ({"type": "hora", "yiman": False, "title_id": 11, "han": 13, "fu": 30}, True),
        ({"type": "hora", "yiman": False, "title_id": 4, "han": 11, "fu": 30}, False),
    ],
)
def test_only_yakuman_or_counted_yakuman_changes_legacy_cache_key(event, invalidates):
    events = [event]
    parts = ["game", events, {}, 0, MODEL_ID, 0.1, f"{REVIEW_SCHEMA}_{SHA}"]
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    legacy = "review-" + hashlib.sha256(payload.encode("utf8")).hexdigest()
    current = server_app.review_cache_key(*parts)
    assert (legacy != current) is invalidates


def test_mjai_disconnect_retry_has_stable_identity(monkeypatch, tmp_path):
    async def scenario():
        now = [1000]
        monkeypatch.setattr(server_app.time, "time", lambda: now[0])
        monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
        monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(server_app, "_review_tasks", set())
        monkeypatch.setattr(server_app, "_MEMORY_LOGS", {})
        monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
        monkeypatch.setattr(server_app, "resolve_engine_cache_info", lambda _: (MODEL_ID, f"{REVIEW_SCHEMA}_{SHA}"))
        started = asyncio.Event()
        release = threading.Event()
        calls = []
        loop = asyncio.get_running_loop()
        events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}]

        def review(_events, _seat, _manager, **kwargs):
            calls.append(copy.deepcopy(kwargs["meta"]))
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise RuntimeError("test did not release review")
            return {**valid_result(), "meta": kwargs["meta"]}

        monkeypatch.setattr(server_app, "review_game", review)
        try:
            response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(content=events))
            stream = response.body_iterator
            assert event_from_chunk(await anext(stream))["percent"] == 5
            assert event_from_chunk(await anext(stream))["percent"] == 15
            await asyncio.wait_for(started.wait(), 2)
            await stream.aclose()
            now[0] = 2000
            game_id, retry_events, meta, seat = await server_app.resolve_game_data(content=events)
            assert meta["start_time"] is None
            retry = asyncio.create_task(server_app._run_review_with_cache(game_id, retry_events, meta, seat, MODEL_ID, 0.1))
            await asyncio.sleep(0.03)
            assert not retry.done() and len(calls) == 1
            release.set()
            result = await asyncio.wait_for(retry, 2)
            assert len(calls) == 1
            assert result["meta"]["start_time"] is None
            now[0] = 3000
            game_id2, events2, meta2, seat2 = await server_app.resolve_game_data(content=events)
            again = await server_app._run_review_with_cache(game_id2, events2, meta2, seat2, MODEL_ID, 0.1)
            assert game_id2 == game_id and again == result and len(calls) == 1
            assert (await server_app.resolve_game_data(paipu=game_id))[2]["start_time"] is None
            assert not server_app._review_tasks
        finally:
            release.set()

    asyncio.run(scenario())


def test_real_metadata_start_time_remains_part_of_cache_identity(monkeypatch, tmp_path):
    async def scenario():
        monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
        monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(server_app, "_review_tasks", set())
        monkeypatch.setattr(server_app, "_MEMORY_LOGS", {})
        monkeypatch.setattr(server_app, "resolve_engine_cache_info", lambda _: (MODEL_ID, f"{REVIEW_SCHEMA}_{SHA}"))
        calls = []

        def review(_events, _seat, _manager, **kwargs):
            calls.append(kwargs["meta"]["start_time"])
            return {**valid_result(), "meta": kwargs["meta"]}

        monkeypatch.setattr(server_app, "review_game", review)
        first = {"head": {"uuid": "external", "start_time": 1234, "accounts": [], "result": {}, "config": {}}, "actions": []}
        second = copy.deepcopy(first)
        second["head"]["start_time"] = 5678
        results = []
        for record in (first, second, first):
            game_id, events, meta, seat = await server_app.resolve_game_data(content=record)
            results.append(await server_app._run_review_with_cache(game_id, events, meta, seat, MODEL_ID, 0.1))
        assert [result["meta"]["start_time"] for result in results] == [1234, 5678, 1234]
        assert calls == [1234, 5678]
        signature = f"{REVIEW_SCHEMA}_{SHA}"
        assert server_app.review_cache_key("upload-game", [], {"start_time": 1234}, 0, MODEL_ID, 0.1, signature) != server_app.review_cache_key("upload-game", [], {"start_time": 5678}, 0, MODEL_ID, 0.1, signature)

    asyncio.run(scenario())


def test_cache_replacement_is_atomic(monkeypatch, tmp_path):
    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    server_app.save_cached_review("test", {"version": "old"})
    started, release = threading.Event(), threading.Event()
    original_dump = json.dump

    def delayed_dump(value, file, **kwargs):
        file.write('{"partial":')
        file.flush()
        started.set()
        assert release.wait(5)
        file.seek(0)
        file.truncate()
        original_dump(value, file, **kwargs)

    monkeypatch.setattr(server_app.json, "dump", delayed_dump)
    writer = threading.Thread(target=server_app.save_cached_review, args=("test", {"version": "new"}))
    writer.start()
    try:
        assert started.wait(2)
        assert server_app.get_cached_review("test") == {"version": "old"}
    finally:
        release.set()
        writer.join(timeout=5)
    assert not writer.is_alive()
    assert server_app.get_cached_review("test") == {"version": "new"}
    assert not list(tmp_path.glob("*.tmp"))


def test_cleanup_only_old_review_temps_and_counts_survivors(monkeypatch, tmp_path, caplog):
    reviews, records, cache = (tmp_path / name for name in ("reviews", "records", "cache"))
    for directory in (reviews, records, cache):
        directory.mkdir()
    monkeypatch.setattr(server_app, "REVIEWS_DIR", reviews)
    monkeypatch.setattr(server_app, "RECORDS_DIR", records)
    monkeypatch.setattr(server_app, "CACHE_DIR", cache)
    old_temp = reviews / ".review-abcdefgh.tmp"
    active = reviews / ".review-active01.tmp"
    young = reviews / ".review-young001.tmp"
    unrelated = reviews / ".review-incorrect-length.tmp"
    other_dir = records / ".review-abcdefgh.tmp"
    recent_json = reviews / "recent.json"
    for path, size in ((old_temp, 5), (active, 250_000), (young, 350_000), (unrelated, 13), (other_dir, 17), (recent_json, 400_000)):
        path.write_bytes(b"x" * size)
    old = time.time() - 8 * 86400
    for path in (old_temp, active, unrelated, other_dir):
        os.utime(path, (old, old))
    monkeypatch.setattr(server_app, "_active_review_temp_files", {active})
    caplog.set_level("INFO", logger="majsoul-analyzer")
    outcome = server_app.cleanup_old_caches(max_days=7, max_total_mb=100)
    assert outcome == {"cleaned_count": 1, "cleaned_bytes": 5}
    assert not old_temp.exists()
    assert all(path.exists() for path in (active, young, unrelated, other_dir, recent_json))
    expected_mb = (250_000 + 350_000 + 400_000) / (1024 * 1024)
    assert f"{expected_mb:.2f} MB / 上限" in caplog.text


@pytest.mark.parametrize("phase", ["expiration", "quota"])
def test_cleanup_does_not_delete_newly_replaced_review(monkeypatch, tmp_path, phase):
    reviews, records, cache = (tmp_path / name for name in ("reviews", "records", "cache"))
    for directory in (reviews, records, cache):
        directory.mkdir()
    monkeypatch.setattr(server_app, "REVIEWS_DIR", reviews)
    monkeypatch.setattr(server_app, "RECORDS_DIR", records)
    monkeypatch.setattr(server_app, "CACHE_DIR", cache)
    server_app.save_cached_review("test", {"version": "old"})
    target = reviews / "test.json"
    if phase == "expiration":
        old_time = time.time() - 8 * 86400
        os.utime(target, (old_time, old_time))
    inspected, resume = threading.Event(), threading.Event()
    original_remove = server_app._remove_review_cache_if_unchanged
    outcomes = []

    def delayed_remove(path, observed):
        inspected.set()
        if not resume.wait(5):
            raise RuntimeError("test did not resume cleanup")
        return original_remove(path, observed)

    monkeypatch.setattr(server_app, "_remove_review_cache_if_unchanged", delayed_remove)
    cleaner = threading.Thread(target=lambda: outcomes.append(server_app.cleanup_old_caches(
        max_days=7, max_total_mb=100 if phase == "expiration" else 0,
    )))
    cleaner.start()
    try:
        assert inspected.wait(2)
        server_app.save_cached_review("test", {"version": "new replacement"})
    finally:
        resume.set()
        cleaner.join(timeout=5)
    assert not cleaner.is_alive()
    assert outcomes == [{"cleaned_count": 0, "cleaned_bytes": 0}]
    assert server_app.get_cached_review("test") == {"version": "new replacement"}


def test_cleanup_quota_counts_young_temp_without_deleting_it(monkeypatch, tmp_path):
    reviews, records, cache = (tmp_path / name for name in ("reviews", "records", "cache"))
    for directory in (reviews, records, cache):
        directory.mkdir()
    monkeypatch.setattr(server_app, "REVIEWS_DIR", reviews)
    monkeypatch.setattr(server_app, "RECORDS_DIR", records)
    monkeypatch.setattr(server_app, "CACHE_DIR", cache)
    temp = reviews / ".review-young001.tmp"
    review = reviews / "review.json"
    temp.write_bytes(b"x" * 200)
    review.write_bytes(b"y" * 200)
    outcome = server_app.cleanup_old_caches(max_days=7, max_total_mb=300 / (1024 * 1024))
    assert temp.exists() and not review.exists()
    assert outcome == {"cleaned_count": 1, "cleaned_bytes": 200}


def test_cleanup_does_not_remove_active_temp_even_with_old_mtime(monkeypatch, tmp_path):
    reviews, records, cache = (tmp_path / name for name in ("reviews", "records", "cache"))
    for directory in (reviews, records, cache):
        directory.mkdir()
    monkeypatch.setattr(server_app, "REVIEWS_DIR", reviews)
    monkeypatch.setattr(server_app, "RECORDS_DIR", records)
    monkeypatch.setattr(server_app, "CACHE_DIR", cache)
    entered, release = threading.Event(), threading.Event()
    original_dump = json.dump

    def delayed_dump(value, file, **kwargs):
        file.write("{")
        file.flush()
        old = time.time() - 8 * 86400
        os.utime(file.name, (old, old))
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test did not release writer")
        file.seek(0)
        file.truncate()
        original_dump(value, file, **kwargs)

    monkeypatch.setattr(server_app.json, "dump", delayed_dump)
    writer = threading.Thread(target=server_app.save_cached_review, args=("test", {"version": "new"}))
    writer.start()
    try:
        assert entered.wait(2)
        temps = list(reviews.glob(".review-????????.tmp"))
        assert len(temps) == 1
        server_app.cleanup_old_caches(max_days=7, max_total_mb=0)
        assert temps[0].exists()
    finally:
        release.set()
        writer.join(timeout=5)
    assert not writer.is_alive()
    assert server_app.get_cached_review("test") == {"version": "new"}
    assert not server_app._active_review_temp_files


def test_concurrent_cache_reader_does_not_block_atomic_replace(monkeypatch, tmp_path):
    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    server_app.save_cached_review("test", {"version": "old"})
    reading, release = threading.Event(), threading.Event()
    original_read = Path.read_text
    results = []

    def delayed_read(path, *args, **kwargs):
        if path == tmp_path / "test.json":
            with path.open(encoding="utf8") as file:
                reading.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release reader")
                return file.read()
        return original_read(path, *args, **kwargs)

    def reader():
        results.append(server_app.get_cached_review("test"))

    monkeypatch.setattr(Path, "read_text", delayed_read)
    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=server_app.save_cached_review, args=("test", {"version": "new"}))
    reader_thread.start()
    try:
        assert reading.wait(2)
        writer_thread.start()
        time.sleep(0.03)
        assert writer_thread.is_alive()
    finally:
        release.set()
        reader_thread.join(timeout=5)
        writer_thread.join(timeout=5)
    assert not reader_thread.is_alive() and not writer_thread.is_alive()
    assert results == [{"version": "old"}]
    assert server_app.get_cached_review("test") == {"version": "new"}


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
@pytest.mark.parametrize("open_mode", ["deny_delete", "ordinary"])
def test_windows_external_reader_sharing_violation_is_retried(monkeypatch, tmp_path, open_mode):
    import ctypes
    from ctypes import wintypes

    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    server_app.save_cached_review("test", {"version": "old"})
    target = tmp_path / "test.json"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    # Permit reading/writing, but deny deletion/rename of the old file.
    handle, ordinary_file = None, None
    if open_mode == "ordinary":
        ordinary_file = target.open(encoding="utf8")
    else:
        handle = create_file(str(target), 0x80000000, 1 | 2, None, 3, 0x80, None)
        assert handle != ctypes.c_void_p(-1).value
    real_replace = Path.replace
    collision = threading.Event()

    def observed_replace(source, destination):
        try:
            return real_replace(source, destination)
        except OSError as error:
            if getattr(error, "winerror", None) in (5, 32, 33):
                collision.set()
            raise

    monkeypatch.setattr(Path, "replace", observed_replace)
    writer = threading.Thread(target=server_app.save_cached_review, args=("test", {"version": "new"}))
    writer.start()
    try:
        assert collision.wait(2)
    finally:
        if ordinary_file is not None:
            ordinary_file.close()
        if handle is not None:
            close_handle(handle)
        writer.join(timeout=5)
    assert not writer.is_alive()
    assert server_app.get_cached_review("test") == {"version": "new"}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_cache_replace_retries_windows_sharing_violation(monkeypatch, tmp_path, winerror):
    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    server_app.save_cached_review("test", {"version": "old"})
    replace = Path.replace
    attempts = []

    def flaky_replace(source, target):
        attempts.append(1)
        if len(attempts) < 3:
            error = PermissionError(13, "sharing violation")
            error.winerror = winerror
            raise error
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    server_app.save_cached_review("test", {"version": "new"})
    assert len(attempts) == 3
    assert server_app.get_cached_review("test") == {"version": "new"}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("winerror", [5, 32])
def test_cache_replace_persistent_failure_keeps_old_file(monkeypatch, tmp_path, caplog, winerror):
    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    server_app.save_cached_review("test", {"version": "old"})
    attempts = []

    def blocked_replace(source, target):
        attempts.append(1)
        error = PermissionError(13, "sharing violation")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(Path, "replace", blocked_replace)
    server_app.save_cached_review("test", {"version": "new"})
    assert len(attempts) == 5
    assert server_app.get_cached_review("test") == {"version": "old"}
    assert not list(tmp_path.glob("*.tmp"))
    assert "Failed to save review cache" in caplog.text


@pytest.mark.parametrize("route", ["paipu", "json", "demo", "stream"])
@pytest.mark.parametrize("caller", ["pages_proxy", "local"])
def test_analysis_responses_hide_engine_paths_without_mutating_cache(monkeypatch, tmp_path, route, caller):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "synthetic-api-token")
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://majsoul.ccwu.cc")
    monkeypatch.setattr(server_app, "REVIEWS_DIR", tmp_path)
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())
    monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
    monkeypatch.setattr(server_app, "resolve_engine_cache_info", lambda _: (MODEL_ID, f"{REVIEW_SCHEMA}_{SHA}"))
    private_path = "/internal/synthetic/model.pth"
    calls = []

    async def resolve(**_kwargs):
        return "same", [{"type": "start_game"}], {"names": ["A", "B", "C", "D"]}, 0

    def review(*args, **kwargs):
        calls.append(True)
        result = valid_result()
        result["engine"].update(model_path=private_path, models_dir="/internal/synthetic", private_debug={"path": private_path})
        result["meta"] = {"label": "preserved"}
        return result

    monkeypatch.setattr(server_app, "resolve_game_data", resolve)
    monkeypatch.setattr(server_app, "review_game", review)
    if caller == "pages_proxy":
        client = TestClient(server_app.app, base_url="http://api.example", client=("203.0.113.5", 12345))
        headers = {"X-Api-Token": "synthetic-api-token", "Origin": "https://majsoul.ccwu.cc", "User-Agent": "majsoul-analyzer-pages/1.0"}
    else:
        client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
        headers = {"X-Api-Token": "synthetic-api-token"}
    payload = {"paipu": "same"} if route in ("paipu", "stream") else ({"content": [{"type": "start_game"}]} if route == "json" else {})
    for attempt in range(2):
        response = client.post(f"/api/analyze/{route}", json=payload, headers=headers)
        assert response.status_code == 200
        if route == "stream":
            messages = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]
            result = next(message["result"] for message in messages if message["type"] == "complete")
        else:
            result = response.json()
        assert private_path not in response.text
        assert "models_dir" not in result["engine"]
        assert "model_path" not in result["engine"]
        assert "private_debug" not in result["engine"]
        assert valid_review_source(result, SHA)
        assert result["meta"] == {"label": "preserved"}
        assert len(calls) == 1
    stored = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf8"))
    assert stored["engine"]["model_path"] == private_path
    assert stored["engine"]["private_debug"]["path"] == private_path


def test_public_review_redaction_does_not_modify_original():
    original = valid_result()
    original["engine"]["model_path"] = "/internal/synthetic/model.pth"
    public = server_app._public_review_result(original)
    assert public is not original and public["engine"] is not original["engine"]
    assert "model_path" not in public["engine"]
    assert original["engine"]["model_path"] == "/internal/synthetic/model.pth"
    assert valid_review_source(public, SHA)


def test_public_status_excludes_private_state_and_retains_model_attestation(monkeypatch):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "synthetic-api-token")
    monkeypatch.delenv("MAJSOUL_ALLOWED_ORIGINS", raising=False)
    private_path = "/private/synthetic/model.pth"
    engine = {
        "has_mortal_weights": True,
        "active_engine": "mortal",
        "weight_sha256": SHA,
        "review_schema": REVIEW_SCHEMA,
        "device": "CUDA:0",
        "model_path": private_path,
        "models_dir": "/private/synthetic",
        "load_error": "synthetic-password /private/synthetic",
        "available_models": [{"id": MODEL_ID, "name": "Mortal", "path": private_path}],
    }
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(status=lambda: copy.deepcopy(engine), selected_model=MODEL_ID))
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: [{}, {}])
    monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "error", "error_msg": "synthetic-password /private/synthetic"}})
    remote = TestClient(server_app.app, base_url="http://api.example", client=("203.0.113.5", 12345))
    public = remote.get("/api/status")
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store"
    assert {"authorization", "x-api-token"} <= {value.strip().lower() for value in public.headers["vary"].split(",")}
    status = public.json()
    assert set(status) == {"ok", "engine"}
    assert status["engine"]["has_mortal_weights"] is True
    assert status["engine"]["active_engine"] == "mortal"
    assert status["engine"]["weight_sha256"] == SHA
    assert status["engine"]["review_schema"] == REVIEW_SCHEMA
    assert status["engine"]["device"] == "CUDA:0"
    assert all("path" not in model for model in status["engine"]["available_models"])
    for forbidden in ("/private/", "synthetic-password", "account_pool_count", "logged_in", "load_error", "model_download"):
        assert forbidden not in public.text

    trusted_response = remote.get("/api/status", headers={"X-Api-Token": "synthetic-api-token"})
    assert trusted_response.headers["cache-control"] == "no-store"
    assert {"authorization", "x-api-token"} <= {value.strip().lower() for value in trusted_response.headers["vary"].split(",")}
    trusted = trusted_response.json()
    assert trusted["majsoul"]["account_pool_count"] == 2
    assert trusted["engine"]["model_path"] == private_path
    assert "synthetic-password" not in json.dumps(trusted)
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://review.example")
    cors_status = remote.get("/api/status", headers={"Origin": "https://review.example", "X-Api-Token": "synthetic-api-token"})
    assert cors_status.headers["cache-control"] == "no-store"
    assert {"origin", "authorization", "x-api-token"} <= {value.strip().lower() for value in cors_status.headers["vary"].split(",")}
    assert remote.get("/api/status", headers={"Origin": "https://evil.example"}).status_code == 403
    assert remote.get("/api/records/local").status_code == 401

    # Without a configured token, only a direct same-origin loopback caller is trusted.
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    local = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    assert local.get("/api/status").json()["majsoul"]["account_pool_count"] == 2
    assert "majsoul" not in remote.get("/api/status").json()


def test_mixed_account_failures_are_private_in_json_and_sse(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "synthetic-api-token")
    monkeypatch.delenv("MAJSOUL_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(server_app, "_ms_client_lock", asyncio.Lock())
    accounts = [
        {"account": "first@example.invalid", "nickname": "PrivateNickOne", "account_id": 1234501,
         "password": "synthetic-password-one", "access_token": "synthetic-token-one"},
        {"account": "second@example.invalid", "nickname": "PrivateNickTwo", "account_id": 1234502,
         "password": "synthetic-password-two", "access_token": "synthetic-token-two"},
    ]
    fake = SimpleNamespace(nickname=None, account_id=None, access_token=None, logged_in=False)

    async def close():
        fake.logged_in = False

    async def login(acc):
        if acc["account_id"] == 1234502:
            raise RuntimeError("connect failed synthetic-password-two synthetic-token-two")
        fake.nickname, fake.account_id, fake.access_token = acc["nickname"], acc["account_id"], acc["access_token"]
        fake.logged_in = True

    async def fetch(_uuid):
        raise MajsoulError("quota reached", code=540)

    fake.close, fake.fetch_record = close, fetch
    monkeypatch.setattr(server_app, "ms_client", fake)
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: accounts)
    monkeypatch.setattr(server_app, "login_with_account_spec", login)
    monkeypatch.setattr(server_app, "is_client_logged_in_as", lambda _: False)
    monkeypatch.setattr(server_app, "parse_paipu", lambda _: ("synthetic-game", None))
    monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
    client = TestClient(server_app.app, base_url="http://api.example", client=("203.0.113.5", 12345))
    headers = {"X-Api-Token": "synthetic-api-token"}
    json_response = client.post("/api/analyze/paipu", json={"paipu": "synthetic-game"}, headers=headers)
    stream_response = client.post("/api/analyze/stream", json={"paipu": "synthetic-game"}, headers=headers)
    assert json_response.status_code == 400
    assert stream_response.status_code == 200
    assert '"type": "error"' in stream_response.text
    for response in (json_response, stream_response):
        assert "额度已满" in response.text
        for forbidden in ("PrivateNickOne", "PrivateNickTwo", "1234501", "1234502", "example.invalid", "synthetic-password", "synthetic-token"):
            assert forbidden not in response.text
    assert "PrivateNickOne" in caplog.text and "PrivateNickTwo" in caplog.text
    assert "synthetic-password" not in caplog.text and "synthetic-token" not in caplog.text


def test_overlapping_credentials_are_fully_redacted():
    from server.account_pool import _redact_error

    short, long = "synthetic", "synthetic-token-long"
    message = f"first={long} second={short}"
    redacted = _redact_error(RuntimeError(message), short, long)
    assert redacted == "first=[已隐藏] second=[已隐藏]"
    assert long not in redacted and "-token-long" not in redacted


def test_account_login_errors_never_echo_credentials(monkeypatch, caplog):
    from fastapi import HTTPException

    password, token = "synthetic-password-long", "synthetic-token-long"
    fake = SimpleNamespace(logged_in=False, _ws=None, access_token=None)

    async def close():
        pass

    async def connect():
        pass

    async def login_token(_token):
        raise MajsoulError(f"login refused {password} {token}")

    async def login_spec(_account):
        raise RuntimeError(f"connection refused {password} {token}")

    fake.close, fake.connect, fake.login_token = close, connect, login_token
    monkeypatch.setattr(server_app, "ms_client", fake)
    monkeypatch.setattr(server_app, "_ms_client_lock", asyncio.Lock())
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: [{"nickname": "PrivateNick", "password": password, "access_token": token}])
    monkeypatch.setattr(server_app, "login_with_account_spec", login_spec)

    async def scenario():
        with pytest.raises(HTTPException) as login_error:
            await server_app.api_login(server_app.LoginReq(password=password, access_token=f"  {token}  ", remember=False))
        with pytest.raises(HTTPException) as pool_error:
            await server_app.ensure_ms_logged_in()
        for error in (login_error.value, pool_error.value):
            assert password not in error.detail and token not in error.detail
            assert "PrivateNick" not in error.detail

    asyncio.run(scenario())
    assert "PrivateNick" in caplog.text
    assert password not in caplog.text and token not in caplog.text


def test_download_failures_do_not_echo_internal_error(monkeypatch):
    from fastapi import HTTPException

    async def scenario():
        state = {"status": "downloading", "progress_pct": 5, "error_msg": None}
        monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: state})
        response = await server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"))
        stream = response.body_iterator
        assert event_from_chunk(await anext(stream))["type"] == "progress"
        waiting = asyncio.create_task(server_app._wait_for_model_download(MODEL_ID))
        await asyncio.sleep(0)
        state.update(status="error", error_msg="synthetic-token /private/model.pth")
        error_event = event_from_chunk(await asyncio.wait_for(anext(stream), timeout=2))
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(waiting, timeout=2)
        assert error_event["type"] == "error"
        for detail in (error_event["detail"], error.value.detail):
            assert "下载失败" in detail
            assert "synthetic-token" not in detail and "/private/" not in detail
        await stream.aclose()

    asyncio.run(scenario())


def test_safe_error_context_records_fixed_location_and_code_without_message():
    try:
        try:
            raise MajsoulError("synthetic-password /private/path private-user", code=540)
        except MajsoulError as cause:
            raise RuntimeError("synthetic-token secret-game") from cause
    except RuntimeError as error:
        context = server_app._safe_error_context(error)
    assert "RuntimeError" in context
    assert all(secret not in context for secret in ("synthetic-password", "synthetic-token", "/private/path", "private-user", "secret-game"))
    try:
        raise MajsoulError("synthetic-token", code=540)
    except MajsoulError as error:
        context = server_app._safe_error_context(error)
    assert "MajsoulError code=540" in context
    assert "synthetic-token" not in context


def test_invalid_paipu_does_not_echo_sensitive_input(monkeypatch, caplog):
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    sensitive = "synthetic-user-token-in-invalid-paipu"
    for route in ("paipu", "stream"):
        response = client.post(f"/api/analyze/{route}", json={"paipu": sensitive})
        assert "无法识别的牌谱 ID" in response.text
        assert sensitive not in response.text
    assert sensitive not in caplog.text
    assert "server/app.py:" in caplog.text


def test_unexpected_stream_error_does_not_echo_exception(monkeypatch, caplog):
    async def broken(**kwargs):
        raise RuntimeError("private-user synthetic-password /private/path")

    monkeypatch.setattr(server_app, "resolve_game_data", broken)
    monkeypatch.setattr(server_app, "models_download_state", {MODEL_ID: {"status": "idle"}})
    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    response = client.post("/api/analyze/stream", json={"paipu": "demo"})
    assert '"type": "error"' in response.text
    assert "private-user" not in response.text and "synthetic-password" not in response.text
    assert "/private/path" not in response.text
    assert "RuntimeError" in caplog.text and "server/app.py:" in caplog.text
    assert "private-user" not in caplog.text and "synthetic-password" not in caplog.text
    assert "/private/path" not in caplog.text
