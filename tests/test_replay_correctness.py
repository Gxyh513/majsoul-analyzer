"""Browser-independent checks for replay rendering and navigation."""
import shutil
import subprocess
from pathlib import Path
import pytest

SOURCE = Path('web/app.js').read_text(encoding='utf8')

def section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end)]

def run_js(code):
    if not shutil.which('node'):
        pytest.skip('Node.js not installed')
    proc = subprocess.run(['node', '-e', code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_board_uses_snapshot_scores_and_ron_tile():
    code = section('function renderBoard(', 'function renderPlayerRiver(')
    run_js('''const assert=require('assert');
const nodes={giP0:{},giP1:{},giP2:{},giP3:{}};
const $=id=>nodes[id], state={targetActor:0,stepIdx:0};
const handWind=x=>String(x), renderPlayerRiver=()=>{}, renderHeroHand=(...args)=>{globalThis.heroArgs=args}, renderOpponentHand=()=>{};
''' + code + '''
const kyoku={init_scores:[25000,25000,25000,25000],steps:[]};
const snap={scores:[24000,25000,25000,25000],rivers:[[],[{pai:'1m'}],[],[]],drawn:[],event:{type:'hora',actor:0,target:1,pai:'5mr'}};
renderBoard(kyoku,snap,null);
assert(nodes.giP0.innerHTML.includes('24000'));
assert.strictEqual(heroArgs[3], '5mr');
''')


def test_ron_tile_renders_for_hero_and_opponent():
    code = section('function renderHeroHand(', 'function renderCallsHtml(')
    run_js('''const assert=require('assert');
const nodes={handBottomClosed:{innerHTML:''},handRightClosed:{innerHTML:''}};
const $=id=>nodes[id], state={openHands:false};
const escapeHtml=x=>x, tileCn=x=>x, getTileBackSrc=()=>'/back.svg';
''' + code + '''
const snap={hands:[['1m'],['2m'],[],[]],drawn:[null,null,null,null],melds:[[],[],[],[]]};
renderHeroHand(0,snap,null,'5mr',0,true,null);
assert(nodes.handBottomClosed.innerHTML.includes('/tiles/5mr.svg'));
renderOpponentHand('Right',1,snap,'5mr',1,true,null);
assert(nodes.handRightClosed.innerHTML.includes('/tiles/5mr.svg'));
renderOpponentHand('Right',1,snap,'5mr',0,true,null);
assert(!nodes.handRightClosed.innerHTML.includes('/tiles/5mr.svg'));
''')


def test_replay_cross_round_starts_at_first_step():
    code = section('function stepDelta(', 'function getPlayIntervalMs(')
    run_js('''const assert=require('assert');
const state={kyokuIdx:0,stepIdx:1,reviewData:{kyokus:[{steps:[{},{}]},{steps:[{},{},{}],decisions:[{step_idx:2}]}]}};
function selectKyoku(k,jump=false){state.kyokuIdx=k;state.stepIdx=jump?2:0};
function setStep(i){state.stepIdx=i}; function togglePlay(){}
''' + code + '''
stepDelta(1); assert.strictEqual(state.kyokuIdx,1); assert.strictEqual(state.stepIdx,0);
''')


def test_error_navigation_skips_empty_round():
    code = section('function jumpPrevBlunder()', '// ============================================================================\n// Modals:')
    run_js('''const assert=require('assert');
const state={kyokuIdx:0,stepIdx:0,reviewData:{kyokus:[{decisions:[]},{decisions:[{step_idx:2,is_equal:null},{step_idx:3,is_equal:false}]}]}};
function selectKyoku(k){state.kyokuIdx=k}; function setStep(s){state.stepIdx=s}; function showToast(){}
''' + code + '''
jumpNextBlunder(); assert.strictEqual(state.kyokuIdx,1); assert.strictEqual(state.stepIdx,3);
''')


def test_draw_reason_does_not_guess_unknown():
    code = section('function renderThisRoundModalContent(', 'function renderDecisionCard(')
    run_js('''const assert=require('assert'), content={innerHTML:''}; const state={targetActor:0};
const $=id=>id==='infoThisRoundContent'?content:null;
''' + code + '''
const snap={event:{type:'ryukyoku',deltas:[8000,-4000,-2000,-2000],majsoul:{reason:'nagashi_mangan'}}};
renderThisRoundModalContent(snap,{}); assert(content.innerHTML.includes('流局满贯')); assert(!content.innerHTML.includes('罚符结算'));
snap.event.majsoul={reason:'unknown'}; renderThisRoundModalContent(snap,{}); assert(!content.innerHTML.includes('荒牌流局'));
''')
