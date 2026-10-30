"""Verify empty input and explicit demo navigation."""
import shutil
import subprocess
from pathlib import Path

import pytest

from frontend_assets import javascript_source


def test_empty_input_and_demo_label():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    code = source[source.index("function applyPerspective("):source.index("async function switchSeatPerspective(")]
    script = r'''
const assert = require('assert');
const elements = {};
for (const id of ['homeInputError', 'homePaipuInput', 'demoReviewLabel']) {
  const classes = new Set(['hidden']);
  elements[id] = {classList: {add:x=>classes.add(x), remove:x=>classes.delete(x),
    toggle:(x, on)=>on ? classes.add(x) : classes.delete(x), contains:x=>classes.has(x)}, focus(){this.focused=true}};
}
const $ = id => elements[id] || null;
const state = {selectedModel:'akagi', kyokuIdx:0, stepIdx:0};
let currentReviewSource, analysisViewVersion = 0, requests = [];
const getReview = async source => {requests.push(source); return {game_id:source.identity,target_actor:0,kyokus:[]}};
const parsePaipuUuid = x=>x, updateEngineStatusDisplay=()=>{}, setLoading=()=>{},
  updateAnalysisProgress=()=>{}, cancelScheduledStep=()=>{}, renderSummaryDashboard=()=>{},
  selectKyoku=()=>{}, showTier=()=>{}, showToast=()=>{}, showAnalysisError=()=>{};
const window = {location:{href:'https://example.test/'},history:{pushState(){}}};
''' + code + r'''
(async()=>{
  await startAnalysisFromInput('   ');
  assert.strictEqual(requests.length,0);
  assert(!elements.homeInputError.classList.contains('hidden'));
  assert(elements.homePaipuInput.focused);
  await startAnalysisFromInput('demo');
  assert.strictEqual(requests[0].input,'demo');
  assert(!elements.demoReviewLabel.classList.contains('hidden'));
  assert(elements.homeInputError.classList.contains('hidden'));
  await startAnalysisFromInput('real-game');
  assert.strictEqual(requests[1].input,'real-game');
  assert(elements.demoReviewLabel.classList.contains('hidden'));
})().catch(e=>{console.error(e);process.exit(1)});
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_demo_entry_and_labels_are_present():
    html = Path("web/index.html").read_text(encoding="utf-8")
    js = javascript_source()
    assert 'id="btnDemoAnalyze"' in html
    assert 'id="demoReviewLabel"' in html
    assert 'id="homeInputError"' in html
    assert '$("btnDemoAnalyze")?.addEventListener("click", () => startAnalysisFromInput("demo"' in js
