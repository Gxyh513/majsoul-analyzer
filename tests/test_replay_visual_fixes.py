"""Offline regressions for replay seats, winning tiles, calls and settlements."""
import shutil
import subprocess
from pathlib import Path

import pytest


SOURCE = Path("web/app.js").read_text(encoding="utf8")


def section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end)]


def run_js(script):
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


def test_round_table_keeps_fixed_identities_and_current_hand_winds():
    sanitizer = section("function sanitizeSummaryText(", "function toggleHidePlayerId(")
    escaping = section("function escapeHtml(", "function tileCn(")
    hand_wind = section("function handWind(", "function renderSummaryDashboard(")
    yaku = section("const MAJSOUL_YAKU_NAMES", "function getKillerActionSpan(")
    table = section("function getRoundDeltas(", "function openRoundScoresModal(")
    run_js(r'''const assert=require('assert');
const WIND_CN=['东','南','西','北'];
const buttons={};
const nodes={
  roundModalSub:{innerHTML:''}, roundTableHead:{innerHTML:''},
  roundTableBody:{innerHTML:'',querySelectorAll:()=>[]}, roundTableFoot:{innerHTML:''},
  roundModalPovTabs:{innerHTML:'',querySelectorAll:()=>[0,1,2,3].map(s=>({
    dataset:{seat:String(s)},addEventListener:(_type,fn)=>{buttons[s]=fn}
  }))}
};
const $=id=>nodes[id];
const names=['<img src=x onerror=1>','Victim<script>','Player C','Player D'];
const ron1={type:'hora',actor:2,target:1,han:3,fu:30,deltas:[0,-13200,5200,8000]};
const ron2={type:'hora',actor:3,target:1,han:3,fu:40};
const rounds=[
  {label:'东1局',kyoku_num:1,oya:0,honba:0,init_scores:[25000,25000,25000,25000],
   end_scores:[31000,22000,24000,23000],steps:[],end_summaries:['荒牌流局']},
  {label:'东2局',kyoku_num:2,oya:1,honba:1,init_scores:[31000,22000,24000,23000],
   end_scores:[31000,8800,29200,31000],steps:[{event:ron1},{event:ron2}],
   end_summaries:['Player C 荣和 Victim<script> 30符3番 +5200',
                  'Player D 荣和 Victim<script> 40符3番 +8000']}
];
const state={targetActor:2,kyokuIdx:1,reviewData:{names,kyokus:rounds}};
const getPlayerDisplayName=()=>{throw new Error('dynamic identities cannot label fixed columns')};
const switchSeatPerspective=async s=>{state.targetActor=s;renderKyokuTable()};
const selectKyoku=()=>{},closeModal=()=>{};
''' + sanitizer + escaping + hand_wind + yaku + table + r'''
const scores=html=>[...html.matchAll(/<span class="score-num">([^<]+)<\/span>/g)]
  .map(m=>Number(m[1].replace(/,/g,'')));
(async()=>{
  renderKyokuTable();
  assert(nodes.roundModalSub.innerHTML.includes('南家 (自家)'),nodes.roundModalSub.innerHTML);
  assert(nodes.roundModalSub.innerHTML.includes('起始西家'),nodes.roundModalSub.innerHTML);
  for(const wind of WIND_CN) assert(nodes.roundTableHead.innerHTML.includes('起始'+wind+'家'));
  assert(nodes.roundModalPovTabs.innerHTML.includes('本局南家'));
  assert.deepStrictEqual(scores(nodes.roundTableBody.innerHTML),[31000,22000,24000,23000,31000,8800,29200,31000]);
  assert.deepStrictEqual(scores(nodes.roundTableFoot.innerHTML),[31000,8800,29200,31000]);
  assert(nodes.roundTableBody.innerHTML.includes('南家 (自家) 荣和 东家'));
  assert(nodes.roundTableBody.innerHTML.includes('西家 荣和 东家'));
  assert(!nodes.roundTableBody.innerHTML.includes('Player C'));
  assert(!nodes.roundTableBody.innerHTML.includes('Victim<script>'));
  assert(!nodes.roundTableBody.innerHTML.includes('<img'));
  const head=nodes.roundTableHead.innerHTML.replace(/active-pov-th/g,'').replace(/ <span[^>]*>\(自家\)<\/span>/g,'');
  await buttons[3]();
  assert(nodes.roundModalSub.innerHTML.includes('西家 (自家)'),nodes.roundModalSub.innerHTML);
  assert(nodes.roundModalSub.innerHTML.includes('起始北家'));
  assert.deepStrictEqual(scores(nodes.roundTableBody.innerHTML),[31000,22000,24000,23000,31000,8800,29200,31000]);
  assert.strictEqual(nodes.roundTableHead.innerHTML.replace(/active-pov-th/g,'').replace(/ <span[^>]*>\(自家\)<\/span>/g,''),head);
  assert(nodes.roundTableBody.innerHTML.includes('南家 荣和 东家'));
  assert(nodes.roundTableBody.innerHTML.includes('西家 (自家) 荣和 东家'));
})().catch(e=>{console.error(e);process.exit(1)});
''')


