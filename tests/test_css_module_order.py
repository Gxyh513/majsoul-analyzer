"""Keep the CSS entry point and important trailing overrides in cascade order."""
import re
from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "web"
MODULES = ("variables", "landing", "board", "decision", "modal", "responsive")
IMPORT = re.compile(r'@import\s+url\("(/css/[a-z]+\.css)\?v=([\w.-]+)"\);')


def rule(css, selector):
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_css_import_order_paths_and_cache_version():
    entry = (WEB / "styles.css").read_text(encoding="utf8")
    imports = IMPORT.findall(entry)
    assert [path for path, _ in imports] == [f"/css/{name}.css" for name in MODULES]
    assert len({version for _, version in imports}) == 1
    assert not IMPORT.sub("", entry).strip()
    assert all((WEB / path.lstrip("/")).is_file() for path, _ in imports)


def test_responsive_and_final_overrides_remain_last():
    css = {name: (WEB / "css" / f"{name}.css").read_text(encoding="utf8")
           for name in MODULES}
    responsive = css["responsive"]
    mobile = responsive[responsive.index("@media (max-width: 768px)"):
                        responsive.index("@media (max-width: 380px)")]

    assert "padding: 44px 36px;" in rule(css["landing"], ".kd-landing-card")
    assert "padding: 24px 16px !important;" in rule(mobile, ".kd-landing-card")
    assert "padding: 12px 10px;" in rule(css["decision"], ".kd-decision-card")
    assert "padding: 10px 8px !important;" in rule(mobile, ".kd-decision-card")

    # These board, table and dora fixes deliberately follow the mobile rules.
    assert ".pov-p1 .tileDiv.has-rotate," in css["board"]
    trailing = responsive[responsive.index("@media (max-width: 380px)"):]
    assert "justify-content: flex-end !important;" in rule(
        trailing, ".pov-p1 .tileDiv.has-rotate,\n.grid-discard-p1 .tileDiv.has-rotate"
    )
    assert "width: calc(var(--tile-img-width) * 0.72) !important;" in rule(
        trailing, ".info-doras .tileDiv"
    )
    assert "text-align: left !important;" in rule(
        trailing, "#actualVsAiTable td:nth-child(2),\n"
        "#actualVsAiTable #optActualCell,\n#actualVsAiTable #optAiCell"
    )
    assert "width: 120px !important;" in rule(
        trailing, "#candTable th:first-child,\n#candTable td:first-child"
    )
