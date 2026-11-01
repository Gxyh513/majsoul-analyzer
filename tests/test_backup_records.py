"""Backup must not include account secrets, files outside records, or existing targets."""
import hashlib
import json
import os
import tarfile

import pytest

from scripts.backup_records import backup_records


UUID = "241026-11111111-2222-3333-4444-555555555555"


def test_raw_backup_only_includes_verified_named_records(tmp_path):
    records = tmp_path / "records"
    records.mkdir()
    (records / (UUID + ".pb")).write_bytes(b"protobuf-bytes")
    (records / "admin_accounts.sqlite3").write_text("private")
    (records / "secret-token.json").write_text("private")
    (records / ".locks").mkdir()
    (records / ".locks" / (UUID + ".lock")).write_text("lock")
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    try:
        (records / "symlink.json").symlink_to(outside)
    except OSError:
        pass
    target = tmp_path / "backup.tar.gz"

    result = backup_records(records, target)
    assert result["records"] == 1
    with tarfile.open(target, "r:gz") as archive:
        assert sorted(archive.getnames()) == ["manifest.json", "records/" + UUID + ".pb"]
        manifest = json.load(archive.extractfile("manifest.json"))
        assert manifest["records"][0]["sha256"] == hashlib.sha256(b"protobuf-bytes").hexdigest()
        assert archive.extractfile("records/" + UUID + ".pb").read() == b"protobuf-bytes"
    with pytest.raises(FileExistsError):
        backup_records(records, target)
    assert outside.read_text() == "private"


def test_no_backup_when_empty_or_record_dir_missing(tmp_path):
    records = tmp_path / "records"
    with pytest.raises(ValueError):
        backup_records(records, tmp_path / "output.tar.gz")
    records.mkdir()
    with pytest.raises(ValueError):
        backup_records(records, tmp_path / "output.tar.gz")


def test_backup_hash_tracks_bytes_actually_written_to_archive(tmp_path, monkeypatch):
    """Source may change after enumeration; manifest must match archived bytes."""
    from scripts.verify_records_backup import verify_backup

    records = tmp_path / "records"
    records.mkdir()
    path = records / (UUID + ".pb")
    path.write_bytes(b"first-content")
    updated = b"newer-content"
    original = tarfile.TarFile.addfile

    def change_during_archive(archive, entry, fileobj=None):
        if entry.name.startswith("records/"):
            # Same length; the already-open source descriptor sees the update.
            path.write_bytes(updated)
        return original(archive, entry, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "addfile", change_during_archive)
    target = tmp_path / "backup.tar.gz"
    assert backup_records(records, target)["records"] == 1
    assert verify_backup(target)["validated_records"] == 1
    with tarfile.open(target, "r:gz") as archive:
        assert archive.extractfile("records/" + UUID + ".pb").read() == updated
        manifest = json.load(archive.extractfile("manifest.json"))
        assert manifest["records"][0]["sha256"] == hashlib.sha256(updated).hexdigest()


def test_backup_never_overwrites_concurrent_target(tmp_path, monkeypatch):
    import scripts.backup_records as script

    records = tmp_path / "records"
    records.mkdir()
    (records / (UUID + ".pb")).write_bytes(b"raw")
    target = tmp_path / "backup.tar.gz"
    original_link = os.link

    def competing_publisher(source, destination, *args, **kwargs):
        target.write_bytes(b"other-backup")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(script.os, "link", competing_publisher)
    with pytest.raises(FileExistsError):
        backup_records(records, target)
    assert target.read_bytes() == b"other-backup"
    assert not list(tmp_path.glob(".records-*.tmp"))


def test_backup_verifies_before_publishing(tmp_path, monkeypatch):
    import scripts.verify_records_backup as verifier

    records = tmp_path / "records"
    records.mkdir()
    (records / (UUID + ".pb")).write_bytes(b"record")
    target = tmp_path / "backup.tar.gz"

    def rejected_archive(*args, **kwargs):
        raise ValueError("备份校验失败")

    monkeypatch.setattr(verifier, "verify_backup", rejected_archive)
    with pytest.raises(ValueError, match="校验"):
        backup_records(records, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".records-*.tmp"))
