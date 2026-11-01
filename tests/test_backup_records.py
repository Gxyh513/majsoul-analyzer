"""Backup must not include account secrets, files outside records, or existing targets."""
import hashlib
import json
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
    (records / "symlink.json").symlink_to(outside)
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
