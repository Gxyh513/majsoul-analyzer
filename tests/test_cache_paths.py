from pathlib import Path

import pytest

from server.cache_paths import cache_paths


def mock_workspace(monkeypatch, present):
    original = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: present if self == Path("/mnt/workspace") else original(self))


def test_default_cache_paths_keep_existing_local_layout(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_CACHE_ROOT", raising=False)
    mock_workspace(monkeypatch, False)
    assert cache_paths(tmp_path) == (tmp_path / "cache", tmp_path / "records", tmp_path / "reviews")


def test_workspace_is_default_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_CACHE_ROOT", raising=False)
    mock_workspace(monkeypatch, True)
    root = Path("/mnt/workspace/majsoul-analyzer")
    assert cache_paths(tmp_path) == (root / "cache", root / "records", root / "reviews")


@pytest.mark.parametrize("workspace_present", [False, True])
def test_explicit_cache_root_keeps_caches_together(monkeypatch, tmp_path, workspace_present):
    root = tmp_path / "mounted storage"
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", str(root))
    mock_workspace(monkeypatch, workspace_present)
    assert cache_paths(tmp_path / "local") == (root / "cache", root / "records", root / "reviews")


def test_relative_cache_root_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        cache_paths(tmp_path)


def test_empty_cache_root_uses_local_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", "  ")
    mock_workspace(monkeypatch, False)
    assert cache_paths(tmp_path) == (tmp_path / "cache", tmp_path / "records", tmp_path / "reviews")


def test_empty_cache_root_uses_workspace_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", "  ")
    mock_workspace(monkeypatch, True)
    root = Path("/mnt/workspace/majsoul-analyzer")
    assert cache_paths(tmp_path) == (root / "cache", root / "records", root / "reviews")


def test_backend_uses_configured_cache_root_in_new_process(monkeypatch, tmp_path):
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "持久 cache"
    env = os.environ.copy()
    env["MAJSOUL_CACHE_ROOT"] = str(root)
    code = ("import json, server.app as a; "
            "print(json.dumps([str(a.CACHE_DIR), str(a.RECORDS_DIR), "
            "str(a.REVIEWS_DIR), str(a.MODELS_DIR), str(a.LOCAL_CONFIG_PATH)]))")
    result = subprocess.run([sys.executable, "-c", code], env=env, check=True,
                            capture_output=True, text=True, encoding="utf8")
    paths = list(map(Path, json.loads(result.stdout.splitlines()[-1])))
    assert paths[:3] == [root / "cache", root / "records", root / "reviews"]
    assert all(p.is_dir() for p in paths[:3])
    assert paths[3] != root / "models" and paths[4] != root / "config.local.json"


@pytest.mark.anyio
@pytest.mark.parametrize(("setting", "enabled"), [(None, False), ("0", False), ("true", False), ("yes", False), ("1", True)])
async def test_lifespan_cleanup_requires_explicit_opt_in(monkeypatch, tmp_path, setting, enabled):
    import asyncio
    import os
    import time
    from unittest.mock import AsyncMock
    import server.app as app

    for name in ("REVIEWS_DIR", "RECORDS_DIR", "CACHE_DIR", "METADATA_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(app, name, directory)
    old_record = app.RECORDS_DIR / "old.json"
    old_record.write_text("{}", encoding="utf8")
    old = time.time() - 8 * 86400
    os.utime(old_record, (old, old))
    if setting is None:
        monkeypatch.delenv("MAJSOUL_CACHE_AUTO_CLEANUP", raising=False)
    else:
        monkeypatch.setenv("MAJSOUL_CACHE_AUTO_CLEANUP", setting)
    login = AsyncMock()
    monkeypatch.setattr(app, "ensure_ms_logged_in", login)
    async with app.lifespan(app.app):
        if enabled:
            for _ in range(100):
                if not old_record.exists():
                    break
                await asyncio.sleep(0.01)
        else:
            await asyncio.sleep(0.05)
        assert old_record.exists() is not enabled
    login.assert_not_called()


def test_record_write_excludes_nested_credentials(tmp_path):
    import json
    from server.account_pool import _save_record

    uuid = "241026-11111111-2222-3333-4444-555555555555"
    data = {"head": {"uuid": uuid, "accounts": [{"seat": 0, "account_id": 1,
             "password": "password-sentinel", "access_token": "token-sentinel"}]},
            "actions": [], "token": "outer-sentinel"}
    _save_record(tmp_path, uuid, data)
    contents = (tmp_path / f"{uuid}.json").read_text(encoding="utf8")
    assert all(secret not in contents for secret in ("password-sentinel", "token-sentinel", "outer-sentinel"))
    assert json.loads(contents)["head"]["accounts"][0]["account_id"] == 1
