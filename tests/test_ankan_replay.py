"""Replay a ron claimed from a concealed kan, not an earlier discard."""
from test_replay_visual_fixes import run_js, section


def test_robbing_ankan_uses_kan_tile_without_marking_river():
    board = section("function renderBoard(", "function renderHeroHand(")
    run_js(r'''const assert=require('assert');
const rivers={Bottom:{dataset:{},innerHTML:''},Right:{dataset:{},innerHTML:''},
  Top:{dataset:{},innerHTML:''},Left:{dataset:{},innerHTML:''}};
const $=id=>id.startsWith('river')?rivers[id.slice(5)]:null;
const state={targetActor:0,stepIdx:0,kyokuIdx:0,gameId:'fixture'};
const handWind=()=>'',tileCn=x=>x,escapeHtml=x=>String(x),getTileBackSrc=()=>'/back.svg';
let heroArgs; const renderHeroHand=(...args)=>heroArgs=args,renderOpponentHand=()=>{};
''' + board + r'''
const discard={event:{type:'dahai',actor:1,pai:'3m'}};
const kan={event:{type:'ankan',actor:1,consumed:['E','E','E','E']}};
const win={event:{type:'hora',actor:0,target:1,pai:'E'},
  rivers:[[],[{pai:'3m',step:0}],[],[]],drawn:[]};
let steps=[discard,kan,win], kyoku={oya:0,steps}; state.stepIdx=2;
renderBoard(kyoku,win,null);
assert(!rivers.Right.innerHTML.includes('ron-claimed'));
assert.strictEqual(heroArgs[3],'E');
const older={...win,event:{type:'hora',actor:0,target:1}};
steps=[discard,kan,older]; kyoku={oya:0,steps}; state.stepIdx=2;
renderBoard(kyoku,older,null);
assert(!rivers.Right.innerHTML.includes('ron-claimed'));
assert.strictEqual(heroArgs[3],'E');
const second={...win,event:{type:'hora',actor:2,target:1}};
steps=[discard,kan,win,second]; kyoku={oya:0,steps}; state.stepIdx=3;
renderBoard(kyoku,second,null);
assert(!rivers.Right.innerHTML.includes('ron-claimed'));
assert.strictEqual(heroArgs[3],'E');
''')
