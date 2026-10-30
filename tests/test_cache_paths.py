from pathlib import Path

import pytest

from server.cache_paths import cache_paths


def test_default_cache_paths_keep_existing_local_layout(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_CACHE_ROOT", raising=False)
    assert cache_paths(tmp_path) == (tmp_path / "cache", tmp_path / "records", tmp_path / "reviews")


def test_workspace_is_default_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_CACHE_ROOT", raising=False)
    original = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: True if self == Path("/mnt/workspace") else original(self))
    root = Path("/mnt/workspace/majsoul-analyzer")
    assert cache_paths(tmp_path) == (root / "cache", root / "records", root / "reviews")


def test_explicit_cache_root_keeps_caches_together(monkeypatch, tmp_path):
    root = tmp_path / "mounted storage"
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", str(root))
    assert cache_paths(tmp_path / "local") == (root / "cache", root / "records", root / "reviews")


def test_relative_cache_root_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        cache_paths(tmp_path)


def test_empty_cache_root_uses_local_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", "  ")
    assert cache_paths(tmp_path) == (tmp_path / "cache", tmp_path / "records", tmp_path / "reviews")


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
