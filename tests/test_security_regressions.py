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


def test_remote_requires_token_or_allowed_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "test-secret")
    monkeypatch.setenv("MAJSOUL_ALLOWED_ORIGINS", "https://review.example")
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    c = client(peer="203.0.113.5", host="api.example")
    # Direct caller without token or allowed origin is rejected
    assert c.get("/api/records/local").status_code == 401
    assert c.get("/api/records/local", headers={"Authorization":"Bearer wrong"}).status_code == 401
    # Caller from an untrusted origin is rejected with 403
    assert c.get("/api/records/local", headers={"Origin":"https://evil.example"}).status_code == 403
    # Authorized frontend origin is allowed
    assert c.get("/api/records/local", headers={"Origin":"https://review.example"}).status_code == 200
    # Direct caller with valid token is allowed
    assert c.get("/api/records/local", headers={"Authorization":"Bearer test-secret"}).status_code == 200
    assert c.get("/api/records/local?api_token=test-secret").status_code == 200


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
    import pytest
    root = tmp_path / "records"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("original")
    try:
        (root / "safe.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    monkeypatch.setattr(server_app, "RECORDS_DIR", root)
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException):
        asyncio.run(server_app.resolve_game_data(content={"head":{"uuid":"safe","accounts":[],"result":{},"config":{}},"actions":[]}, target_actor=0))
    assert outside.read_text() == "original"


def test_configured_token_also_protects_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "secret")
    monkeypatch.setattr(server_app, "RECORDS_DIR", tmp_path)
    c = client()
    assert c.get("/api/records/local").status_code == 200
    assert c.get("/api/records/local", headers={"Authorization":"Bearer secret"}).status_code == 200


def test_path_resolution_guard_blocks_escaping_target(monkeypatch, tmp_path):
    from fastapi import HTTPException
    import pytest
    root = tmp_path / "records"
    root.mkdir()
    monkeypatch.setattr(server_app, "RECORDS_DIR", root)
    original_resolve = Path.resolve
    def redirected(path, *args, **kwargs):
        if path == root / "safe.json":
            return tmp_path / "outside.json"
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(HTTPException):
        asyncio.run(server_app.resolve_game_data(content={"head":{"uuid":"safe","accounts":[],"result":{},"config":{}},"actions":[]}, target_actor=0))
    assert not (tmp_path / "outside.json").exists()


def test_api_status_is_public_health_check(monkeypatch):
    monkeypatch.setenv("MAJSOUL_API_TOKEN", "test-secret")
    c = client(peer="203.0.113.5", host="api.example")
    # /api/status is exempt from token requirement
    assert c.get("/api/status").status_code == 200
    # but other endpoints require token
    assert c.get("/api/records/local").status_code == 401
    # and support X-Api-Token header or api_token query param
    assert c.get("/api/records/local", headers={"X-Api-Token": "test-secret"}).status_code == 200
    assert c.get("/api/records/local?api_token=test-secret").status_code == 200
