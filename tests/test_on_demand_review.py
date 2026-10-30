"""Exercise the actual frontend request coordinator with controlled async APIs."""
import shutil
import subprocess

import pytest

from frontend_assets import javascript_source


def test_on_demand_perspectives_and_races():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    parse_start = source.index("function parsePaipuUuid(")
    parse_code = source[parse_start:source.index("\n// ===", parse_start)]
    code = source[source.index("let analysisViewVersion = 0;"):source.index("function handWind(")]
    script = r'''
const assert = require('assert');
const state = {selectedModel:'akagi', targetActor:0, kyokuIdx:1, stepIdx:3};
let calls = [], pending = [], errors = [], loads = [], progressEvents = [];
const requireModelStatus = async () => {};
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
const setLoading = (...a) => loads.push(a), updateAnalysisProgress=(...args)=>progressEvents.push(args);
const showAnalysisError=(message, retry)=>errors.push({message,retry});
const updateEngineStatusDisplay=()=>{}, renderSummaryDashboard=()=>{}, showTier=()=>{}, showToast=()=>{};
const selectKyoku=(n)=>{state.kyokuIdx=n;state.stepIdx=0}, setStep=n=>state.stepIdx=n;
const cancelScheduledStep=()=>{}, togglePlay=()=>{}, getPlayerDisplayName=s=>`seat${s}`;
const window={location:{href:'https://example.test/'},history:{pushState(){}}};
''' + parse_code + code + r'''
const watchdog = setTimeout(()=>{console.error('async test timed out'); process.exit(1)}, 5000);
(async () => {
  // Even overlapping identical user requests make separate backend calls.
  const a = startAnalysisFromInput('game-one', 'akagi', 0);
  const aDuplicate = startAnalysisFromInput('game-one', 'akagi', 0);
  await wait(); assert.strictEqual(calls.length, 2);
  assert.deepStrictEqual(calls[0], {paipu:'game-one',engine:'akagi',target_actor:0});
  assert.deepStrictEqual(calls[1], calls[0]);
  const first = result(0), second = result(0);
  const [oldRequest, newRequest] = pending.splice(0);
  newRequest.progress(60,'new'); oldRequest.progress(20,'old');
  assert.deepStrictEqual(progressEvents,[[60,'new']]);
  newRequest.resolve(second); await aDuplicate;
  oldRequest.resolve(first); await a;
  assert.strictEqual(state.reviewData, second);
  assert.strictEqual(state.targetActor, 0);
  await wait(); assert.strictEqual(calls.length,2); // no automatic preloading

  state.kyokuIdx=1; state.stepIdx=3;
  const b = switchSeatPerspective(1); const b2 = switchSeatPerspective(1);
  await wait(); assert.strictEqual(calls.length,4);
  const c = switchSeatPerspective(2); const d = switchSeatPerspective(3);
  await wait(); assert.strictEqual(calls.length,6);
  const work = pending.splice(0);
  work.find(x=>x.body.target_actor===3).resolve(result(3)); await d;
  for (const request of work.filter(x=>x.body.target_actor===1)) request.resolve(result(1));
  work.find(x=>x.body.target_actor===2).resolve(result(2));
  await Promise.all([b,b2,c]); assert.strictEqual(state.targetActor,3);
  assert.strictEqual(state.kyokuIdx,1); assert.strictEqual(state.stepIdx,3);

  // Previously visited seats and repeated analyses are fetched again.
  const back = switchSeatPerspective(0); await wait();
  assert.strictEqual(calls.length,7); assert.strictEqual(pending[0].body.target_actor,0);
  pending.shift().resolve(result(0)); await back;
  const revisit = switchSeatPerspective(1); await wait();
  assert.strictEqual(calls.length,8); assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await revisit;
  const repeat = startAnalysisFromInput('game-one','akagi',0); await wait();
  assert.strictEqual(calls.length,9); pending.shift().resolve(result(0)); await repeat;

  // Returning to the displayed seat invalidates an older in-flight switch.
  const canceled = switchSeatPerspective(2); await wait();
  await switchSeatPerspective(0);
  assert.strictEqual(calls.length,10);
  pending.shift().resolve(result(2)); await canceled;
  assert.strictEqual(state.targetActor,0);
  assert.strictEqual(loads.at(-1)[0],false);

  // A failed attempt keeps the displayed result and retry makes a new request.
  const displayed = state.reviewData;
  const failed=switchSeatPerspective(1); await wait();
  assert.strictEqual(state.reviewData, displayed);
  pending.shift().reject(Error('offline')); await failed;
  assert(errors.at(-1).message.includes('fallback failed'));
  assert.strictEqual(state.reviewData, displayed);
  const beforeRetry = calls.length;
  const retry=errors.at(-1).retry(); await wait(); assert.strictEqual(calls.length,beforeRetry+1);
  pending.shift().resolve(result(1)); await retry;
  assert.strictEqual(state.targetActor,1);

  const json='  \r\n{"type":"start_game"}\n{"type":"end_game"}\n  ';
  const upload=startAnalysisFromInput(json,'akagi',0); await wait();
  assert.strictEqual(pending[0].body.content,json); pending.shift().resolve(result(0)); await upload;
  const uploadB=switchSeatPerspective(1); await wait();
  assert.strictEqual(pending[0].body.content,json); assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await uploadB;
  const uploadBack=switchSeatPerspective(0); await wait();
  assert.strictEqual(pending[0].body.content,json); assert.strictEqual(pending[0].body.target_actor,0);
  pending.shift().resolve(result(0)); await uploadBack;
  const uploadFailed=switchSeatPerspective(2); await wait();
  assert.strictEqual(pending[0].body.content,json);
  pending.shift().reject(Error('offline')); await uploadFailed;
  const uploadRetry=errors.at(-1).retry(); await wait();
  assert.strictEqual(pending[0].body.content,json);
  assert.strictEqual(pending[0].body.target_actor,2);
  pending.shift().resolve(result(2)); await uploadRetry;

  const stale=switchSeatPerspective(3); await wait();
  const next=startAnalysisFromInput('game-two','akagi',0); await wait();
  const staleWork=pending.splice(0);
  staleWork.find(x=>x.body.paipu==='game-two').resolve(result(0)); await next;
  staleWork.find(x=>x.body.content===json).resolve(result(3)); await stale;
  assert.strictEqual(state.targetActor,0);
  const bad=switchSeatPerspective(1); await wait(); pending.shift().resolve(result(2)); await bad;
  assert(errors.at(-1).message.includes('座位不匹配')); assert.strictEqual(state.targetActor,0);
  const untrusted=switchSeatPerspective(2); await wait(); pending.shift().resolve(result(2,false)); await untrusted;
  assert(errors.at(-1).message.includes('untrusted')); assert.strictEqual(state.targetActor,0);
  const staleModel=switchSeatPerspective(3); await wait(); state.selectedModel='different';
  pending.shift().resolve(result(3)); await staleModel; assert.strictEqual(state.targetActor,0);

  // URL-based paipu analysis and subsequent switches send the parsed UUID.
  const url='https://game.maj-soul.com/?paipu=240101-abc123&other=value';
  const byUrl=startAnalysisFromInput(url,'akagi',0); await wait();
  assert.strictEqual(pending[0].body.paipu,'240101-abc123');
  pending.shift().resolve(result(0)); await byUrl;
  const urlSeat=switchSeatPerspective(1); await wait();
  assert.strictEqual(pending[0].body.paipu,'240101-abc123');
  assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await urlSeat;
})().then(()=>clearTimeout(watchdog)).catch(e=>{console.error(e);process.exit(1)});
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_each_review_calls_backend_and_validates_status():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    code = source[source.index("let analysisViewVersion = 0;"):source.index("function setPerspectiveLoading(")]
    script = r'''
