"""Run focused rendering regressions using Node without a browser dependency."""
import re
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
    code = source_between("function isPagesProxyMode()", "async function fetchReviewStream(")
    script = """const assert = require('assert');
let backend='https://api-a.example';
const getBackendUrl=()=>backend, saved=new Map();
const sessionStorage={setItem:(k,v)=>saved.set(k,v),getItem:k=>saved.get(k)||null};
const window={location:new URL('https://frontend.example/#api_token=secret'),history:{replaceState:(_a,_b,u)=>{window.location=new URL(u)}}};
""" + code + """
assert.strictEqual(getApiToken(),'secret');
assert.deepStrictEqual(apiAuthHeaders(), {"X-Api-Token":"secret"});
assert.strictEqual(window.location.hash,'');
backend='https://api-b.example';
assert.strictEqual(getApiToken(),'');
backend='https://api-a.example';
assert.strictEqual(getApiToken(),'secret');
"""
    run_js(script)


def test_token_not_in_urls_and_cross_origin_requests_do_not_receive_it():
    token_code = source_between("function isPagesProxyMode()", "async function fetchReviewStream(")
    api_code = source_between("function buildApiUrl(", "function isBackendOnline(")
    script = """const assert = require('assert');
const saved = new Map(), legacy = new Map([['majsoul_api_token:https://api.example','stale']]);
const sessionStorage={setItem:(k,v)=>saved.set(k,v),getItem:k=>saved.get(k)||null,removeItem:k=>saved.delete(k)};
const localStorage={get length(){return legacy.size},key:i=>[...legacy.keys()][i],removeItem:k=>legacy.delete(k)};
const window={location:new URL('https://frontend.example/#api_token=secret'),history:{replaceState:(_a,_b,u)=>{window.location=new URL(u)}}};
const getBackendUrl=()=> 'https://api.example/?api_token=old&other=ok';
let calls=[]; const fetch=async (url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({})}};
""" + token_code + api_code + """
(async()=>{
  assert.strictEqual(getApiToken(),'secret'); assert.strictEqual(legacy.size,0);
  const url=buildApiUrl(getBackendUrl(),'/api/test');
  assert(!url.includes('api_token')); assert(url.includes('other=ok'));
  await apiFetch('/api/test');
  assert.strictEqual(calls[0].options.headers['X-Api-Token'],'secret');
  assert.strictEqual(calls[0].options.redirect,'error');
  await apiFetch('https://other.example/api/test');
  assert(!calls[1].options.headers['X-Api-Token']);
  setApiToken(''); assert.strictEqual(getApiToken(),'');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    run_js(script)


def test_stream_sends_token_only_in_header():
    token_code = source_between("function isPagesProxyMode()", "async function fetchReviewStream(")
    stream_code = source_between("async function fetchReviewStream(", "const DEFAULT_MODELSCOPE_BACKEND")
    script = """const assert = require('assert');
