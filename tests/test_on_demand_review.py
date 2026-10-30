"""Exercise the actual frontend request coordinator with controlled async APIs."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_on_demand_perspectives_and_races():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = Path("web/app.js").read_text(encoding="utf-8")
    code = source[source.index("const inFlightRequests = new Map();"):source.index("function handWind(")]
    script = r'''
const assert = require('assert');
const state = {perspectiveCache:{}, selectedModel:'akagi', targetActor:0, kyokuIdx:1, stepIdx:3};
const persisted = new Map();
const idbReviewCache = {
  findByGame: async (id, model, seat) => persisted.get(`${id}_${seat}_${model}`),
  set: async (key, value) => persisted.set(key, value),
};
let calls = [], pending = [], statusGate = null, errors = [], loads = [];
const requireModelStatus = async () => { if (statusGate) await statusGate; };
const isTrustedReview = r => !!r && r.trusted === true;
const requireTrustedReview = r => { if (!isTrustedReview(r)) throw Error('untrusted'); };
const fetchReviewStream = (body, progress) => {
  calls.push(body);
  return new Promise((resolve, reject) => pending.push({body, resolve, reject, progress}));
};
const apiFetch = async () => { throw Error('fallback failed'); };
const result = (seat, trusted=true) => ({target_actor:seat, game_id:'server-id', trusted,
  kyokus:[{steps:[0,1,2,3,4]}, {steps:[0,1,2,3,4]}]});
const wait = () => new Promise(resolve => setImmediate(resolve));
const $ = () => null;
const setLoading = (...a) => loads.push(a), updateAnalysisProgress=()=>{};
const showAnalysisError=(message, retry)=>errors.push({message,retry});
const updateEngineStatusDisplay=()=>{}, renderSummaryDashboard=()=>{}, showTier=()=>{}, showToast=()=>{};
const selectKyoku=(n)=>{state.kyokuIdx=n;state.stepIdx=0}, setStep=n=>state.stepIdx=n;
const cancelScheduledStep=()=>{}, togglePlay=()=>{}, getPlayerDisplayName=s=>`seat${s}`;
const parsePaipuUuid=s=>s;
const window={location:{href:'https://example.test/'},history:{pushState(){}}};
''' + code + r'''
(async () => {
  const a = startAnalysisFromInput('game-one', 'akagi', 0);
  const aDuplicate = startAnalysisFromInput('game-one', 'akagi', 0);
  await wait(); assert.strictEqual(calls.length, 1);
  pending.shift().resolve(result(0)); await Promise.all([a,aDuplicate]);
  assert.strictEqual(state.targetActor, 0);
  await new Promise(r=>setTimeout(r,40)); assert.strictEqual(calls.length,1);
  state.kyokuIdx=1; state.stepIdx=3;
  const b = switchSeatPerspective(1); const b2 = switchSeatPerspective(1);
  await wait(); assert.strictEqual(calls.length,2);
  const c = switchSeatPerspective(2); const d = switchSeatPerspective(3);
  await wait(); assert.strictEqual(calls.length,4);
  const work = pending.splice(0);
  work.find(x=>x.body.target_actor===3).resolve(result(3)); await d;
  work.find(x=>x.body.target_actor===1).resolve(result(1));
  work.find(x=>x.body.target_actor===2).resolve(result(2));
  await Promise.all([b,b2,c]); assert.strictEqual(state.targetActor,3);
  assert.strictEqual(state.kyokuIdx,1); assert.strictEqual(state.stepIdx,3);
  await switchSeatPerspective(0); await switchSeatPerspective(1);
  assert.strictEqual(state.targetActor,1); assert.strictEqual(calls.length,4);
  state.perspectiveCache={}; await switchSeatPerspective(0);
  assert.strictEqual(calls.length,4); assert.strictEqual(state.targetActor,0);
  const old = switchSeatPerspective(2); await old; // persistent cache
  const fail = switchSeatPerspective(3); await fail; // persistent cache
  state.perspectiveCache={}; persisted.delete('game-one_1_akagi');
  const failed=switchSeatPerspective(1); await wait();
  pending.shift().reject(Error('offline')); await failed;
  assert(errors.at(-1).message.includes('fallback failed'));
  errors.at(-1).retry(); await wait(); pending.shift().resolve(result(1)); await wait();
  assert.strictEqual(state.targetActor,1);
  const json='{"type":"start_game"}\n{"type":"end_game"}';
  const upload=startAnalysisFromInput(json,'akagi',0); await wait();
  assert.strictEqual(pending[0].body.content,json); pending.shift().resolve(result(0)); await upload;
  const uploadB=switchSeatPerspective(1); await wait();
  assert.strictEqual(pending[0].body.content,json); assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await uploadB;
  const stale=switchSeatPerspective(2); await wait();
  const next=startAnalysisFromInput('game-two','akagi',0); await wait();
  pending.find(x=>x.body.paipu==='game-two').resolve(result(0)); await next;
  pending.find(x=>x.body.content===json).resolve(result(2)); await stale;
  assert.strictEqual(state.targetActor,0);
  const bad=switchSeatPerspective(1); await wait(); pending.at(-1).resolve(result(2)); await bad;
  assert(errors.at(-1).message.includes('座位不匹配')); assert.strictEqual(state.targetActor,0);
  const untrusted=switchSeatPerspective(2); await wait(); pending.at(-1).resolve(result(2,false)); await untrusted;
  assert(errors.at(-1).message.includes('untrusted')); assert.strictEqual(state.targetActor,0);
  const staleModel=switchSeatPerspective(3); await wait(); state.selectedModel='different';
  pending.at(-1).resolve(result(3)); await staleModel; assert.strictEqual(state.targetActor,0);
  console.log('on-demand checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_cached_read_overlaps_status_and_write_does_not_block():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = Path("web/app.js").read_text(encoding="utf-8")
    code = source[source.index("const inFlightRequests = new Map();"):source.index("function setPerspectiveLoading(")]
    script = r'''
const assert = require('assert');
const state = {perspectiveCache:{}};
let releaseStatus, readStarted = false, writes = 0, requests = 0;
const gate = new Promise(resolve => releaseStatus = resolve);
const requireModelStatus = () => gate;
const isTrustedReview = r => !!r && r.trusted;
const requireTrustedReview = r => { if (!isTrustedReview(r)) throw Error('untrusted'); };
const cached = {target_actor:0, trusted:true};
const idbReviewCache = {
  findByGame: async id => { readStarted = true; return id === 'cached' ? cached : null; },
  set: () => { writes++; return new Promise(() => {}); },
};
const fetchReviewStream = async () => { requests++; return cached; };
const apiFetch = async () => { throw Error('unexpected fallback'); };
''' + code + r'''
(async () => {
  const first = getReview({kind:'paipu',identity:'cached',input:'cached'}, 'akagi', 0);
  assert(readStarted); assert.strictEqual(requests, 0);
  releaseStatus(); assert.strictEqual(await first, cached); assert.strictEqual(requests, 0);
  const second = await Promise.race([
    getReview({kind:'paipu',identity:'new',input:'new'}, 'akagi', 0),
    new Promise((_, reject) => setTimeout(() => reject(Error('write blocked result')), 100))
  ]);
  assert.strictEqual(second, cached); assert.strictEqual(writes, 1);
})().catch(e => { console.error(e); process.exit(1); });
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_no_automatic_review_preloading():
    source = Path("web/app.js").read_text(encoding="utf-8")
    assert "preloadOtherPerspectives" not in source
    assert "requestIdleCallback" not in source