const assert = require('assert');
let releaseStatus, statusCalls = 0, requests = [], fallbackCalls = [];
let failStream = false;
const gate = new Promise(resolve => releaseStatus = resolve);
const requireModelStatus = async () => { statusCalls++; await gate; };
const isTrustedReview = r => !!r && r.trusted;
const requireTrustedReview = r => { if (!isTrustedReview(r)) throw Error('untrusted'); };
const response = {target_actor:0, trusted:true};
let fetchReviewStream = async (body, onProgress) => {
  requests.push(body);
  onProgress?.(42, 'working');
  if (failStream) throw Error('stream unavailable');
  return response;
};
const apiFetch = async (endpoint, options) => {
  fallbackCalls.push({endpoint, body:JSON.parse(options.body)});
  return response;
};
''' + code + r'''
const watchdog = setTimeout(()=>{console.error('async test timed out'); process.exit(1)}, 5000);
(async () => {
  const source = {kind:'paipu',identity:'game',input:'https://example.test/?paipu=game'};
  const progress=[];
  const first = getReview(source, 'akagi', 0, (...args)=>progress.push(args));
  const concurrent = getReview(source, 'akagi', 0, ()=>{});
  assert.strictEqual(statusCalls,2); assert.strictEqual(requests.length,0);
  releaseStatus();
  assert.strictEqual(await first, response); assert.strictEqual(await concurrent, response);
  assert.deepStrictEqual(progress,[[42,'working']]);
  assert.strictEqual(requests.length,2);
  assert.strictEqual(await getReview(source, 'akagi', 0), response);
  assert.strictEqual(requests.length,3); assert.strictEqual(statusCalls,3);

  failStream = true;
  assert.strictEqual(await getReview(source, 'akagi', 0), response);
  const json = '{"type":"start_game"}\n{"type":"end_game"}';
  assert.strictEqual(await getReview({kind:'json',identity:json,input:json}, 'akagi', 0), response);
  assert.deepStrictEqual(fallbackCalls, [
    {endpoint:'/api/analyze/paipu',body:{paipu:'game',engine:'akagi',target_actor:0}},
    {endpoint:'/api/analyze/json',body:{content:json,engine:'akagi',target_actor:0}},
  ]);
  const reported = Object.assign(new Error('保存失败'), {serverReported:true});
  failStream = false;
  const original = fetchReviewStream;
  fetchReviewStream = async () => { throw reported; };
  await assert.rejects(getReview(source, 'akagi', 0), /保存失败/);
  assert.strictEqual(fallbackCalls.length, 2);
  fetchReviewStream = original;
})().then(()=>clearTimeout(watchdog)).catch(e => { console.error(e); process.exit(1); });
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_no_automatic_review_preloading():
    source = javascript_source()
    assert "preloadOtherPerspectives" not in source
    assert "requestIdleCallback" not in source
    assert "perspectiveCache" not in source
    assert "idbReviewCache" not in source