def test_round_table_infers_starting_seats_and_handles_missing_metadata():
    sanitizer = section("function sanitizeSummaryText(", "function toggleHidePlayerId(")
    escaping = section("function escapeHtml(", "function tileCn(")
    hand_wind = section("function handWind(", "function renderSummaryDashboard(")
    yaku = section("const MAJSOUL_YAKU_NAMES", "function getKillerActionSpan(")
    table = section("function getRoundDeltas(", "function openRoundScoresModal(")
    run_js(r'''const assert=require('assert');
const WIND_CN=['东','南','西','北'];
const nodes={roundTableHead:{innerHTML:''},roundTableBody:{innerHTML:'',querySelectorAll:()=>[]},
  roundModalSub:{innerHTML:''},roundTableFoot:{innerHTML:''}};
const $=id=>nodes[id], getPlayerDisplayName=()=>'',selectKyoku=()=>{},closeModal=()=>{};
const state={targetActor:1,kyokuIdx:0,reviewData:{names:['Private A','Private B','C','D'],kyokus:[
  {oya:2,kyoku_num:2,label:'东2局',steps:[],end_scores:[1,2,3,4],end_summaries:['Private A 荣和 Private B 30符1番 +1000']}
]}};
''' + sanitizer + escaping + hand_wind + yaku + table + r'''
renderKyokuTable();
const headers=[...nodes.roundTableHead.innerHTML.matchAll(/起始[东南西北]家/g)].map(x=>x[0]);
assert.deepStrictEqual(headers,['起始北家','起始东家','起始南家','起始西家']);
assert(!nodes.roundTableBody.innerHTML.includes('Private A'));
assert(!nodes.roundTableBody.innerHTML.includes('Private B'));
assert(nodes.roundTableBody.innerHTML.includes('西家 荣和 北家 (自家) 30符1番 +1000'));
state.reviewData.kyokus[0]={steps:[],end_scores:[1,2,3,4],result:'<img src=x onerror=1>'};
renderKyokuTable();
for(let seat=1;seat<=4;seat++) assert(nodes.roundTableHead.innerHTML.includes('起始座位'+seat));
assert(!nodes.roundTableBody.innerHTML.includes('<img'));
// Missing score data is unknown, not a fabricated starting score or a zero delta.
state.reviewData.kyokus[0]={label:'东1局',oya:0,kyoku_num:1,steps:[],result:'流局'};
renderKyokuTable();
assert(!nodes.roundTableBody.innerHTML.includes('25,000'));
assert(!nodes.roundTableFoot.innerHTML.includes('25,000'));
assert.strictEqual((nodes.roundTableBody.innerHTML.match(/class="score-num">—/g)||[]).length,4);
assert.strictEqual((nodes.roundTableFoot.innerHTML.match(/class="score-num">—/g)||[]).length,4);
assert.strictEqual((nodes.roundTableBody.innerHTML.match(/delta-zero">—/g)||[]).length,5);
assert.strictEqual((nodes.roundTableFoot.innerHTML.match(/delta-zero">—/g)||[]).length,5);
assert(!nodes.roundTableFoot.innerHTML.includes('位]'));
''')


