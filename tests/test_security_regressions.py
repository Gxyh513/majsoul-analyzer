"""Security boundaries for local, cross-origin and remote access."""
import asyncio
from pathlib import Path
from starlette.testclient import TestClient
from server import app as server_app


def client(peer="127.0.0.1", host="127.0.0.1"):
    return TestClient(server_app.app, base_url=f"http://{host}", client=(peer, 12345))


def test_local_same_origin_works_and_cross_site_is_denied(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    monkeypatch.delenv("MAJSOUL_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    c = client()
    assert c.get("/api/records/local").status_code == 200
    assert c.get("/api/records/local", headers={"Origin":"http://127.0.0.1"}).status_code == 200
    bad = c.options("/api/records/local", headers={"Origin":"https://evil.example", "Access-Control-Request-Method":"GET"})
    assert bad.status_code == 403
    assert "access-control-allow-origin" not in bad.headers
    assert c.get("/api/records/local", headers={"Origin":"https://evil.example"}).status_code == 403


def test_remote_requires_token_even_with_allowed_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "test-secret")
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://review.example")
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    c = client(peer="203.0.113.5", host="api.example")
    # Direct caller without token or allowed origin is rejected
    assert c.get("/api/records/local").status_code == 401
    assert c.get("/api/records/local", headers={"Authorization":"Bearer wrong"}).status_code == 401
    # Caller from an untrusted origin is rejected with 403
    assert c.get("/api/records/local", headers={"Origin":"https://evil.example"}).status_code == 403
    # Allowed frontend origin is not an identity
    denied = c.get("/api/records/local", headers={"Origin":"https://review.example"})
    assert denied.status_code == 401
    assert denied.headers["access-control-allow-origin"] == "https://review.example"
    assert c.get("/api/records/local", headers={"Origin":"https://review.example", "X-Api-Token":"test-secret"}).status_code == 200
    assert c.options("/api/records/local", headers={"Origin":"https://review.example", "Access-Control-Request-Method":"GET"}).status_code == 204
    # Direct caller with valid token is allowed
    assert c.get("/api/records/local", headers={"Authorization":"Bearer test-secret"}).status_code == 200
    assert c.get("/api/records/local?api_token=test-secret").status_code == 401


def test_uploaded_uuid_cannot_escape_record_directory(monkeypatch, tmp_path):
    root = tmp_path / "records"
    root.mkdir()
    monkeypatch.setattr(server_app, "RECORDS_DIR", root)
    from fastapi import HTTPException
    for uuid in ("../outside", "/absolute", r"..\outside", "C:outside"):
        content = {"head":{"uuid":uuid,"accounts":[],"result":{},"config":{}},"actions":[]}
        try:
            asyncio.run(server_app.resolve_game_data(content=content, target_actor=0))
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("unsafe UUID accepted")
    assert not list(tmp_path.glob("*.json"))


def test_symlink_record_cannot_escape(monkeypatch, tmp_path):
    root = tmp_path / "records"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("original")
    monkeypatch.setattr(server_app, "RECORDS_DIR", root)
    asyncio.run(server_app.resolve_game_data(content={"head":{"uuid":"safe","accounts":[],"result":{},"config":{}},"actions":[]}, target_actor=0))
    assert outside.read_text() == "original"


def test_configured_token_also_protects_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "secret")
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    c = client()
    assert c.get("/api/records/local").status_code == 401
    assert c.get("/api/records/local", headers={"Authorization":"Bearer secret"}).status_code == 200


def test_resolve_game_data_does_not_persist_records_to_disk(monkeypatch, tmp_path):
    root = tmp_path / "records"
    root.mkdir()
    monkeypatch.setattr(server_app, "RECORDS_DIR", root)
    content = {"head": {"uuid": "safe_uuid", "accounts": [], "result": {}, "config": {}}, "actions": []}
    game_id, events, meta, seat = asyncio.run(server_app.resolve_game_data(content=content, target_actor=0))
    assert game_id.startswith("upload-")
    assert meta["uuid"] == "safe_uuid"
    assert len(events) > 0
    # Zero files must be written to disk
    assert not list(root.glob("*.json"))
    assert not list(root.glob("*.tmp"))
    assert not list(tmp_path.glob("*.json"))


def test_api_status_is_public_health_check(monkeypatch):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "test-secret")
    c = client(peer="203.0.113.5", host="api.example")
    # /api/status is exempt from token requirement
    assert c.get("/api/status").status_code == 200
    # but other endpoints require token
    assert c.get("/api/records/local").status_code == 401
    # and support the X-Api-Token header but not query parameters
    assert c.get("/api/records/local", headers={"X-Api-Token": "test-secret"}).status_code == 200
    assert c.get("/api/records/local?api_token=test-secret").status_code == 401


def test_remote_allowed_origin_without_configured_token_is_denied(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_API_TOKEN", raising=False)
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://review.example")
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    c = client(peer="203.0.113.5", host="api.example")
    assert c.get("/api/records/local", headers={"Origin": "https://review.example"}).status_code == 401


def test_wrong_origin_rejected_even_with_valid_token(monkeypatch):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "secret")
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://review.example")
    c = client(peer="203.0.113.5", host="api.example")
    assert c.get("/api/records/local", headers={"Origin": "https://evil.example", "X-Api-Token": "secret"}).status_code == 403