def test_uploaded_file_is_passed_without_trimming():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    start = source.index("function readAndAnalyzeFile(")
    code = source[start:source.index("\n// ===", start)]
    script = r'''const assert = require('assert');
const state={selectedModel:'akagi'}, notices=[];
let observed;
const showToast=(...args)=>notices.push(args);
const startAnalysisFromInput=(content,model)=>{observed={content,model};};
class FileReader {
  readAsText(file,encoding) {
    assert.strictEqual(encoding,'utf-8');
    this.onload({target:{result:file.content}});
  }
}
''' + code + r'''
const content=' \r\n{"type":"start_game"}\n{"type":"end_game"}\n  ';
readAndAnalyzeFile({name:'original.json',content});
assert.deepStrictEqual(observed,{content,model:'akagi'});
assert(notices.some(args=>args[0].includes('original.json')));
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_stream_ignores_persistence_field_and_keeps_results_and_progress():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    code = source[source.index("async function fetchReviewStream("):source.index("const DEFAULT_MODELSCOPE_BACKEND")]
    script = r'''const assert = require('assert');
const getBackendUrl=()=>'', buildApiUrl=(_backend,path)=>path;
const isPagesProxyMode=()=>false, apiAuthHeaders=()=>({});
let chunks=[], toasts=[], progress=[];
const showToast=(...args)=>toasts.push(args);
const fetch=async ()=>({ok:true,body:{getReader:()=>({read:async()=>
  chunks.length ? {done:false,value:new TextEncoder().encode(chunks.shift())} : {done:true}})}});
''' + code + r'''
const watchdog = setTimeout(()=>{console.error('async test timed out'); process.exit(1)}, 5000);
(async()=>{
  for (const persisted of [true,false,undefined]) {
    for (const newline of [true,false]) {
      const complete=JSON.stringify({type:'complete',result:{ok:true, nested:{value:7}},cache_persisted:persisted});
      chunks=[
        'data: {"type":"progress","percent":37,"message":"working"}\n\n',
        'data: {"type":"queued","message":"queued"}\n\n',
        'data: '+complete.slice(0,12),
        complete.slice(12)+(newline?'\n\n':''),
      ];
      progress=[];
      const result=await fetchReviewStream({paipu:'demo'},(...args)=>progress.push(args));
      assert.deepStrictEqual(result,{ok:true,nested:{value:7}});
      assert.deepStrictEqual(progress,[[37,'working'],[5,'queued']]);
      assert.deepStrictEqual(toasts,[]);
    }
  }
  chunks=['data: {"type":"error","detail":"bad JSON analysis"}\n\n'];
  await assert.rejects(fetchReviewStream({},()=>{}), error => error.serverReported && /bad JSON analysis/.test(error.message));
})().then(()=>clearTimeout(watchdog)).catch(e=>{console.error(e);process.exit(1)});
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