def test_ron_river_markers_follow_actual_discard_or_kakan_history():
    board = section("function renderBoard(", "function renderHeroHand(")
    run_js(r'''const assert=require('assert');
const rivers={Bottom:{dataset:{},innerHTML:''},Right:{dataset:{},innerHTML:''},
  Top:{dataset:{},innerHTML:''},Left:{dataset:{},innerHTML:''}};
const $=id=>id.startsWith('river')?rivers[id.slice(5)]:null;
const state={targetActor:0,stepIdx:0,kyokuIdx:0,gameId:'fixture'};
const handWind=()=>'',tileCn=x=>x,escapeHtml=x=>String(x),getTileBackSrc=()=>'/back.svg';
let heroArgs; const renderHeroHand=(...args)=>heroArgs=args,renderOpponentHand=()=>{};
''' + board + r'''
const discarded=(actor,pai)=>({event:{type:'dahai',actor,pai}});
const kan=(actor,pai)=>({event:{type:'kakan',actor,pai}});
const win=(actor=0,target=1,pai='5mr')=>({event:{type:'hora',actor,target,pai},
  rivers:[[],[{pai:'1p',step:0},{pai:'5mr',step:1}],[],[]],drawn:[]});
function render(steps,snap) {
  const kyoku={oya:0,steps}; state.stepIdx=steps.indexOf(snap);
  renderBoard(kyoku,snap,null); return rivers.Right.innerHTML;
}
const d=discarded(1,'5mr'), first=win(); first.rivers[1][1].step=1;
let steps=[discarded(1,'1p'),d,first];
assert(render(steps,first).includes('ron-claimed'));
assert.strictEqual(heroArgs[3],'5mr');
assert.strictEqual((rivers.Right.innerHTML.match(/ron-claimed/g)||[]).length,1);
const second=win(2,1,'5mr'); steps=[discarded(1,'1p'),d,first,second];
assert(render(steps,second).includes('ron-claimed')); // double ron uses the same discard
const rob=win(); rob.event.pai=undefined; rob.rivers[1][1].step=0;
steps=[discarded(1,'5mr'),kan(1,'5mr'),rob];
assert(!render(steps,rob).includes('ron-claimed')); // same tile value does not make a discard
assert.strictEqual(heroArgs[3],'5mr'); // winning tile comes from kakan
const rob2=win(2); steps=[discarded(1,'5mr'),kan(1,'5mr'),rob,rob2];
assert(!render(steps,rob2).includes('ron-claimed'));
const contradictory=win(); contradictory.event.fans=[{id:3,val:1}];
steps=[discarded(1,'1p'),d,contradictory];
assert(!render(steps,contradictory).includes('ron-claimed')); // old cache missing kakan
const stale=win(); steps=[discarded(1,'1p'),d,{event:{type:'tsumo',actor:1,pai:'4p'}},stale];
assert(!render(steps,stale).includes('ron-claimed')); // stale earlier discard
const unknown=win(); steps=[unknown];
assert(!render(steps,unknown).includes('ron-claimed')); // no source event in cache
const noStep=win(); delete noStep.rivers[1][1].step; noStep.event.pai=undefined;
steps=[discarded(1,'1p'),d,noStep];
assert(render(steps,noStep).includes('ron-claimed')); // old rivers may omit step
assert.strictEqual(heroArgs[3],'5mr');
const mismatch=win(); mismatch.rivers[1][1].step=0;
steps=[discarded(1,'1p'),d,mismatch];
assert(!render(steps,mismatch).includes('ron-claimed')); // step disproves same-valued tile
const called=win(); called.rivers[1][1].called=true;
steps=[discarded(1,'1p'),d,called];
assert(!render(steps,called).includes('ron-claimed'));
''')


