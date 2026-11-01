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


def test_homepage_keeps_model_status_but_not_detail_prompt():
    page = (WEB_DIR / "index.html").read_text(encoding="utf8")
    assert "点击查看模型详情" not in page
    assert 'id="landingModelCard"' in page
    assert 'id="landingModelDevice"' in page
    assert "Mortal S42 ONNX" in page


def test_refresh_returns_home_without_automatic_paipu_analysis():
    """A refresh with an old ?paipu= URL must not start review work."""
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    app_js = str(WEB_DIR / "app.js")
    review_js = str(WEB_DIR / "js/review.js")
    script = r'''
const assert = require('assert'), fs = require('fs'), vm = require('vm');
const appCode = fs.readFileSync(APP_PATH, 'utf8');
const showTierCode = fs.readFileSync(REVIEW_PATH, 'utf8').split('const REVIEW_SCHEMA')[0];

async function check(inputUrl, shouldReplace) {
  let ready, analysisCount = 0, refreshCount = 0, replacements = [];
  const callbacks = {};
  const classes = (initial) => {
    const values = new Set(initial);
    return {add: x=>values.add(x), remove: x=>values.delete(x), contains:x=>values.has(x)};
  };
  const nodes = {
    tier1Screen: {classList:classes(['hidden'])},
    tier2Screen: {classList:classes([])},
    homePaipuInput: {value:'old-value'}
  };
  const window = {
    location:new URL(inputUrl),
    history:{replaceState(_state,_title,newUrl) {
      replacements.push(newUrl);
      window.location=new URL(newUrl);
    }},
    addEventListener(name,fn){callbacks[name]=fn;},
    scrollTo(){},
  };
  const context = {
    window,URL,URLSearchParams,
    document:{documentElement:{removeAttribute(){}}},
    localStorage:{removeItem(){}},
    state:{isPlaying:false,playSpeed:1},
    $:id=>nodes[id],
    updateResponsiveZoom(){},applyHidePlayerId(){},updateSoundButton(){},bindEvents(){},
    refreshServerStatus(){refreshCount++;return new Promise(resolve=>{ready=resolve})},
    startAnalysisFromInput(){analysisCount++;throw Error('automatic analysis prohibited')},
    togglePlay(){throw Error('unexpected playback')},
  };
  vm.runInNewContext(showTierCode + '\n' + appCode, vm.createContext(context));
  const pending = callbacks.DOMContentLoaded();
  assert(!nodes.tier1Screen.classList.contains('hidden'), 'show home immediately');
  assert(nodes.tier2Screen.classList.contains('hidden'), 'hide old review');
  assert.strictEqual(nodes.homePaipuInput.value, '');
  assert.strictEqual(refreshCount,1);
  assert.strictEqual(analysisCount,0);
  assert.strictEqual(replacements.length,shouldReplace ? 1 : 0);
  assert.strictEqual(window.location.searchParams.has('paipu'),false);
  assert.strictEqual(window.location.searchParams.get('backend'),'model');
  assert.strictEqual(window.location.searchParams.get('studio_token'),'keep');
  assert.strictEqual(window.location.hash,'#section');
  ready();
  await pending;
  assert.strictEqual(analysisCount,0);
}
(async()=>{
 await check('https://example.test/?paipu=241101-abc&backend=model&studio_token=keep#section',true);
 await check('https://example.test/?backend=model&studio_token=keep#section',false);
 await check('https://example.test/?paipu=&backend=model&studio_token=keep#section',true);
})().catch(e=>{console.error(e);process.exit(1)});
'''.replace('APP_PATH', json.dumps(app_js)).replace('REVIEW_PATH', json.dumps(review_js))
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
