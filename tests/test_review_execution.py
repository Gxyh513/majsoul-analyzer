"""Review requests execute independently, without persisted review results."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

import server.app as app
from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA


UUID = "241026-11111111-2222-3333-4444-555555555555"
SHA = MODEL_SHA256
SEAT = 1
EVENTS = [{"type": "start_game", "names": ["A", "B", "C", "D"]}]
META = {"uuid": UUID, "account_ids": [11, 22, 33, 44]}


def review():
    return {
        "target_actor": SEAT,
        "engine": {"type": "mortal", "id": MODEL_ID, "weight_file": MODEL_FILE,
                   "weight_sha256": SHA, "version": 4, "steps": None, "revision": MODEL_REVISION,
                   "q_source": "neural_network", "review_schema": REVIEW_SCHEMA},
        "summary": {"rating": 95},
        "kyokus": [{"steps": [{"event": EVENTS[0]}],
                    "decisions": [{"candidates": [{"label": 1, "raw_q": 0.5}]}]}],
    }


@pytest.fixture
def review_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path / "records")
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app, "review_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(app, "_review_tasks", set())
    monkeypatch.setattr(app, "model_download_state", {"status": "idle"})
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=SHA), MODEL_ID, None),
    ))
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
    assert "X-Review-Cache-Persisted" not in response.headers
    if route == "stream":
        messages = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
                    if line.startswith("data: ")]
        complete = [message for message in messages if message["type"] == "complete"]
        assert len(complete) == 1 and not [message for message in messages if message["type"] == "error"]
        assert "cache_persisted" not in complete[0]
        return complete[0]["result"]
    return response.json()


@pytest.mark.parametrize("route", ["paipu", "json", "demo", "stream"])
def test_repeated_api_requests_run_inference_each_time(review_environment, tmp_path, route):
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    assert request_result(client, route) == {**review(), "game_id": UUID}
    assert request_result(client, route) == {**review(), "game_id": UUID}
    assert review_environment.call_count == 2
    assert not (tmp_path / "reviews").exists()
    assert not (tmp_path / "metadata").exists()


def test_stream_send_metrics_wrap_the_actual_app(review_environment, monkeypatch, caplog):
    request_id = "1234567890abcdef1234567890abcdef"
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    with caplog.at_level("DEBUG", logger="majsoul-analyzer"):
        response = client.post("/api/analyze/stream", json=analyze_payload("stream"), headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    messages = [record.getMessage() for record in caplog.records]
    send_logs = [json.loads(message) for message in messages if '"事件":"SSE发送明细"' in message]
    assert [entry["阶段"] for entry in send_logs] == ["100%", "complete"]
    assert all(entry["请求编号"] == request_id and entry["状态"] == "完成" for entry in send_logs)
    assert all(entry["ASGI发送等待毫秒"] >= 0 for entry in send_logs)
    summary_logs = [json.loads(message) for message in messages if '"事件":"牌谱分析"' in message]
    assert len(summary_logs) == 1
    assert summary_logs[0]["请求编号"] == request_id
    assert summary_logs[0]["状态"] == "成功"
    events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
              if line.startswith("data: ")]
    reports = [event["performance"] for event in events if event["type"] == "performance"]
    assert len(reports) == 1
    report = reports[0]
    assert report["请求ID"] == request_id
    assert report["耗时_ms"]["complete ASGI发送开始时间"] == send_logs[1]["ASGI发送开始毫秒"]
    assert report["耗时_ms"]["complete ASGI发送完成时间"] == send_logs[1]["ASGI发送完成毫秒"]
    assert report["耗时_ms"]["complete ASGI发送等待耗时"] == send_logs[1]["ASGI发送等待毫秒"]
    assert 0 <= report["耗时_ms"]["complete ASGI发送开始时间"] <= report["耗时_ms"]["complete ASGI发送完成时间"]
    assert report["耗时_ms"]["complete Base64编码耗时"] is None
    assert report["数据大小_KB"]["Base64编码后大小"] is None
    assert report["状态"]["是否使用Base64"] is False
    assert events.index(next(event for event in events if event["type"] == "complete")) < events.index(
        next(event for event in events if event["type"] == "performance"))


def test_stream_compresses_only_when_client_negotiates(review_environment, monkeypatch):
    large = review()
    large["summary"]["description"] = "完整结果" * 20000
    monkeypatch.setattr(app, "review_game", lambda *args, **kwargs: copy.deepcopy(large))
    client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))

    def complete(accept_encoding, payload):
        response = client.post("/api/analyze/stream", json=payload, headers={"Accept-Encoding": accept_encoding})
        assert response.status_code == 200
        # TestClient transparently decodes Content-Encoding; raw wire bytes are covered at ASGI level.
        assert response.headers.get("Content-Encoding") == ("gzip" if accept_encoding == "gzip" else "identity")
        events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        assert any(event.get("type") == "progress" and event.get("percent") == 100 for event in events)
        completed = [event for event in events if event["type"] == "complete"]
        assert len(completed) == 1
        return completed[0]

    payload = analyze_payload("stream")
    identity = complete("identity", payload)
    refused = complete("gzip;q=0", payload)
    compressed = complete("gzip", {**payload, "accept_compression": "gzip+base64"})
    assert identity == refused == compressed == {"type": "complete", "result": {**large, "game_id": UUID}}


def test_start_review_job_returns_task_that_owns_permit(review_environment, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def blocked_review(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise RuntimeError("test did not release inference")
            return review()

        monkeypatch.setattr(app, "review_game", blocked_review)
        task = None
        try:
            task = await app._start_review_job(
                game_id=UUID, mjai_events=EVENTS, meta=META, seat=SEAT,
                engine=MODEL_ID, temperature=0.0,
            )
            assert isinstance(task, asyncio.Task)
            await asyncio.wait_for(started.wait(), 2)
            assert task in app._review_tasks and app.review_semaphore.locked()
            release.set()
            assert await asyncio.wait_for(task, 2) == {**review(), "game_id": UUID}
            await asyncio.sleep(0)
            assert not app._review_tasks and not app.review_semaphore.locked()
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_same_review_requests_run_separately_and_serially(review_environment, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = [asyncio.Event(), asyncio.Event()]
        release = [threading.Event(), threading.Event()]
        calls = []
        active = 0
        max_active = 0
        lock = threading.Lock()

        def blocked_review(*args, **kwargs):
            nonlocal active, max_active
            with lock:
                index = len(calls)
                calls.append(True)
                active += 1
                max_active = max(max_active, active)
            loop.call_soon_threadsafe(started[index].set)
            try:
                if not release[index].wait(5):
                    raise RuntimeError("test did not release inference")
                return review()
            finally:
                with lock:
                    active -= 1

        monkeypatch.setattr(app, "review_game", blocked_review)
        first = second = None
        try:
            first = asyncio.create_task(app._run_review(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.wait_for(started[0].wait(), 2)
            second = asyncio.create_task(app._run_review(UUID, EVENTS, META, SEAT, MODEL_ID, 0.0))
            await asyncio.sleep(0.03)
            assert not second.done() and calls == [True]
            release[0].set()
            assert await asyncio.wait_for(first, 2) == {**review(), "game_id": UUID}
            await asyncio.wait_for(started[1].wait(), 2)
            assert calls == [True, True] and max_active == 1
            release[1].set()
            assert await asyncio.wait_for(second, 2) == {**review(), "game_id": UUID}
            await asyncio.sleep(0)
            assert not app._review_tasks and not app.review_semaphore.locked()
        finally:
            for event in release:
                event.set()
            await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)

    asyncio.run(scenario())


def test_queued_stream_gets_its_own_progress_after_disconnect(review_environment, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = [asyncio.Event(), asyncio.Event()]
        release = [threading.Event(), threading.Event()]
        send_first_progress = threading.Event()
        queued = asyncio.Event()
        calls = []

        def progress_review(*args, **kwargs):
            index = len(calls)
            calls.append(index)
            loop.call_soon_threadsafe(started[index].set)
            if index == 0 and not send_first_progress.wait(5):
                raise RuntimeError("test did not release first progress")
            kwargs["progress_callback"](42 if index == 0 else 65, f"worker {index}")
            if not release[index].wait(5):
                raise RuntimeError("test did not release inference")
            return review()

        async def consume(response):
            messages = []
            async for chunk in response.body_iterator:
                message = json.loads(chunk.removeprefix("data: ").strip())
                messages.append(message)
                if message.get("type") == "queued":
                    queued.set()
            return messages

        monkeypatch.setattr(app, "review_game", progress_review)
        request = app.AnalyzeStreamReq(**analyze_payload("stream"))
        leader = follower = None
        try:
            leader_response = await asyncio.wait_for(app.api_analyze_stream(request), 2)
            assert not calls
            leader = asyncio.create_task(consume(leader_response))
            await asyncio.wait_for(started[0].wait(), 2)
            follower_response = await asyncio.wait_for(app.api_analyze_stream(request), 2)
            follower = asyncio.create_task(consume(follower_response))
            await asyncio.wait_for(queued.wait(), 2)
            assert not follower.done() and calls == [0]
            send_first_progress.set()
            leader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leader
            assert app.review_semaphore.locked() and len(app._review_tasks) == 1
            release[0].set()
            await asyncio.wait_for(started[1].wait(), 2)
            release[1].set()
            messages = await asyncio.wait_for(follower, 2)
            assert any(message.get("type") == "queued" for message in messages)
            assert any(message.get("percent") == 65 for message in messages)
            assert not any(message.get("percent") == 42 for message in messages)
            complete = [message for message in messages if message["type"] == "complete"]
            assert len(complete) == 1 and complete[0]["result"] == {**review(), "game_id": UUID}
            assert calls == [0, 1]
            await asyncio.sleep(0)
            assert not app._review_tasks and not app.review_semaphore.locked()
        finally:
            for event in release:
                event.set()
            send_first_progress.set()
            for task in (leader, follower):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (leader, follower) if task is not None), return_exceptions=True)

    asyncio.run(scenario())
