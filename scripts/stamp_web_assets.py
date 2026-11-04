#!/usr/bin/env python3
"""Stamp existing static resource URLs using their SHA-256 content digest.

Run as the Cloudflare Pages build command or during Docker image creation.
The script modifies only published web assets, requires no external packages,
and is idempotent after all dependencies have been stamped.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
STATIC_LINK = re.compile(r"(?P<path>/(?:js|css)/[A-Za-z0-9_./-]+\.(?:js|css))\?v=[A-Za-z0-9_.-]+")
TILE_VERSION = re.compile(r'(const TILE_ASSET_VERSION = ")[^\"]+(";)')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def stamp_links(file: Path) -> None:
    original = file.read_text(encoding="utf8")

    def replace(match: re.Match[str]) -> str:
        asset = WEB / match.group("path").lstrip("/")
        if not asset.is_file() or not asset.resolve().is_relative_to(WEB.resolve()):
            raise RuntimeError(f"missing static asset: {match.group('path')}")
        return f"{match.group('path')}?v={digest(asset.read_bytes())}"

    updated = STATIC_LINK.sub(replace, original)
    if updated != original:
        file.write_text(updated, encoding="utf8")


def main() -> None:
    tile_files = sorted((WEB / "tiles").glob("*.svg"))
    if not tile_files:
        raise RuntimeError("missing tile assets")
    tiles = hashlib.sha256()
    for file in tile_files:
        tiles.update(file.name.encode("utf8"))
        tiles.update(file.read_bytes())
    state_file = WEB / "js" / "replay-state.js"
    original = state_file.read_text(encoding="utf8")
    updated, count = TILE_VERSION.subn(lambda match: match.group(1) + tiles.hexdigest()[:12] + match.group(2), original)
    if count != 1:
        raise RuntimeError("TILE_ASSET_VERSION declaration not found")
    if updated != original:
        state_file.write_text(updated, encoding="utf8")

    # Leaf modules are immutable; stamp consumers before stamping the HTML.
    for file in (WEB / "js" / "resource-loader.js", WEB / "js" / "api.js", WEB / "index.html"):
        stamp_links(file)


if __name__ == "__main__":
    main()
