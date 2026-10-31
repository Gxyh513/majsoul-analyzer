"""Node-backed regressions for double-ron and seat-specific timeline steps."""
import shutil
import subprocess

import pytest

from frontend_assets import javascript_source


def _node(script: str):
    if not shutil.which("node"):
        pytest.skip("Node.js is required")
    proc = subprocess.run(["node", "-e", script], text=True, capture_output=True, timeout=15)
    assert proc.returncode == 0, proc.stderr


def test_next_decision_keeps_all_double_ron_settlements():
    src = javascript_source()
    code = src[src.index("function jumpNextDecision()"):src.index("function jumpPrevBlunder()")]
    _node(r"""
const assert = require('node:assert/strict');
let state = {stepIdx:0,kyokuIdx:0,reviewData:{kyokus:[
  {steps:[{event:{type:'dahai'}},{event:{type:'hora',actor:1}},
          {event:{type:'hora',actor:2}}],decisions:[]},
  {steps:[{event:{type:'start_kyoku'}}],decisions:[]}
]}};
const setStep=i=>{state.stepIdx=i};
const jumpNextKyoku=()=>{state.kyokuIdx++;state.stepIdx=0};
const showToast=()=>{throw Error('unexpected end of match')};
""" + code + r"""
jumpNextDecision();
assert.equal(state.stepIdx,2); // final ron, not first
assert.equal(state.kyokuIdx,0);
jumpNextDecision();
assert.equal(state.kyokuIdx,1);
""")


def test_seat_switch_maps_by_event_index():
    src = javascript_source()
    code = src[src.index("function findPerspectiveStep("):src.index("function applyPerspective(")]
    _node(r"""
const assert = require('node:assert/strict');
""" + code + r"""
const seat0=[{event_idx:1},{event_idx:2},{event_idx:3},{event_idx:4}];
const seat1=[{event_idx:1},{event_idx:2},{event_idx:4}];
assert.equal(findPerspectiveStep(seat1,seat0[3].event_idx,3),2);
assert.equal(findPerspectiveStep(seat1,seat0[2].event_idx,2),1);
assert.equal(findPerspectiveStep(seat0,seat1[2].event_idx,2),3);
assert.equal(findPerspectiveStep([0,1,2,3],null,3),3);
""")