def test_daiminkan_opens_four_tiles_and_kakan_alone_stacks():
    calls = section("function renderCallsHtml(", "function renderDecisionTileOrBadge(")
    run_js(r'''const assert=require('assert'),getTileBackSrc=()=>'/back.svg';
''' + calls + r'''
function tiles(html) {
  return [...html.matchAll(/<div class="([^"]+)"><img src="([^"]+)" class="([^"]+)"><\/div>/g)]
    .map(([,div,src,img])=>({div,src,img}));
}
for(const pov of [0,1,2,3]) for(const [from,calledIndex] of [[3,0],[2,1],[1,3]]) {
  const meld={type:'daiminkan',pai:'5mr',consumed:['5m','5m','5m'],from};
  const result=tiles(renderCallsHtml([meld],0,pov));
  assert.strictEqual(result.length,4);
  assert.deepStrictEqual(result.map(t=>t.div.includes('has-rotate')),
    [0,1,2,3].map(i=>i===calledIndex));
  assert(result[calledIndex].src.includes('5mr.svg'));
  assert(result.every(t=>!t.div.includes('has-float')&&!t.img.includes('float')));
}
const legacy=tiles(renderCallsHtml([{type:'daiminkan',pai:'5mr',tiles:['5mr','5m','5m','5m'],from:1}],0,0));
assert.strictEqual(legacy.length,4);
assert(legacy[3].src.includes('5mr.svg') && legacy[3].div.includes('has-rotate'));
assert.strictEqual(legacy.filter(t=>t.src.includes('5mr.svg')).length,1);
for(const [pai,added] of [['5m','5mr'],['5mr','5m']]) {
  const meld={type:'kakan',pai,added,consumed:['5m','5m'],tiles:[pai,'5m','5m',added],from:2};
  for(const candidate of [meld,{type:'kakan',pai,tiles:meld.tiles,from:2}]) {
    const result=tiles(renderCallsHtml([candidate],0,0));
    assert.strictEqual(result.length,4);
    assert(result[1].div.includes('has-rotate')&&!result[1].div.includes('has-float'));
    assert(result[1].src.includes('/'+pai+'.svg'));
    assert(result[2].div.includes('has-rotate')&&result[2].div.includes('has-float'));
    assert(result[2].src.includes('/'+added+'.svg'));
    assert.strictEqual(result.filter(t=>t.div.includes('has-float')).length,1);
  }
}
const ankan=tiles(renderCallsHtml([{type:'ankan',consumed:['5m','5mr','5m','5m']}],0,0));
assert.strictEqual(ankan.length,4); assert.strictEqual(ankan[0].src,'/back.svg');
assert(ankan[1].src.includes('5mr.svg')); assert.strictEqual(ankan[3].src,'/back.svg');
''')


def test_draw_settlements_show_event_deltas_and_status_not_payments():
    yaku = section("const MAJSOUL_YAKU_NAMES", "function getKillerActionSpan(")
    render = section("function renderThisRoundModalContent(", "function renderDecisionCard(")
    run_js(r'''const assert=require('assert');
const content={innerHTML:''}, state={targetActor:0};
const $=id=>id==='infoThisRoundContent'?content:null,escapeHtml=s=>String(s);
''' + yaku + render + r'''
const values=()=>[...content.innerHTML.matchAll(/>([+-]?\d+|—)<\/td>/g)].map(m=>m[1]);
const snap={event:{type:'ryukyoku',deltas:[1500,-1500,0,0],majsoul:{reason:'exhaustive'}},
  honba:2,kyotaku:3,riichi:[true,false,false,false]};
const kyoku={score_deltas:[500,-1500,0,0]};
renderThisRoundModalContent(snap,kyoku);
assert(content.innerHTML.includes('荒牌流局'));
assert.deepStrictEqual(values(),['+1500','-1500','0','0']);
assert(content.innerHTML.includes('2本场')&&content.innerHTML.includes('3根'));
assert(!content.innerHTML.includes('本场供')&&!content.innerHTML.includes('立直场供'));
assert(!content.innerHTML.includes('>600</td>')&&!content.innerHTML.includes('>3000</td>'));
snap.event.majsoul.reason='suufon_renda'; snap.event.deltas=[0,0,0,0];
renderThisRoundModalContent(snap,kyoku);
assert(content.innerHTML.includes('四风连打'));
assert.deepStrictEqual(values(),['0','0','0','0']);
assert(!content.innerHTML.includes('本场供')&&!content.innerHTML.includes('立直场供'));
snap.event.majsoul.reason='nagashi_mangan'; snap.event.deltas=[8000,-4000,-2000,-2000];
renderThisRoundModalContent(snap,kyoku);
assert(content.innerHTML.includes('流局满贯'));
assert.deepStrictEqual(values(),['+8000','-4000','-2000','-2000']);
assert(!content.innerHTML.includes('本场供')&&!content.innerHTML.includes('立直场供'));
delete snap.event.deltas; snap.event.majsoul.reason='exhaustive';
renderThisRoundModalContent(snap,kyoku);
assert.deepStrictEqual(values(),['—','—','—','—']);
assert(!content.innerHTML.includes('无罚符结算'));
''')


