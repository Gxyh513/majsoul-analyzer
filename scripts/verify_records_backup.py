"""Offline verification and safe empty-directory restore of raw paipu backups.

Never opens account DBs, cached reviews, tokens, or private upload JSON.
No tarfile.extract(): every member and checksum is checked before writing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile

from .backup_records import _FILENAME

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000


def _digest_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    checksum = hashlib.sha256()
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError("备份内容无法读取")
    with stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(data)
    return checksum.hexdigest()


def verify_backup(archive_path: Path, restore_to: Path | None = None) -> dict:
    archive_path = Path(archive_path)
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ValueError("需要普通备份归档文件，拒绝符号链接")
    destination = Path(restore_to) if restore_to is not None else None
    if destination is not None and (destination.exists() or destination.is_symlink()):
        raise FileExistsError("恢复目标必须尚不存在，不能覆盖已有记录")

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) < 2 or len(members) > MAX_RECORDS + 1:
            raise ValueError("备份成员数量超出安全范围")
        if any(not member.isfile() or member.linkname for member in members):
            raise ValueError("备份不允许目录、符号链接或特殊文件")
        indexed = {member.name: member for member in members}
        if len(indexed) != len(members) or "manifest.json" not in indexed:
            raise ValueError("备份成员重复或缺少清单")
        manifest_member = indexed["manifest.json"]
        if manifest_member.size > MAX_MANIFEST_BYTES:
            raise ValueError("备份清单过大")
        stream = archive.extractfile(manifest_member)
        if stream is None:
            raise ValueError("备份清单无法读取")
        with stream:
            manifest = json.load(stream)
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("备份清单版本不支持")
        entries = manifest.get("records")
        if not isinstance(entries, list) or not entries or len(entries) > MAX_RECORDS:
            raise ValueError("备份记录清单无效")
        expected = {"manifest.json"}
        record_members = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("备份记录格式错误")
            name, sha = entry.get("name"), entry.get("sha256")
            if (not isinstance(name, str) or not _FILENAME.fullmatch(name)
                    or not isinstance(sha, str) or len(sha) != 64
                    or any(char not in "0123456789abcdef" for char in sha)):
                raise ValueError("备份记录名称或 SHA-256 不合法")
            key = "records/" + name
            member = indexed.get(key)
            if key in expected or member is None or member.size > MAX_RECORD_BYTES:
                raise ValueError("备份包含重复、缺失或过大的记录")
            expected.add(key)
            record_members.append(member)
            if _digest_member(archive, member) != sha:
                raise ValueError("备份记录校验失败")
        if set(indexed) != expected:
            raise ValueError("备份包含未列入清单的文件")

        if destination is not None:
            if destination.parent.is_symlink() or not destination.parent.is_dir():
                raise ValueError("恢复目标父目录不安全或不存在")
            # Files are created explicitly, not extracted from untrusted paths.
            destination.mkdir(mode=0o700)
            try:
                for member in record_members:
                    output = destination / member.name.removeprefix("records/")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("记录无法重新读取")
                    with stream, output.open("xb") as out:
                        os.chmod(output, 0o600)
                        shutil.copyfileobj(stream, out, length=1024 * 1024)
            except BaseException:
                for file in destination.iterdir():
                    if file.is_file() and not file.is_symlink():
                        file.unlink()
                destination.rmdir()
                raise

        return {"validated_records": len(record_members),
                "restored": destination is not None}


def main():
    parser = argparse.ArgumentParser(description="验证备份清单与哈希，恢复只允许新目录")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--restore-to", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(verify_backup(args.archive, args.restore_to), ensure_ascii=False))


if __name__ == "__main__":
    main()
