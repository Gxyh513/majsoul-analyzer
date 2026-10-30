"""Centralized filesystem locations for non-secret application caches.

MAJSOUL_CACHE_ROOT overrides the ModelScope workspace or local data default.
Model weights and configuration remain under the application's data directory.
"""
from __future__ import annotations

import os
from pathlib import Path


def cache_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    """Return resource, record and review directories, in that order."""
    configured = os.environ.get("MAJSOUL_CACHE_ROOT", "").strip()
    workspace = Path('/mnt/workspace')
    root = Path(configured).expanduser() if configured else (workspace / 'majsoul-analyzer' if workspace.is_dir() else Path(data_dir))
    if configured and not root.is_absolute():
        raise ValueError("MAJSOUL_CACHE_ROOT must be an absolute path")
    return root / "cache", root / "records", root / "reviews"
