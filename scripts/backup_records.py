"""Explicit offline backup of immutable raw Mahjong Soul records.

Never includes account databases, passwords, cached AI results or lockfiles.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
from datetime import datetime, timezone

from server.storage_paths import storage_paths


_FILENAME = re.compile(r"\d{6}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(?:pb|json)")


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(part)
    return checksum.hexdigest()


def backup_records(records_dir: Path, output_file: Path) -> dict:
    records_dir, output_file = Path(records_dir), Path(output_file)
    if not records_dir.is_dir() or records_dir.is_symlink():
        raise ValueError("原始牌谱目录不存在或是符号链接")
    if output_file.exists() or output_file.is_symlink():
        raise FileExistsError("备份文件已存在，不覆盖已有备份")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in sorted(records_dir.iterdir()):
        if (not path.is_file() or path.is_symlink() or not _FILENAME.fullmatch(path.name)):
            continue
        entries.append((path, _digest(path)))
    if not entries:
        raise ValueError("没有可备份的原始牌谱文件")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output_file.parent, prefix=".records-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        manifest = {
            "version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "records": [{"name": path.name, "sha256": sha} for path, sha in entries],
        }
        with tarfile.open(temporary, "w:gz") as archive:
            for path, _digest_value in entries:
                with path.open("rb") as source:
                    stat = path.stat()
                    entry = tarfile.TarInfo("records/" + path.name)
                    entry.size = stat.st_size
                    entry.mode = 0o600
                    archive.addfile(entry, source)
            payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
        os.replace(temporary, output_file)
        temporary = None
        return {"records": len(entries), "bytes": output_file.stat().st_size}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    default_records = storage_paths(root / "data")[1]
    parser = argparse.ArgumentParser(description="只备份原始牌谱，不包含账号及令牌")
    parser.add_argument("--records", type=Path, default=default_records)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backup_records(args.records, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
