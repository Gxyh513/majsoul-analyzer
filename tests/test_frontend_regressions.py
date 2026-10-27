"""Run focused rendering regressions using Node without a browser dependency."""
import shutil
import subprocess
from pathlib import Path

import pytest


def run_js(script):
    if not shutil.which("node"): pytest.skip("Node.js not installed")
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def source_between(start, end):
    text = Path("web/app.js").read_text(encoding="utf8")
    return text[text.index(start):text.index(end)]


def test_river_rerenders_when_earlier_tiles_change():
    code = source_between("function renderPlayerRiver(", "function renderHeroHand(")
    script = """const assert = require('assert');
const container = {dataset:{},innerHTML:''};
const state={gameId:'game',kyokuIdx:0,targetActor:0,reviewData:null};
const $=()=>container, tileCn=x=>x, escapeHtml=x=>x;
""" + code + """
const tile=(pai)=>({pai,tsumogiri:false,riichi:false,called:false});
const snap=r=>({rivers:[r],event:{type:'dahai',actor:0}});
renderPlayerRiver('Top',0,snap([tile('N'),tile('W'),tile('S')]));
renderPlayerRiver('Top',0,snap([tile('1m'),tile('9m'),tile('S')]));
assert(container.innerHTML.includes('/tiles/1m.svg'));
assert(!container.innerHTML.includes('/tiles/N.svg'));
"""
    run_js(script)


def test_kakan_stacks_actual_added_red_tile():
    code = source_between("function renderCallsHtml(", "function renderDecisionTileOrBadge(")
    script = """const assert = require('assert');
const getTileBackSrc=()=>'/back.svg';
""" + code + """
const meld={type:'kakan',pai:'5m',consumed:['5m','5m'],added:'5mr',tiles:['5m','5m','5m','5mr'],from:3};
const html=renderCallsHtml([meld],0,1);
assert(html.includes('has-float"><img src="/tiles/5mr.svg"'),html);
assert((html.match(/5m(?:r)?.svg/g)||[]).length===4);
"""
    run_js(script)


def test_remote_tokens_are_bound_to_backend_origin():
    code = source_between("function getApiToken(", "function apiAuthHeaders(")
    script = """const assert = require('assert');
let backend='https://api-a.example';
const getBackendUrl=()=>backend, saved=new Map();
const sessionStorage={setItem:(k,v)=>saved.set(k,v),getItem:k=>saved.get(k)||null};
const window={location:new URL('https://frontend.example/#api_token=secret'),history:{replaceState:(_a,_b,u)=>{window.location=new URL(u)}}};
""" + code + """
assert.strictEqual(getApiToken(),'secret');
assert.strictEqual(window.location.hash,'');
backend='https://api-b.example';
assert.strictEqual(getApiToken(),'');
backend='https://api-a.example';
assert.strictEqual(getApiToken(),'secret');
"""
    run_js(script)
