"""Keyboard and wheel regressions for the visible replay screen."""
import shutil
import subprocess

import pytest

from frontend_assets import javascript_source


def test_replay_keyboard_and_wheel_preserve_native_controls():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    modal_helpers = source[source.index("function openModal("):source.index("function getRoundDeltas(")]
    score_opener = source[source.index("function openRoundScoresModal("):source.index("// ============================================================================\n// Local Paipu Cache")]
    bindings = source[source.index("function bindEvents()"):source.index("function updateResponsiveZoom()")]
    harness = r'''
const assert = require('assert');
const classes = (initial=[]) => {
  const values = new Set(initial);
  return {add:x=>values.add(x), remove:x=>values.delete(x), contains:x=>values.has(x)};
};
const nodes = {};
const document = {activeElement:null, addEventListener(){},
  querySelectorAll(selector) {
    if (selector === '.modal') return [nodes.infoThisRoundModal,nodes.roundModal,nodes.shortcutsModal,nodes.engineModal];
    if (selector === '.modal-backdrop') return [nodes.roundModalBackdrop,nodes.shortcutsModalBackdrop,nodes.engineModalBackdrop];
    return [];
  },
  querySelector(selector) {return selector === 'dialog[open]' && nodes.infoThisRoundModal.open ? nodes.infoThisRoundModal : null;}
};
function element(id,tagName,initial=[]) {
  const el={id,tagName,classList:classes(initial),listeners:{},dataset:{},open:false,
    addEventListener(type,fn){this.listeners[type]=fn},querySelector(){return null},
    showModal(){this.open=true},close(){this.open=false},setAttribute(k){if(k==='open')this.open=true},
    removeAttribute(k){if(k==='open')this.open=false},blur(){},closest(){return null}};
  nodes[id]=el;return el;
}
const tier=element('tier2Screen','SECTION',['hidden']);
const dialog=element('infoThisRoundModal','DIALOG');
dialog.getBoundingClientRect=()=>({left:100,right:300,top:100,bottom:300});
const score=element('roundModal','DIV',['hidden']);
const scoreClose=element('roundModalClose','BUTTON');
score.querySelector=()=>({focus(){document.activeElement=scoreClose}});
element('roundModalBackdrop','DIV',['hidden']);
const shortcuts=element('shortcutsModal','DIV',['hidden']);
const shortcutsClose=element('shortcutsModalClose','BUTTON');
shortcuts.querySelector=()=>({focus(){document.activeElement=shortcutsClose}});
element('shortcutsModalBackdrop','DIV',['hidden']);
element('engineModal','DIV',['hidden']);
element('engineModalBackdrop','DIV',['hidden']);
const slider=element('stepSlider','INPUT'); slider.type='range';
const listeners={}, window={addEventListener:(type,fn)=>listeners[type]=fn};
const $=id=>nodes[id], state={reviewData:{kyokus:[{steps:[{},{}]}]},targetActor:0,kyokuIdx:0,stepIdx:0};
const counts={steps:[],fast:[],play:0,nextDecision:0,render:0};
const initLocalFileDropzone=()=>{},jumpPrevKyoku=()=>{},jumpNextKyoku=()=>{},
  jumpPrevBlunder=()=>{},jumpNextBlunder=()=>{},jumpPrevDecision=()=>{},
  jumpNextDecision=()=>{counts.nextDecision++},toggleHandsVisibility=()=>{},toggleSound=()=>{},
  stepDelta=d=>counts.steps.push(d),stepDeltaFast=d=>counts.fast.push(d),
  jumpPrevBlunderFast=()=>{},jumpNextBlunderFast=()=>{},cancelScheduledStep=()=>{},
  setStep=()=>{},scheduleStep=()=>{},setPlaySpeed=()=>{},togglePlay=()=>{counts.play++},
  renderKyokuTable=()=>{counts.render++};
''' + modal_helpers + score_opener + bindings + r'''
bindEvents();
function key(value,active=null,extra={}) {
  document.activeElement=active;
  const e={key:value,code:value===' '?'Space':'',defaultPrevented:false,
    preventDefault(){this.defaultPrevented=true},...extra};
  listeners.keydown(e);return e;
}
function wheel(target,extra={}) {
  const e={target,deltaY:100,altKey:false,ctrlKey:false,metaKey:false,defaultPrevented:false,
    preventDefault(){this.defaultPrevented=true},...extra};
  listeners.wheel(e);return e;
}
const boardTarget={closest:s=>s==='#gridMain'?{}:null};
const pageTarget={closest:()=>null};
const dialogTarget={closest:s=>s.includes('#infoThisRoundModal')?dialog:null};
const formTarget={closest:s=>s==='#gridMain'?{}:s.includes('input')?{}:null};

// A loaded review must not hijack keyboard or mouse while the home page is visible.
assert(!wheel(boardTarget).defaultPrevented);
assert(!key('ArrowRight').defaultPrevented);
assert(!key('s').defaultPrevented);
assert.deepStrictEqual(counts.steps,[]);assert.deepStrictEqual(counts.fast,[]);
assert(score.classList.contains('hidden'));

// Ordinary vertical wheel events step only over the board; browser gestures stay native.
tier.classList.remove('hidden');
assert(!wheel(pageTarget).defaultPrevented);
assert(!wheel(formTarget).defaultPrevented);
for (const modifier of ['altKey','ctrlKey','metaKey','shiftKey']) {
  assert(!wheel(boardTarget,{[modifier]:true}).defaultPrevented);
}
assert(!wheel(boardTarget,{deltaY:0}).defaultPrevented);
assert(wheel(boardTarget).defaultPrevented);
assert(wheel(boardTarget,{deltaY:-10}).defaultPrevented);
assert.deepStrictEqual(counts.steps,[1,-1]);
const alreadyHandled=wheel(boardTarget,{defaultPrevented:true});
assert(alreadyHandled.defaultPrevented);assert.deepStrictEqual(counts.steps,[1,-1]);

// Custom modals retain their own scrolling, even over a board event target.
openModal('roundModal');
assert(!wheel(boardTarget).defaultPrevented);
assert.deepStrictEqual(counts.steps,[1,-1]);
closeModal('roundModal');

// The native dialog retains its inside scroll and opening guard.
dialog.showModal();dialog.dataset.openedAt=String(Date.now());
assert(!wheel(dialogTarget).defaultPrevented);assert(dialog.open);
assert(wheel(boardTarget).defaultPrevented);assert(dialog.open);
dialog.dataset.openedAt=String(Date.now()-1000);
assert(wheel(boardTarget).defaultPrevented);assert(!dialog.open);
assert.deepStrictEqual(counts.steps,[1,-1,1]);

// Focused controls keep their native keys; focus on the replay background retains shortcuts.
const button=element('plainButton','BUTTON'), select=element('plainSelect','SELECT');
assert(!key(' ',button).defaultPrevented);assert.strictEqual(counts.play,0);
assert(!key('ArrowDown',select).defaultPrevented);assert.strictEqual(counts.nextDecision,0);
assert(key('ArrowRight').defaultPrevented);assert.deepStrictEqual(counts.fast,[1]);
assert(key(' ').defaultPrevented);assert.strictEqual(counts.play,1);
const edit=element('editable','DIV');edit.isContentEditable=true;
assert(!key('ArrowRight',edit).defaultPrevented);assert.deepStrictEqual(counts.fast,[1]);

// Slider's own handler remains authoritative and prevents a duplicate global step.
const slideEvent={key:'ArrowRight',defaultPrevented:false,preventDefault(){this.defaultPrevented=true}};
slider.listeners.keydown(slideEvent);document.activeElement=slider;listeners.keydown(slideEvent);
assert.deepStrictEqual(counts.fast,[1,1]);

// S opens a rendered score table, closes it even after focus moves to its close button,
// and does not open an empty score sheet without review data.
assert(key('s').defaultPrevented);assert.strictEqual(counts.render,1);assert(!score.classList.contains('hidden'));
assert.strictEqual(document.activeElement,scoreClose);
assert(!key('s',scoreClose,{ctrlKey:true}).defaultPrevented);assert(!score.classList.contains('hidden'));
assert(!key('s',select).defaultPrevented);assert(!score.classList.contains('hidden'));
assert(key('s',scoreClose).defaultPrevented);assert(score.classList.contains('hidden'));
// Help toggles even after focusing its close button, but not from an editable select
// or a browser modifier shortcut.
assert(key('?').defaultPrevented);assert(!shortcuts.classList.contains('hidden'));
assert.strictEqual(document.activeElement,shortcutsClose);
assert(!key('?',select).defaultPrevented);assert(!shortcuts.classList.contains('hidden'));
assert(!key('?',shortcutsClose,{ctrlKey:true}).defaultPrevented);assert(!shortcuts.classList.contains('hidden'));
assert(key('?',shortcutsClose).defaultPrevented);assert(shortcuts.classList.contains('hidden'));
assert(key('/',null,{shiftKey:true,code:'Slash'}).defaultPrevented);assert(!shortcuts.classList.contains('hidden'));
assert(key('?',shortcutsClose).defaultPrevented);assert(shortcuts.classList.contains('hidden'));
// Space on a focused native dialog button must not close or toggle playback.
dialog.showModal();const closeButton=element('infoThisRoundClose','BUTTON');
const oldPlay=counts.play;assert(!key(' ',closeButton).defaultPrevented);assert(dialog.open);assert.strictEqual(counts.play,oldPlay);
key('Escape',closeButton);assert(!dialog.open);
state.reviewData=null;assert(!key('s').defaultPrevented);assert(score.classList.contains('hidden'));
'''
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, encoding="utf8")
    assert result.returncode == 0, result.stderr
