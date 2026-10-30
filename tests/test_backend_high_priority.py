"""Controlled regressions for review cancellation and public API privacy."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from server import app as server_app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA, valid_review_source
from server.majsoul.client import MajsoulError


SHA = MODEL_SHA256


def valid_result(sha=SHA):
    return {
        "engine": {
            "type": "mortal",
            "id": MODEL_ID,
            "weight_file": MODEL_FILE,
            "weight_sha256": sha,
            "version": 4,
            "steps": None, "revision": MODEL_REVISION,
            "q_source": "neural_network",
            "review_schema": REVIEW_SCHEMA,
        },
        "summary": {},
        "target_actor": 0,
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
def review_environment(monkeypatch):
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
        status=lambda: {}, selected_model=MODEL_ID,
    ))

    async def resolve(paipu=None, content=None, target_actor=None):
        name = paipu or "first"
        return name, [{"name": name}], {"names": ["A", "B", "C", "D"]}, 0

    monkeypatch.setattr(server_app, "resolve_game_data", resolve)


async def nonstream(name):
    return await server_app._run_review(
        name, [{"name": name}], {"names": ["A", "B", "C", "D"]}, 0, MODEL_ID, 0.1
    )


def event_from_chunk(chunk):
    return json.loads(chunk.removeprefix("data: ").strip())


async def wait_started(control, name):
    await asyncio.wait_for(control.started[name].wait(), timeout=2)


async def assert_queued_and_finish(control, owner):
    """Both follow-up requests run afresh after the detached worker finishes."""
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
    assert control.calls.count("first") == 2
    assert control.max_active == 1
    control.release["other"].set()
    assert (await asyncio.wait_for(other, timeout=2))["game_id"] == "other"
    await asyncio.sleep(0)
    assert not server_app._review_tasks
    assert not server_app.review_semaphore.locked()
    if owner is not None:
        assert owner.cancelled()


def test_stream_disconnect_keeps_permit_until_worker_finishes(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop(), emit_progress=True)
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
        try:
            request = MemoryASGIRequest("/api/analyze/stream", {"paipu": "first"})
            task = request.start()
            await wait_started(control, "first")
            progress = await asyncio.wait_for(request.wait_for_sse_event("progress", percent=35), 2)
            assert progress["message"] == "正在检讨本局"
            assert any(message["type"] == "http.response.start" and message["status"] == 200 for message in request.sent)
            assert not any(b'"type": "complete"' in message.get("body", b"") for message in request.sent)
            assert server_app.review_semaphore.locked() and len(server_app._review_tasks) == 1
            request.disconnect()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
            await assert_queued_and_finish(control, None)
        finally:
            control.release_all()
    asyncio.run(scenario())


def test_disconnect_during_asgi_send_closes_listener_but_not_worker(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop(), emit_progress=True)
        monkeypatch.setattr(server_app, "review_game", control.review)
        blocked_send = asyncio.Event()
        sender = None
        try:
            response = await asyncio.wait_for(
                server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first")), 2
            )
            assert not control.calls and not server_app._review_tasks

            async def send(message):
                body = message.get("body", b"")
                if message["type"] == "http.response.body" and b'"percent": 35' in body:
                    blocked_send.set()
                    await asyncio.Event().wait()

            sender = asyncio.create_task(response.stream_response(send))
            await wait_started(control, "first")
            await asyncio.wait_for(blocked_send.wait(), 2)
            sender.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sender
            assert response.body_iterator.ag_frame is None
            assert len(server_app._review_tasks) == 1 and server_app.review_semaphore.locked()
            await assert_queued_and_finish(control, None)
        finally:
            control.release_all()
            if sender is not None and not sender.done():
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
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


def test_stream_and_nonstream_complete_normally(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop(), emit_progress=True)
        monkeypatch.setattr(server_app, "review_game", control.review)
        progress_seen = asyncio.Event()
        preparation = []
        get_mortal = server_app.engine_mgr.get_mortal
        resolve_game_data = server_app.resolve_game_data
        pending = None

        def tracked_get_mortal(model_id):
            preparation.append("model")
            return get_mortal(model_id)

        async def tracked_resolve(**kwargs):
            preparation.append("resolve")
            return await resolve_game_data(**kwargs)

        monkeypatch.setattr(server_app.engine_mgr, "get_mortal", tracked_get_mortal)
        monkeypatch.setattr(server_app, "resolve_game_data", tracked_resolve)

        async def consume(response):
            messages = []
            async for chunk in response.body_iterator:
                event = event_from_chunk(chunk)
                messages.append(event)
                if event.get("percent") == 35:
                    progress_seen.set()
            return messages

        try:
            response = await asyncio.wait_for(
                server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first")), 2
            )
            assert response.status_code == 200
            assert not preparation and not control.calls
            assert not server_app._review_tasks and not server_app.review_semaphore.locked()
            pending = asyncio.create_task(consume(response))
            await wait_started(control, "first")
            await asyncio.wait_for(progress_seen.wait(), 2)
            assert "model" in preparation and "resolve" in preparation
            assert not pending.done()
            control.release["first"].set()
            messages = await asyncio.wait_for(pending, 2)
            assert any(event.get("percent") == 35 for event in messages)
            assert next(event["result"] for event in messages if event["type"] == "complete")["game_id"] == "first"
            assert (await nonstream("first"))["game_id"] == "first"
            assert control.calls == ["first", "first"]
            await asyncio.sleep(0)
            assert not server_app._review_tasks
        finally:
            control.release_all()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_cancelling_queued_stream_never_starts_job(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
        queued_start = asyncio.Event()
        start_review_job = server_app._start_review_job

        async def track_start(**kwargs):
            if kwargs["game_id"] == "other":
                queued_start.set()
            return await start_review_job(**kwargs)

        monkeypatch.setattr(server_app, "_start_review_job", track_start)
        try:
            owner = asyncio.create_task(nonstream("first"))
            await wait_started(control, "first")
            request = MemoryASGIRequest("/api/analyze/stream", {"paipu": "other"})
            waiter = request.start()
            await asyncio.wait_for(request.wait_for_sse_event("queued"), 2)
            await asyncio.wait_for(queued_start.wait(), 2)
            assert not waiter.done() and not control.started["other"].is_set()
            assert server_app.review_semaphore.locked() and len(server_app._review_tasks) == 1
            request.disconnect()
            await asyncio.wait_for(asyncio.gather(waiter, return_exceptions=True), 2)
            assert server_app.review_semaphore.locked() and len(server_app._review_tasks) == 1
            control.release["first"].set()
            await asyncio.wait_for(owner, 2)
            await asyncio.sleep(0)
            assert control.calls == ["first"] and not server_app._review_tasks
            assert not server_app.review_semaphore.locked()
        finally:
            control.release_all()
    asyncio.run(scenario())


def test_shutdown_drains_detached_inference(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
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
            assert control.calls == ["first", "first"]
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
        self.sent_messages = asyncio.Queue()
        self.disconnected = False
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
        if self.disconnected:
            raise OSError("connection closed")
        self.sent.append(message)
        self.sent_messages.put_nowait(message)

    async def wait_for_sse_event(self, event_type, **expected):
        buffer = b""
        while True:
            message = await self.sent_messages.get()
            buffer += message.get("body", b"")
            while b"\n\n" in buffer:
                chunk, buffer = buffer.split(b"\n\n", 1)
                for line in chunk.splitlines():
                    if line.startswith(b"data: "):
                        event = json.loads(line.removeprefix(b"data: "))
                        if event.get("type") == event_type and all(event.get(key) == value for key, value in expected.items()):
                            return event

    def start(self):
        return asyncio.create_task(server_app.app(self.scope, self.receive, self.send))

    def disconnect(self):
        self.disconnected = True
        self.messages.put_nowait({"type": "http.disconnect"})


@pytest.mark.parametrize("route", ["paipu", "json", "demo"])
def test_asgi_nonstream_disconnect_while_queued_does_not_start_review(review_environment, monkeypatch, route):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
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
        monkeypatch.setattr(server_app, "model_download_state", {"status": "downloading"})
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


def test_asgi_nonstream_disconnect_after_start_preserves_worker(review_environment, monkeypatch):
    async def scenario():
        control = ControlledReviews(asyncio.get_running_loop())
        monkeypatch.setattr(server_app, "review_game", control.review)
        monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
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
        monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
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


def test_cancelling_job_itself_waits_for_thread_before_next_review(review_environment, monkeypatch):
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
            assert control.calls == ["first", "first"]
            await asyncio.sleep(0)
            assert not server_app._review_tasks
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_worker_cancelled_error_does_not_spin_or_leak():
    # Run in a subprocess so a regression cannot freeze the entire suite's
    # event loop and prevent an asyncio timeout from firing.
    script = textwrap.dedent("""
        import asyncio
        import pathlib
        from types import SimpleNamespace

        original_is_file = pathlib.Path.is_file
        pathlib.Path.is_file = lambda self: False if self.name in ('config.json', 'config.local.json') else original_is_file(self)
        from server import app
        from server.engine.model_policy import MODEL_ID, MODEL_SHA256 as SHA
        pathlib.Path.is_file = original_is_file

        app.review_semaphore = asyncio.Semaphore(1)
        app._review_tasks = set()
        app.engine_mgr = SimpleNamespace(get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None))
        def cancelled_worker(*args, **kwargs):
            raise asyncio.CancelledError('worker failed')
        app.review_game = cancelled_worker

        async def scenario():
            try:
                await asyncio.wait_for(app._run_review('game', [], {}, 0, MODEL_ID, 0.1), 3)
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError('worker cancellation was not propagated')
            await asyncio.sleep(0)
            assert not app._review_tasks
            assert not app.review_semaphore.locked()
            await asyncio.wait_for(asyncio.sleep(0.01), 1)

        asyncio.run(scenario())
    """)
    env = {key: value for key, value in os.environ.items() if not key.startswith("MAJSOUL_")}
    process = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        encoding="utf8", errors="replace", env=env, timeout=20,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_detached_invalid_result_is_rejected_and_next_request_runs(review_environment, monkeypatch):
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
            assert not server_app._review_tasks
            assert not server_app.review_semaphore.locked()
            # A later valid attempt must run independently.
            assert (await asyncio.wait_for(nonstream("first"), timeout=2))["game_id"] == "first"
            assert control.calls == ["first", "first"]
        finally:
            control.release_all()

    asyncio.run(scenario())


def test_mjai_disconnect_retry_reanalyses_without_inventing_start_time(monkeypatch):
    async def scenario():
        now = [1000]
        monkeypatch.setattr(server_app.time, "time", lambda: now[0])
        monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(server_app, "_review_tasks", set())
        monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
        monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
            get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
        ))
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
            request = MemoryASGIRequest("/api/analyze/stream", {"content": events})
            owner = request.start()
            await asyncio.wait_for(started.wait(), 2)
            first_job = next(iter(server_app._review_tasks))
            request.disconnect()
            await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 2)
            now[0] = 2000
            game_id, retry_events, meta, seat = await server_app.resolve_game_data(content=events)
            assert meta["start_time"] is None
            retry = asyncio.create_task(server_app._run_review(game_id, retry_events, meta, seat, MODEL_ID, 0.1))
            await asyncio.sleep(0.03)
            assert not retry.done() and len(calls) == 1
            release.set()
            first_result = await asyncio.wait_for(first_job, 2)
            result = await asyncio.wait_for(retry, 2)
            assert len(calls) == 2
            assert result["meta"]["start_time"] is None
            assert first_result["game_id"] != game_id
            now[0] = 3000
            game_id2, events2, meta2, seat2 = await server_app.resolve_game_data(content=events)
            again = await server_app._run_review(game_id2, events2, meta2, seat2, MODEL_ID, 0.1)
            assert game_id2 != game_id and again["game_id"] == game_id2
            assert again["meta"]["start_time"] is None and len(calls) == 3
            assert not server_app._review_tasks
        finally:
            release.set()

    asyncio.run(scenario())


def test_uploaded_metadata_start_time_is_preserved_on_every_review(monkeypatch):
    async def scenario():
        monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(server_app, "_review_tasks", set())
        monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
            get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
        ))
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
            results.append(await server_app._run_review(game_id, events, meta, seat, MODEL_ID, 0.1))
        assert [result["meta"]["start_time"] for result in results] == [1234, 5678, 1234]
        assert calls == [1234, 5678, 1234]
        assert len({result["game_id"] for result in results}) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["paipu", "json", "demo", "stream"])
@pytest.mark.parametrize("caller", ["pages_proxy", "local"])
def test_analysis_responses_hide_engine_paths_without_mutating_results(monkeypatch, route, caller):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "synthetic-api-token")
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://majsoul.ccwu.cc")
    monkeypatch.setattr(server_app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(server_app, "_review_tasks", set())
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
    ))
    private_path = "/internal/synthetic/model.pth"
    results = []

    async def resolve(**_kwargs):
        return "same", [{"type": "start_game"}], {"names": ["A", "B", "C", "D"]}, 0

    def review(*args, **kwargs):
        result = valid_result()
        result["engine"].update(model_path=private_path, models_dir="/internal/synthetic", private_debug={"path": private_path})
        result["meta"] = {"label": "preserved"}
        results.append(result)
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
        assert len(results) == attempt + 1
        assert results[-1]["engine"]["model_path"] == private_path
        assert results[-1]["engine"]["private_debug"]["path"] == private_path


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
    monkeypatch.setattr(server_app, "model_download_state", {"status": "error", "error_msg": "synthetic-password /private/synthetic"})
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
    assert "available_models" not in status["engine"]
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
    import server.account_pool as pool

    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    monkeypatch.setattr(server_app, "LEGACY_RECORDS_DIRS", ())
    monkeypatch.setattr(server_app, "_ms_client_lock", asyncio.Lock())
    monkeypatch.setattr(pool, "_rate_limited_until", {})
    accounts = [
        {"account": "first@example.invalid", "nickname": "PrivateNickOne", "account_id": 1234501,
         "password": "synthetic-password-one", "access_token": "synthetic-token-one"},
        {"account": "second@example.invalid", "nickname": "PrivateNickTwo", "account_id": 1234502,
         "password": "synthetic-password-two", "access_token": "synthetic-token-two"},
    ]
    fake = SimpleNamespace(nickname=None, account_id=None, access_token=None, logged_in=False, _ws=None)

    async def close():
        fake.logged_in = False
        fake._ws = None

    async def login(acc):
        if acc["account_id"] == 1234502:
            raise RuntimeError("connect failed synthetic-password-two synthetic-token-two")
        fake.nickname, fake.account_id, fake.access_token = acc["nickname"], acc["account_id"], acc["access_token"]
        fake.logged_in = True
        fake._ws = object()

    async def fetch(_uuid):
        raise MajsoulError("quota reached", code=540)

    fake.close, fake.fetch_record = close, fetch
    monkeypatch.setattr(server_app, "ms_client", fake)
    monkeypatch.setattr(server_app, "get_all_configured_accounts", lambda: accounts)
    monkeypatch.setattr(server_app, "login_with_account_spec", login)
    monkeypatch.setattr(server_app, "is_client_logged_in_as", lambda acc: fake.logged_in and fake.account_id == acc["account_id"])
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
    ))
    client = TestClient(server_app.app, base_url="http://api.example", client=("203.0.113.5", 12345))
    headers = {"X-Api-Token": "synthetic-api-token"}
    game_id = "241026-11111111-2222-3333-4444-555555555555"
    json_response = client.post("/api/analyze/paipu", json={"paipu": game_id}, headers=headers)
    stream_response = client.post("/api/analyze/stream", json={"paipu": game_id}, headers=headers)
    assert json_response.status_code == 400
    assert stream_response.status_code == 200
    assert '"type": "error"' in stream_response.text
    for response in (json_response, stream_response):
        assert "额度已满" in response.text
        for forbidden in ("PrivateNickOne", "PrivateNickTwo", "1234501", "1234502", "example.invalid", "synthetic-password", "synthetic-token"):
            assert forbidden not in response.text
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
    async def scenario():
        state = {"status": "downloading", "progress_pct": 5, "error_msg": None}
        monkeypatch.setattr(server_app, "model_download_state", state)
        entered = asyncio.Event()
        wait_for_download = server_app._wait_for_model_download

        async def tracked_wait(model_id):
            entered.set()
            await wait_for_download(model_id)

        def unavailable(_):
            raise RuntimeError("synthetic-token /private/model.onnx")

        monkeypatch.setattr(server_app, "_wait_for_model_download", tracked_wait)
        monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(get_mortal=unavailable))
        response = await asyncio.wait_for(
            server_app.api_analyze_stream(server_app.AnalyzeStreamReq(paipu="first"), None), 2
        )
        assert response.status_code == 200 and not entered.is_set()

        async def consume():
            return [event_from_chunk(chunk) async for chunk in response.body_iterator]

        waiting = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not waiting.done()
            state.update(status="error", error_msg="synthetic-token /private/model.onnx")
            messages = await asyncio.wait_for(waiting, timeout=3)
            errors = [event for event in messages if event["type"] == "error"]
            assert len(errors) == 1 and "下载失败" in errors[0]["detail"]
            assert not any(event["type"] == "complete" for event in messages)
            assert "synthetic-token" not in json.dumps(messages) and "/private/" not in json.dumps(messages)
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)

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
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
    ))
    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    sensitive = "synthetic-user-token-in-invalid-paipu"
    for route in ("paipu", "stream"):
        response = client.post(f"/api/analyze/{route}", json={"paipu": sensitive})
        assert "无法识别的牌谱 ID" in response.text
        assert sensitive not in response.text
    assert sensitive not in caplog.text


def test_unexpected_stream_error_does_not_echo_exception(monkeypatch, caplog):
    async def broken(**kwargs):
        raise RuntimeError("private-user synthetic-password /private/path")

    monkeypatch.setattr(server_app, "resolve_game_data", broken)
    monkeypatch.setattr(server_app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(server_app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
    ))
    client = TestClient(server_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    response = client.post("/api/analyze/stream", json={"paipu": "demo"})
    assert '"type": "error"' in response.text
    assert "private-user" not in response.text and "synthetic-password" not in response.text
    assert "/private/path" not in response.text
    assert "RuntimeError" in caplog.text and "server/app.py:" in caplog.text
    assert "private-user" not in caplog.text and "synthetic-password" not in caplog.text
    assert "/private/path" not in caplog.text
