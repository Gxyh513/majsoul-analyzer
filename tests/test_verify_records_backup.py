"""Offline backup integrity and restore tests; no private account contents."""
import io
import json
import tarfile

import pytest

from scripts.backup_records import backup_records
from scripts.verify_records_backup import verify_backup


UUID = "241026-11111111-2222-3333-4444-555555555555"


def test_verified_backup_can_restore_only_to_new_directory(tmp_path):
    source = tmp_path / "records"
    source.mkdir()
    payload = b"public-paipu-record"
    (source / (UUID + ".pb")).write_bytes(payload)
    (source / "admin_accounts.sqlite3").write_bytes(b"secret")
    archive = tmp_path / "backup.tar.gz"
    backup_records(source, archive)
    assert verify_backup(archive) == {"validated_records": 1, "restored": False}
    assert not (tmp_path / "restored").exists()
    assert verify_backup(archive, tmp_path / "restored") == {"validated_records": 1, "restored": True}
    assert (tmp_path / "restored" / (UUID + ".pb")).read_bytes() == payload
    assert not (tmp_path / "restored" / "admin_accounts.sqlite3").exists()
    with pytest.raises(FileExistsError):
        verify_backup(archive, tmp_path / "restored")


def test_reject_tampered_record_without_creating_restore_destination(tmp_path):
    source = tmp_path / "records"
    source.mkdir()
    (source / (UUID + ".pb")).write_bytes(b"original")
    good = tmp_path / "good.tar.gz"
    backup_records(source, good)
    tampered = tmp_path / "bad.tar.gz"
    with tarfile.open(good, "r:gz") as inp, tarfile.open(tampered, "w:gz") as out:
        for member in inp.getmembers():
            payload = inp.extractfile(member).read()
            if member.name.startswith("records/"):
                payload = b"changed!"
            member.size = len(payload)
            out.addfile(member, io.BytesIO(payload))
    with pytest.raises(ValueError, match="校验"):
        verify_backup(tampered, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_reject_archive_path_traversal_and_unlisted_files(tmp_path):
    archive = tmp_path / "untrusted.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = json.dumps({"version": 1, "records": [{"name": UUID + ".pb", "sha256": "0" * 64}]}).encode()
        manifest = tarfile.TarInfo("manifest.json")
        manifest.size = len(payload)
        tar.addfile(manifest, io.BytesIO(payload))
        bad = tarfile.TarInfo("records/../../secret")
        bad.size = 1
        tar.addfile(bad, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        verify_backup(archive)


def test_reject_symlink_restore_destination(tmp_path):
    source = tmp_path / "records"
    source.mkdir()
    (source / (UUID + ".json")).write_text("{}", encoding="utf8")
    archive = tmp_path / "archive.tar.gz"
    backup_records(source, archive)
    parent = tmp_path / "somewhere"
    parent.mkdir()
    target = tmp_path / "link"
    try:
        target.symlink_to(parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(FileExistsError):
        verify_backup(archive, target)
    assert not list(parent.iterdir())
