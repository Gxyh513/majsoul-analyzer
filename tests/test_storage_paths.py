from pathlib import Path

import pytest

from server.storage_paths import storage_paths


def mock_workspace(monkeypatch, present):
    original = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: present if self == Path("/mnt/workspace") else original(self))


def test_default_storage_paths_keep_existing_local_layout(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_CACHE_ROOT", raising=False)
    mock_workspace(monkeypatch, False)
    assert storage_paths(tmp_path) == (tmp_path / "cache", tmp_path / "records")


def test_workspace_is_default_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv("MAJSOUL_CACHE_ROOT", raising=False)
    mock_workspace(monkeypatch, True)
    root = Path("/mnt/workspace/majsoul-analyzer")
    assert storage_paths(tmp_path) == (root / "cache", root / "records")


@pytest.mark.parametrize("workspace_present", [False, True])
def test_explicit_root_keeps_records_and_protocols_together(monkeypatch, tmp_path, workspace_present):
    root = tmp_path / "mounted storage"
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", str(root))
    mock_workspace(monkeypatch, workspace_present)
    assert storage_paths(tmp_path / "local") == (root / "cache", root / "records")


def test_relative_root_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MAJSOUL_CACHE_ROOT", "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        storage_paths(tmp_path)


@pytest.mark.anyio
async def test_lifespan_never_cleans_records(monkeypatch, tmp_path):
    import os
    import time
    import server.app as app

    old_record = tmp_path / "old.json"
    old_record.write_text("{}", encoding="utf8")
    old = time.time() - 365 * 86400
    os.utime(old_record, (old, old))
    monkeypatch.setattr(app, "RECORDS_DIR", tmp_path)
    monkeypatch.setenv("MAJSOUL_CACHE_AUTO_CLEANUP", "1")
    async with app.lifespan(app.app):
        assert old_record.is_file()
    assert old_record.read_text(encoding="utf8") == "{}"