def test_hora_settlements_do_not_invent_missing_or_multi_ron_scores():
    yaku = section("const MAJSOUL_YAKU_NAMES", "function getKillerActionSpan(")
    render = section("function renderThisRoundModalContent(", "function renderDecisionCard(")
    run_js(r'''const assert=require('assert');
const content={innerHTML:''}, state={targetActor:0};
const $=id=>id==='infoThisRoundContent'?content:null,escapeHtml=s=>String(s);
''' + yaku + render + r'''
const values=()=>[...content.innerHTML.matchAll(/>([+-]?\d+|—)<\/td>/g)].map(m=>m[1]);
const single={event:{type:'hora',actor:0,target:1,han:3,fu:30,deltas:[5200,-5200,0,0]},honba:2,kyotaku:1};
renderThisRoundModalContent(single,{score_deltas:[4200,-5200,0,0]});
assert.deepStrictEqual(values(),['+5200','-5200','0','0']);
assert(content.innerHTML.includes('5200 点'));
assert(!content.innerHTML.includes('本场供')&&!content.innerHTML.includes('立直场供'));
const first={event:{type:'hora',actor:0,target:1,deltas:[5200,-13200,8000,0]},honba:2,kyotaku:3};
const second={event:{type:'hora',actor:2,target:1},honba:2,kyotaku:3};
const round={steps:[first,second],score_deltas:[4200,-13200,8000,0]};
renderThisRoundModalContent(first,round);
assert.deepStrictEqual(values(),['+5200','-13200','+8000','0']);
assert(content.innerHTML.includes('多家荣和') && content.innerHTML.includes('合计'));
assert(!content.innerHTML.includes('5200 点'));
assert(!content.innerHTML.includes('本场供')&&!content.innerHTML.includes('立直场供'));
// Old cache: a missing second hora step must not turn the aggregate into an individual award.
renderThisRoundModalContent(first,{steps:[first],end_summaries:['A 荣和 B +5200','C 荣和 B +8000']});
assert(content.innerHTML.includes('多家荣和') && content.innerHTML.includes('合计'));
assert(!content.innerHTML.includes('5200 点'));
renderThisRoundModalContent(second,round);
assert(content.innerHTML.includes('对家 荣和 下家'));
assert.deepStrictEqual(values(),['—','—','—','—']);
assert(content.innerHTML.includes('未提供'));
assert(!content.innerHTML.includes('8000 点'));
second.event.deltas=[0,-8000,8000,0];
renderThisRoundModalContent(second,round);
assert.deepStrictEqual(values(),['0','-8000','+8000','0']);
''')


def test_settlement_untrusted_fields_remain_escaped():
    escaping = section("function escapeHtml(", "function tileCn(")
    yaku = section("const MAJSOUL_YAKU_NAMES", "function getKillerActionSpan(")
    render = section("function renderThisRoundModalContent(", "function renderDecisionCard(")
    run_js(r'''const assert=require('assert'),content={innerHTML:''},state={targetActor:0};
const $=id=>id==='infoThisRoundContent'?content:null;
''' + escaping + yaku + render + r'''
const event={type:'hora',actor:0,target:1,fu:'<img src=x onerror=1>',han:'<svg onload=1>',
  fans:[{id:999,val:1,name:'<img src=x onerror=1>'}],deltas:['<img src=x>',0,0,0]};
renderThisRoundModalContent({event,honba:'<img>',kyotaku:'<img>'},{});
assert(!content.innerHTML.includes('<img'));
assert(!content.innerHTML.includes('<svg'));
assert(content.innerHTML.includes('&lt;img'));
assert(content.innerHTML.includes('—'));
''')
