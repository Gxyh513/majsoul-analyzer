"""Check that independently loaded scripts retain their shared execution order."""
import json
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from frontend_assets import WEB_DIR, _page_assets


SCRIPT_PATHS = [
    '/js/replay-state.js', '/js/api.js', '/js/review.js',
    '/js/summary-renderer.js', '/js/replay-controls.js', '/js/board-renderer.js',
    '/js/decision-renderer.js', '/js/replay-navigation.js', '/js/modal-manager.js',
    '/js/import.js', '/js/events.js', '/app.js',
]


def test_page_loads_classic_scripts_in_order_with_cache_versions():
    assets = _page_assets()
    urls = [urlsplit(src) for src in assets.scripts]
    assert [url.path for url in urls] == SCRIPT_PATHS
    assert len({url.query for url in urls}) == 1
    assert urls[0].query.startswith('v=')
    assert all((WEB_DIR / url.path.lstrip('/')).is_file() for url in urls)
    assert not (WEB_DIR / 'js/cache.js').exists()
    for attrs in assets.script_attributes:
        assert attrs.get('type', '').lower() in ('', 'text/javascript', 'application/javascript')
        assert 'async' not in attrs and 'defer' not in attrs


def test_classic_scripts_execute_in_shared_scope():
    if not shutil.which('node'):
        pytest.skip('Node.js not installed')
    files = [str(WEB_DIR / path.lstrip('/')) for path in SCRIPT_PATHS]
    script = r'''
const assert = require('assert'), fs = require('fs'), vm = require('vm');
const handlers = {};
const window = {addEventListener:(event, handler)=>handlers[event] = handler};
const context = vm.createContext({
  window, document:{}, localStorage:{getItem:()=>null}, console,
  setTimeout, clearTimeout, URL, URLSearchParams
});
for (const file of FILES) new vm.Script(fs.readFileSync(file,'utf8'),{filename:file}).runInContext(context);
assert(window.state);
assert.strictEqual(typeof handlers.resize, 'function');
assert.strictEqual(typeof handlers.DOMContentLoaded, 'function');
assert.strictEqual(typeof window.toggleCandUkeire, 'function');
'''.replace('FILES', json.dumps(files))
    proc = subprocess.run(['node', '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert proc.returncode == 0, proc.stderr