const saved = new Map(), sessionStorage = {setItem:(k,v)=>saved.set(k,v),getItem:k=>saved.get(k)||null};
const window={location:new URL('https://frontend.example/#api_token=secret'),history:{replaceState:(_a,_b,u)=>{window.location=new URL(u)}}};
const getBackendUrl=()=> 'https://example.ms.show';
const buildApiUrl=(_backend,path)=>'https://example.ms.show'+path;
let seen;
const fetch=async (url,options)=>{seen={url,options};return {ok:true,body:{getReader:()=>({read:async()=>({done:true})})}}};
""" + token_code + stream_code + """
(async()=>{try{await fetchReviewStream({},()=>{});}catch(e){}
assert.strictEqual(seen.options.headers['X-Api-Token'],'secret');
assert.strictEqual(seen.options.redirect,'error'); assert(!seen.url.includes('secret'));
})().catch(e=>{console.error(e);process.exit(1)});
"""
    run_js(script)


def test_yakuman_settlement_uses_majsoul_ids_and_multipliers():
    names = source_between('const MAJSOUL_YAKU_NAMES', 'function getKillerActionSpan(')
    render = source_between('function renderThisRoundModalContent(', 'function renderDecisionCard(')
    script = '''const assert = require('assert');
const content={innerHTML:''}, state={targetActor:0};
const $=id=>id==='infoThisRoundContent'?content:null, escapeHtml=s=>String(s);
''' + names + render + '''
const expectedNames={3:'抢杠',5:'海底捞月',6:'河底捞鱼',15:'混全带幺九',18:'两立直',
  19:'三色同刻',20:'三杠子',21:'对对和',22:'三暗刻',23:'小三元',24:'混老头',
  28:'二杯口',29:'清一色',35:'天和',36:'地和',37:'大三元',38:'四暗刻',
  39:'字一色',40:'绿一色',41:'清老头',42:'国士无双',43:'小四喜',44:'四杠子',
  45:'九莲宝灯',46:'八连庄',47:'纯正九莲宝灯',48:'四暗刻单骑',
  49:'国士无双十三面',50:'大四喜',51:'燕返し',52:'槓振り',64:'大七星'};
for(const [id,name] of Object.entries(expectedNames)) assert.strictEqual(yakuName({id}),name);
assert.strictEqual(yakuName({id:'toString'}),'未知役种（ID toString）');
const event={type:'hora',actor:0,target:1,yiman:true,title_id:5,han:1,fu:50,
  fans:[{id:37,val:1}],deltas:[32000,-32000,0,0]};
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('大三元'));
assert(content.innerHTML.includes('役满'));
assert(!content.innerHTML.includes('混全带幺九'));
assert(!content.innerHTML.includes('50 符'));
assert(!content.innerHTML.includes('1 番'));

event.title_id=7; event.han=3;
event.fans=[{id:37,val:1},{id:48,val:2}];
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('3倍役满'));
assert(content.innerHTML.includes('2倍役满'));
assert(content.innerHTML.includes('四暗刻单骑'));
assert(!content.innerHTML.includes('3 番'));
// Authoritative total survives incomplete fan lists and conflicting title IDs.
event.fans=[{id:37,val:1}]; event.title_id=5;
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('3倍役满'));
event.han=0; event.title_id=7;
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('3倍役满'));
event.title_id=0; event.fans=[{id:37,val:1},{id:48,val:2}];
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('3倍役满'));

event.fans=[]; event.title_id=6; event.han=0;
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('2倍役满'));
event.title_id=10; event.han=6;
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('6倍役满'));
assert(!content.innerHTML.includes('6 番'));
event.title_id=0; event.han=4;
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('4倍役满'));

event.yiman=false; event.title_id=11; event.han=13; event.fu=40;
event.fans=[{id:29,val:6},{id:2,val:1},{id:31,val:6}];
renderThisRoundModalContent({event},{});
assert(content.innerHTML.includes('累计役满'));
assert(content.innerHTML.includes('40 符'));
assert(content.innerHTML.includes('13 番'));
assert(content.innerHTML.includes('6 番'));
assert(!content.innerHTML.includes('13倍役满'));

event.yiman=false; event.title_id=0; event.han=3; event.fu=30;
event.fans=[{id:1,val:1},{id:15,val:2},{id:28,val:1},{id:29,val:1},
            {id:63,val:1},{id:999,val:1},{id:998,val:1,name:'自定义役'}];
renderThisRoundModalContent({event},{});
for (const name of ['门前清自摸和','混全带幺九','二杯口','清一色','石の上にも三年',
                    '未知役种（ID 999）','自定义役','30 符','3 番']) {
  assert(content.innerHTML.includes(name),name);
}
'''
    run_js(script)


def test_old_cached_round_summaries_are_corrected_without_reanalysis():
    names = source_between('const MAJSOUL_YAKU_NAMES', 'function getKillerActionSpan(')
    script = '''const assert=require('assert');
const sanitizeSummaryText=text=>text;
''' + names + '''
const single={type:'hora',yiman:true,title_id:5,fu:50,han:1,fans:[{id:37,val:1}]};
const triple={type:'hora',yiman:true,title_id:7,fu:40,han:3,
              fans:[{id:37,val:1},{id:48,val:2}]};
const ordinary={type:'hora',yiman:false,fu:40,han:3,fans:[{id:15,val:2}]};
const kyoku={steps:[{event:ordinary},{event:single},{event:triple}],
  end_summaries:['A 荣和 B 40符3番 +5200','C 荣和 B 50符1番 +32000','D 荣和 B 40符3番 +96000']};
const fixed=correctedRoundSummaries(kyoku);
assert.strictEqual(fixed[0],kyoku.end_summaries[0]);
assert.strictEqual(fixed[1],'C 荣和 B 役满（大三元） +32000');
assert.strictEqual(fixed[2],'D 荣和 B 3倍役满（大三元、四暗刻单骑） +96000');
kyoku.steps=[{event:single}]; kyoku.end_summaries=['A 自摸 +32000'];
assert.strictEqual(correctedRoundSummaries(kyoku)[0],'A 自摸 役满（大三元） +32000');
kyoku.end_summaries=['A 自摸 役满（大三元） +32000'];
assert.strictEqual(correctedRoundSummaries(kyoku)[0],kyoku.end_summaries[0]);
const counted={type:'hora',yiman:false,title_id:11,fu:40,han:13,fans:[{id:29,val:6}]};
kyoku.steps=[{event:counted}]; kyoku.end_summaries=['A 自摸 40符13番 +32000'];
assert.strictEqual(correctedRoundSummaries(kyoku)[0],'A 自摸 累计役满 40符13番 +32000');
kyoku.end_summaries=['A 自摸 累计役满 40符13番 +32000'];
assert.strictEqual(correctedRoundSummaries(kyoku)[0],kyoku.end_summaries[0]);
'''
    run_js(script)
    assert 'summaries.map(escapeHtml).join("<br>")' in Path('web/app.js').read_text(encoding='utf8')


def test_round_summary_anonymization_does_not_replace_yaku_names_or_html():
    sanitizer = source_between('function sanitizeSummaryText(', 'function toggleHidePlayerId(')
    escaping = source_between('function escapeHtml(', 'function tileCn(')
    names = source_between('const MAJSOUL_YAKU_NAMES', 'function getKillerActionSpan(')
    script = '''const assert=require('assert');
const WIND_CN=['东','南','西','北'];
const state={targetActor:0,reviewData:{names:['大三元','对手<img>','字一色','四暗刻']}};
const handWind=(seat,kyoku)=>WIND_CN[(seat-kyoku.oya+4)%4];
''' + sanitizer + escaping + names + '''
const yaku={type:'hora',actor:0,target:1,yiman:true,han:1,title_id:5,
            fans:[{id:37,val:1}]};
const custom={type:'hora',actor:2,target:1,yiman:true,han:1,title_id:5,
              fans:[{id:999,val:1,name:'<img onerror=1> 大三元'}]};
const ordinary={type:'hora',actor:3,target:1,yiman:false,han:3,fu:40};
const kyoku={oya:0,steps:[{event:yaku},{event:custom},{event:ordinary}],end_summaries:[
  '大三元 荣和 对手<img> 50符1番 +32000',
  '字一色 荣和 对手<img> 役满（<img onerror=1> 大三元） +32000',
  '四暗刻 荣和 对手<img> 40符3番 +5200'
]};
const corrected=correctedRoundSummaries(kyoku);
assert.strictEqual(corrected[0],'东家 (自家) 荣和 南家 役满（大三元） +32000');
assert.strictEqual(corrected[1],'西家 荣和 南家 役满（<img onerror=1> 大三元） +32000');
assert.strictEqual(corrected[2],'北家 荣和 南家 40符3番 +5200');
const html=corrected.map(escapeHtml).join('<br>');
assert(!html.includes('<img'));
assert(html.includes('&lt;img onerror=1&gt; 大三元'));
assert(!html.includes('对手'));
assert.strictEqual((html.match(/<br>/g)||[]).length,2);
'''
    run_js(script)


def test_decision_highlights_actual_physical_discard():
    code = source_between('function renderHeroHand(', 'function renderOpponentHand(')
    script = '''const assert = require('assert');
const closed={innerHTML:''}, nodes={handBottomClosed:closed};
const $=id=>nodes[id], escapeHtml=s=>String(s), tileCn=s=>s;
''' + code + '''
const snap={hands:[['1m','3p','5m']],drawn:['1m'],melds:[[]]};
const decision=(pai,tsumogiri)=>({actual:{type:'dahai',pai,tsumogiri}});
function highlighted() {
  return (closed.innerHTML.match(/<div class="tileDiv[^>]*>.*?<\\/div>/g)||[])
    .filter(el=>el.includes('tileImgHighlight'));
}
renderHeroHand(0,snap,decision('1m',true));
assert.strictEqual(highlighted().length,1);
assert(highlighted()[0].includes('is-drawn'));
renderHeroHand(0,snap,decision('1m',false));
assert.strictEqual(highlighted().length,1);
assert(!highlighted()[0].includes('is-drawn'));
renderHeroHand(0,snap,null,null,null,false,{event:{type:'dahai',actor:0,pai:'1m',tsumogiri:true}});
assert(highlighted()[0].includes('is-drawn'));
const next={event:{type:'dahai',actor:0,pai:'1m',tsumogiri:true}};
renderHeroHand(0,snap,{actual:{type:'dahai',pai:'1m'}},null,null,false,next);
assert(highlighted()[0].includes('is-drawn'));
renderHeroHand(0,snap,decision('1m',false),null,null,false,next);
assert(!highlighted()[0].includes('is-drawn')); // explicit decision flag wins
renderHeroHand(0,snap,{actual:{type:'reach',actor:0,pai:'1m'}},null,null,false,next);
assert(highlighted()[0].includes('is-drawn'));
next.event.tsumogiri=false;
renderHeroHand(0,snap,{actual:{type:'reach',actor:0,pai:'1m'}},null,null,false,next);
assert(!highlighted()[0].includes('is-drawn'));
next.event.tsumogiri=true; next.event.actor=1;
renderHeroHand(0,snap,{actual:{type:'reach',actor:0,pai:'1m'}},null,null,false,next);
assert(!highlighted()[0].includes('is-drawn')); // unrelated player's action ignored
renderHeroHand(0,snap,decision('5m',true));
assert.strictEqual(highlighted().length,0); // inconsistent record: do not accuse another tile
for (const type of ['chi','pon','daiminkan','kakan','hora']) {
  renderHeroHand(0,snap,{actual:{type,pai:'1m',consumed:['1m','1m']}});
  assert.strictEqual(highlighted().length,0,`${type} is not a discard`);
}
snap.drawn[0]='5mr';
renderHeroHand(0,snap,decision('5mr',true));
assert(highlighted()[0].includes('is-drawn'));
const kan={hands:[['P','P','P']],drawn:['P'],melds:[[]]};
renderHeroHand(0,kan,{actual:{type:'ankan',consumed:['P','P','P','P']}});
assert.strictEqual(highlighted().length,4);
kan.hands[0]=['5m','5m','5m','5p']; kan.drawn[0]='5mr';
renderHeroHand(0,kan,{actual:{type:'ankan',consumed:['5mr','5m','5m','5m']}});
assert.strictEqual(highlighted().length,4);
assert(highlighted().some(el=>el.includes('data-pai="5mr"')));
assert(!highlighted().some(el=>el.includes('data-pai="5p"')));
renderHeroHand(0,kan,{actual:{type:'ankan',consumed:['5m','5mr','5m','5m']}});
assert.strictEqual(highlighted().length,4);
'''
    run_js(script)


def test_board_riichi_finds_actual_discard_after_declaration_snapshot():
    code = source_between('function renderBoard(', 'function renderPlayerRiver(')
    script = '''const assert=require('assert');
const state={targetActor:0,stepIdx:0};
const $=()=>null, renderPlayerRiver=()=>{}, handWind=()=>'';
let nextForHero, nextForOpponents=[];
const renderHeroHand=(...args)=>nextForHero=args[6];
const renderOpponentHand=(...args)=>nextForOpponents.push(args[6]);
''' + code + '''
const current={event:{type:'tsumo',actor:0},rivers:[[],[],[],[]],drawn:['1m']};
const reach={event:{type:'reach',actor:0}};
const discard={event:{type:'dahai',actor:0,pai:'1m',tsumogiri:true}};
const kyoku={steps:[current,reach,discard]};
renderBoard(kyoku,current,{actual:{type:'reach',actor:0,pai:'1m'}});
assert.strictEqual(nextForHero,discard);
assert(nextForOpponents.every(next=>next===reach));
kyoku.steps=[current,discard];
renderBoard(kyoku,current,{actual:{type:'reach',actor:0,pai:'1m'}});
assert.strictEqual(nextForHero,discard);
kyoku.steps=[current,{event:{type:'reach',actor:1}},discard];
renderBoard(kyoku,current,{actual:{type:'reach',actor:0,pai:'1m'}});
assert.strictEqual(nextForHero,kyoku.steps[1]);
'''
    run_js(script)


def test_responsive_zoom_fits_mobile_css_viewport_and_resizes():
    code = source_between('function updateResponsiveZoom()', 'let resizeRafId = null;')
    script = '''const assert = require('assert');
let current;
const root={clientWidth:0,style:{setProperty:(name,value)=>{assert.strictEqual(name,'--zoom');current=Number(value)}}};
const document={documentElement:root,body:{clientWidth:0}}, window={innerWidth:0,devicePixelRatio:3};
''' + code + '''
for (const width of [320,360,375,440,680,768,956,1440]) {
  window.innerWidth=root.clientWidth=document.body.clientWidth=width;
  updateResponsiveZoom();
  const padding=width<=768?8:width<=960?16:24;
  assert(current<=1 && current>0);
  assert(current*655 <= width-padding+1e-9,`${width}: ${current}`);
  if(width===440) assert(Math.abs(current*655-432)<1e-9);
  if(width===956 || width===1440) assert.strictEqual(current,1);
}
// Root viewport excludes classic scrollbars; an old body width is irrelevant.
for(const [width,scrollbar] of [[320,0],[956,0],[440,0],[320,17],[956,17],[440,17],[1440,17],[360,17]]) {
  window.innerWidth=width; root.clientWidth=width-scrollbar; document.body.clientWidth=312;
  updateResponsiveZoom();
  const padding=width<=768?8:width<=960?16:24;
  const expected=Math.min(1,(width-scrollbar-padding)/655);
  assert(Math.abs(current-expected)<1e-12);
  assert(current*655<=width-scrollbar-padding+1e-9);
}
'''
    run_js(script)
    css = Path('web/styles.css').read_text(encoding='utf8')
    assert 'width: calc(var(--zoom) * 655px) !important' in css
    assert 'margin-left: calc(var(--zoom) * 20px) !important' in css
    assert '--zoom: calc(' not in css


def test_board_center_rivers_and_tiles_scale_together():
    css = Path('web/styles.css').read_text(encoding='utf8')

    def declarations(selector):
        match = re.search(r'(?m)^' + re.escape(selector) + r'\s*\{([^}]*)\}', css)
        assert match, selector
        return {name: value.replace('!important', '').strip()
                for name, value in re.findall(r'([\w-]+)\s*:\s*([^;]+);', match.group(1))}

    def zoom_pixels(value):
        match = re.fullmatch(r'calc\(var\(--zoom\)\s*\*\s*([\d.]+)px\)', value)
        assert match, value
        return float(match.group(1))

    center = declarations('.grid-info')
    columns = [float(value.removesuffix('%')) for value in center['grid-template-columns'].split()]
    rows = [float(value.removesuffix('%')) for value in center['grid-template-rows'].split()]
    assert columns == [15, 3, 64, 3, 15] and sum(columns) == 100
    assert sum(rows) <= 100
    assert zoom_pixels(center['width']) == zoom_pixels(center['height']) == 200
    assert center['-webkit-text-size-adjust'] == '100%'
    for selector in ('.grid-discard-p0', '.grid-discard-p2'):
        rule = declarations(selector)
        assert zoom_pixels(rule['width']) == 205
        assert zoom_pixels(rule['height']) == 135
    for selector in ('.grid-discard-p1', '.grid-discard-p3'):
        rule = declarations(selector)
        assert zoom_pixels(rule['width']) == 135
        assert zoom_pixels(rule['height']) in (204, 205)
        assert zoom_pixels(declarations(selector + ' .river-row')['height']) == 204
    assert zoom_pixels(declarations('.grid-discard-p2 .river-row')['width']) == 204
    assert '--tile-width: calc(var(--zoom) * 34px)' in css
    assert '--tile-height: calc(var(--zoom) * 44px)' in css
    assert zoom_pixels(declarations(
        '.grid-main .tileImg,\n.grid-main .tileImg[src*="back"]')['padding']) == 2
    assert zoom_pixels(declarations('.info-doras')['height']) == 34
    assert declarations('.info-round-title')['font-size'] == 'max(9px, calc(var(--zoom) * 13.5px))'
    assert declarations('.info-tag-honba')['font-size'] == 'max(8px, calc(var(--zoom) * 9px))'
    assert declarations('.info-tag-kyotaku')['font-size'] == 'max(8px, calc(var(--zoom) * 9px))'
    assert '.pov-p1 > div { width:' not in css
    assert '.pov-p3 > div { width:' not in css

    # Check the CSS geometry at the requested iPhone width, a narrower phone,
    # and desktop. Tile image + padding fit its cell; six tiles / three rows
    # fit each river, and all five dora fit the center track inside its border.
    for viewport in (320, 360, 440, 768, 1440):
        padding = 8 if viewport <= 768 else 16 if viewport <= 960 else 24
        zoom = min(1, (viewport - padding) / 655)
        tile_width, tile_height = 34 * zoom, 44 * zoom
        assert (30 + 2 * 2) * zoom == pytest.approx(tile_width)
        assert (40 + 2 * 2) * zoom == pytest.approx(tile_height)
        assert 6 * tile_width <= 205 * zoom
        assert 3 * tile_height <= 135 * zoom
        inside_center = 200 * zoom - 2  # 1px borders on both sides
        assert 5 * 30 * 0.72 * zoom <= inside_center * columns[2] / 100
        assert 200 * zoom <= 205 * zoom
        round_title = max(9, 13.5 * zoom)
        tag_font = max(8, 9 * zoom)
        # Round button holds its title and one compact two-badge line.
        round_height = inside_center * 0.24
        assert round_title * 1.1 + tag_font + 2 + 4 * zoom <= round_height
        score_font = max(9, 12 * zoom)
        assert score_font <= inside_center * 0.15
        if viewport in (320, 440):
            assert score_font >= 9 and tag_font >= 8


def test_discard_probability_bars_track_scaled_tile_centers():
    code = source_between('function renderHeroHand(', 'function renderOpponentHand(')
    script = '''const assert=require('assert');
let zoom=1, viewBox='';
const tile={getBoundingClientRect(){return {left:100+35*zoom,width:34*zoom}}};
const closed={innerHTML:'',querySelectorAll(){return [tile]}};
const svg={innerHTML:'',getBoundingClientRect(){return {left:100,width:300*zoom,height:50*zoom}},
           setAttribute(name,value){if(name==='viewBox')viewBox=value}};
const nodes={handBottomClosed:closed,discardBarsSvg:svg};
const $=id=>nodes[id], escapeHtml=s=>String(s), tileCn=s=>s;
''' + code + '''
const snap={hands:[['1m']],drawn:[null],melds:[[]]};
for(const z of [1,432/655,312/655]) {
  zoom=z;
  renderHeroHand(0,snap,{actual:{type:'dahai',pai:'1m'},tile_bars:{'1m':{prob:80}}});
  assert.strictEqual(viewBox,`0 0 ${300*z} ${50*z}`);
  const [,x,width]=svg.innerHTML.match(/<rect x="([^"]+)" y="[^"]+" width="([^"]+)"/);
  assert(Math.abs(Number(x)+Number(width)/2-(35+34/2)*z)<1e-8);
  assert(Math.abs(Number(width)-9*z)<1e-8);
}
'''
    run_js(script)


def test_resize_remeasures_bars_without_reopening_or_changing_step():
    hand = source_between('function renderHeroHand(', 'function renderOpponentHand(')
    handler = source_between('function updateResponsiveZoom()', '// ============================================================================\n// Initialization')
    script = '''const assert=require('assert');
let zoom=1, viewBox='', queued=[], renders=0;
const width=()=>300*zoom+20, tileX=()=>50*zoom+10;
const tile={getBoundingClientRect(){return {left:100+tileX(),width:34*zoom}}};
const closed={innerHTML:'',querySelectorAll(){return [tile]}};
const svg={innerHTML:'',getBoundingClientRect(){return {left:100,width:width(),height:50*zoom}},
           setAttribute(name,value){if(name==='viewBox')viewBox=value}};
const nodes={handBottomClosed:closed,discardBarsSvg:svg};
const $=id=>nodes[id], escapeHtml=s=>String(s), tileCn=s=>s;
const root={clientWidth:655,style:{setProperty:(name,value)=>{zoom=Number(value)}}};
const document={documentElement:root,body:{clientWidth:655}};
let resizeHandler;
const window={innerWidth:655,addEventListener(type,callback){if(type==='resize')resizeHandler=callback}};
const requestAnimationFrame=cb=>{queued.push(cb);return queued.length};
const event={type:'tsumo',actor:0};
const snap={event,decision_idx:0,hands:[['1m']],drawn:[null],melds:[[]]};
const decision={actual:{type:'dahai',pai:'1m'},tile_bars:{'1m':{prob:80}}};
const kyoku={steps:[snap],decisions:[decision]};
const state={reviewData:{kyokus:[kyoku]},kyokuIdx:0,stepIdx:0,targetActor:0};
''' + hand + '''
const renderBoard=(k,s,d)=>{renders++;renderHeroHand(0,s,d)};
''' + handler + '''
function centered() {
  const [,x,barWidth]=svg.innerHTML.match(/<rect x="([^"]+)" y="[^"]+" width="([^"]+)"/);
  assert(Math.abs(Number(x)+Number(barWidth)/2-(tileX()+17*zoom))<1e-8);
  assert.strictEqual(viewBox,`0 0 ${width()} ${50*zoom}`);
}
renderBoard(kyoku,snap,decision); centered();
function resize(width,rootAfterLayout,expected) {
  window.innerWidth=width;
  const oldZoom=zoom;
  resizeHandler(); resizeHandler();
  assert.strictEqual(queued.length,1); // coalesce until the layout frame
  assert.strictEqual(zoom,oldZoom); // don't read the stale root on the resize event
  root.clientWidth=rootAfterLayout;
  document.body.clientWidth=100; // body width never drives zoom
  queued.shift()(); centered();
  assert(Math.abs(zoom-expected)<1e-12);
}
// Overlay scrollbar: 320 → 956 → 440, with root width stale until RAF.
resize(320,320,312/655);
resize(956,956,1);
resize(440,440,432/655);
// Repeat with classic 17px scrollbars; board fits the root content viewport.
resize(320,303,295/655);
resize(956,939,1);
resize(440,423,415/655);
assert(zoom*655<=423-8+1e-9);
resize(1440,1423,1);
resize(360,343,335/655);
assert.strictEqual(renders,9);
assert.strictEqual(state.stepIdx,0); assert.strictEqual(state.kyokuIdx,0);
state.reviewData=null; resizeHandler(); queued.shift()();
assert.strictEqual(renders,9);
'''
    run_js(script)


def test_modal_escape_and_backdrop_preserve_native_dialog_lifecycle():
    modal_helpers = source_between('function openModal(', 'function getRoundDeltas(')
    names = source_between('const MAJSOUL_YAKU_NAMES', 'function getKillerActionSpan(')
    render = source_between('function renderThisRoundModalContent(', 'function renderDecisionCard(')
    bindings = source_between('function bindEvents()', 'function updateResponsiveZoom()')
    script = '''const assert=require('assert');
function classes(initial=[]) {
  const values=new Set(initial);
  return {add:x=>values.add(x),remove:x=>values.delete(x),contains:x=>values.has(x)};
}
function element(id,tagName,initial=[]) {
  return {id,tagName,classList:classes(initial),listeners:{},dataset:{},open:false,
    addEventListener(type,fn){this.listeners[type]=fn},querySelector(){return null},
    showModal(){assert(!this.classList.contains('hidden'));this.open=true},
    close(){this.open=false},setAttribute(name){if(name==='open')this.open=true},
    removeAttribute(name){if(name==='open')this.open=false}};
}
const dialog=element('infoThisRoundModal','DIALOG');
dialog.getBoundingClientRect=()=>({left:100,right:300,top:200,bottom:400});
const custom=element('roundModal','DIV',['hidden']);
const backdrop=element('roundModalBackdrop','DIV',['hidden']);
const content={innerHTML:''};
const tier2=element('tier2Screen','SECTION');
const nodes={infoThisRoundModal:dialog,roundModal:custom,roundModalBackdrop:backdrop,infoThisRoundContent:content,tier2Screen:tier2};
const $=id=>nodes[id], state={targetActor:0,reviewData:{}}, escapeHtml=s=>String(s);
const listeners={}, window={addEventListener:(type,fn)=>listeners[type]=fn};
const document={activeElement:null,addEventListener(){},
  querySelectorAll(selector){return selector==='.modal'?[dialog,custom]:selector==='.modal-backdrop'?[backdrop]:[]},
  querySelector(){return null}};
const initLocalFileDropzone=()=>{}, jumpPrevKyoku=()=>{}, jumpNextKyoku=()=>{},
  jumpPrevBlunder=()=>{}, jumpNextBlunder=()=>{}, jumpPrevDecision=()=>{}, jumpNextDecision=()=>{};
let steps=[]; const stepDelta=d=>steps.push(d);
''' + names + modal_helpers + render + bindings + '''
bindEvents();
const escape=()=>listeners.keydown({key:'Escape',preventDefault(){}});
const show=()=>renderThisRoundModalContent({event:{type:'hora',actor:0,target:0}},{});
escape(); assert(!dialog.classList.contains('hidden'));
show(); assert(dialog.open);
escape(); assert(!dialog.open); assert(!dialog.classList.contains('hidden'));
show(); assert(dialog.open); dialog.close();
openModal('roundModal'); backdrop.listeners.click();
assert(custom.classList.contains('hidden')); assert(backdrop.classList.contains('hidden'));
assert(!dialog.classList.contains('hidden'));
show(); assert(dialog.open);
backdrop.listeners.click(); assert(!dialog.open);
dialog.classList.add('hidden'); show();
assert(dialog.open && !dialog.classList.contains('hidden'));
// Blank padding and child clicks do not dismiss, but backdrop clicks do.
const click=(target,x,y,detail=1)=>dialog.listeners.click({target,clientX:x,clientY:y,detail});
click(dialog,105,205); assert(dialog.open); // inside padding
click({},50,100); assert(dialog.open); // child target, even if coordinates are outside
click(dialog,0,0,0); assert(dialog.open); // keyboard-generated click
click(dialog,50,100); assert(!dialog.open); // pointer backdrop click
show(); click(dialog,320,250); assert(!dialog.open); // touch-generated click
show();
// Wheel events from inside the settlement sheet stay native and never step.
let prevented=false, scrolled=0;
const wheelTarget={closest:selector=>selector.includes('#infoThisRoundModal') ? dialog : null};
const wheel={target:wheelTarget,deltaY:50,preventDefault(){prevented=true}};
listeners.wheel(wheel); if(!prevented) scrolled+=wheel.deltaY;
assert.strictEqual(scrolled,50); assert(dialog.open); assert.deepStrictEqual(steps,[]);
// Ordinary wheel remains native, while Alt+wheel over the board keeps the opening guard.
const background={target:{closest:selector=>selector==='#gridMain'?{}:null},deltaY:20,altKey:false,preventDefault(){prevented=true}};
prevented=false; listeners.wheel(background);
assert(!prevented); assert(dialog.open); assert.deepStrictEqual(steps,[]);
background.altKey=true; prevented=false; listeners.wheel(background);
assert(prevented); assert(dialog.open); assert.deepStrictEqual(steps,[]);
dialog.dataset.openedAt=String(Date.now()-1000);
prevented=false; listeners.wheel(background);
assert(prevented); assert(!dialog.open); assert.deepStrictEqual(steps,[1]);
background.deltaY=-20; prevented=false; listeners.wheel(background);
assert(prevented); assert.deepStrictEqual(steps,[1,-1]);
'''
    run_js(script)
