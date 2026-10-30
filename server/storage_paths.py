"""Locations of permanent records and the protocol descriptors needed to read them."""
from __future__ import annotations

import os
from pathlib import Path


def storage_paths(data_dir: Path) -> tuple[Path, Path]:
    configured = os.environ.get("MAJSOUL_CACHE_ROOT", "").strip()
    workspace = Path("/mnt/workspace")
    root = Path(configured).expanduser() if configured else (workspace / "majsoul-analyzer" if workspace.is_dir() else Path(data_dir))
    if configured and not root.is_absolute():
        raise ValueError("MAJSOUL_CACHE_ROOT must be an absolute path")
    return root / "cache", root / "records"
