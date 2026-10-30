"""Read frontend assets in the order used by the page."""
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class _PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.script_attributes = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
            self.script_attributes.append(attrs)
        elif tag == "link" and "stylesheet" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            self.stylesheets.append(attrs["href"])


def _page_assets():
    parser = _PageAssets()
    parser.feed((WEB_DIR / "index.html").read_text(encoding="utf-8"))
    return parser


def _local_path(url, parent=WEB_DIR):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"External frontend asset is not a local source: {url}")
    path = WEB_DIR / parsed.path.lstrip("/") if parsed.path.startswith("/") else parent / parsed.path
    path = path.resolve()
    path.relative_to(WEB_DIR.resolve())
    return path


def javascript_source():
    return "\n".join(_local_path(src).read_text(encoding="utf-8") for src in _page_assets().scripts)


_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?[\"']([^\"']+)[\"']\s*\)?\s*;")


def _css_source(path, ancestors=()):
    if path in ancestors:
        raise ValueError(f"Circular stylesheet import: {path}")
    text = path.read_text(encoding="utf-8")
    return _CSS_IMPORT.sub(
        lambda match: _css_source(_local_path(match.group(1), path.parent), (*ancestors, path)),
        text,
    )


def stylesheet_source():
    return "\n".join(_css_source(_local_path(href)) for href in _page_assets().stylesheets)
