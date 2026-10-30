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
    constants = source[source.index("const REVIEW_SCHEMA ="):source.index("function isTrustedReview(")]
    code = source[source.index("let analysisViewVersion = 0;"):source.index("function handWind(")]
    script = r'''
const assert = require('assert');
const state = {selectedModel:'mortal-s42-onnx', targetActor:0, kyokuIdx:1, stepIdx:3};
let calls = [], pending = [], errors = [], loads = [], progressEvents = [], fallbackCalls = 0;
const getBackendUrl=()=>'', buildApiUrl=(backend,path)=>backend+path;
const requireModelStatus = async () => {};
const isTrustedReview = r => !!r && r.trusted === true;
const requireTrustedReview = r => { if (!isTrustedReview(r)) throw Error('untrusted'); };
const fetchReviewStream = (body, progress, url) => {
  calls.push({body, url});
  return new Promise((resolve, reject) => pending.push({body, resolve, reject, progress, url}));
};
const apiFetch = async () => { fallbackCalls++; throw Error('unexpected fallback'); };
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
''' + parse_code + constants + code + r'''
const watchdog = setTimeout(()=>{console.error('async test timed out'); process.exit(1)}, 5000);
(async () => {
  // Identical overlapping requests share one analysis, with only the latest view updating.
  const a = startAnalysisFromInput('game-one', 0);
  const aDuplicate = startAnalysisFromInput('game-one', 0);
  await wait(); assert.strictEqual(calls.length, 1);
  assert.deepStrictEqual(calls[0], {url:'/api/analyze/stream',
    body:{paipu:'game-one',engine:'auto',temperature:0.1,target_actor:0}});
  progressEvents = [];
  pending[0].progress(60,'shared');
  assert.deepStrictEqual(progressEvents,[[60,'shared']]);
  const first = result(0);
  pending.shift().resolve(first); await Promise.all([a,aDuplicate]);
  assert.strictEqual(state.reviewData, first);
  assert.strictEqual(state.targetActor, 0);
  await wait(); assert.strictEqual(calls.length,1); // no automatic preloading

  state.kyokuIdx=1; state.stepIdx=3;
  const b = switchSeatPerspective(1); const b2 = switchSeatPerspective(1);
  await wait(); assert.strictEqual(calls.length,2);
  const c = switchSeatPerspective(2); const d = switchSeatPerspective(3);
  await wait(); assert.strictEqual(calls.length,4);
  const work = pending.splice(0), latest = result(3);
  work.find(x=>x.body.target_actor===3).resolve(latest); await d;
  work.find(x=>x.body.target_actor===1).resolve(result(1));
  work.find(x=>x.body.target_actor===2).resolve(result(2));
  await Promise.all([b,b2,c]);
  assert.strictEqual(state.reviewData,latest); assert.strictEqual(state.targetActor,3);
  assert.strictEqual(state.kyokuIdx,1); assert.strictEqual(state.stepIdx,3);

  // Completed perspectives and repeated analyses reuse results within the page.
  const beforeCached = calls.length;
  await switchSeatPerspective(0);
  await switchSeatPerspective(1);
  await startAnalysisFromInput('game-one',0);
  assert.strictEqual(calls.length,beforeCached);
  assert.strictEqual(state.reviewData,first); assert.strictEqual(state.targetActor,0);

  // Returning to the displayed seat invalidates an older in-flight switch.
  const prepare=startAnalysisFromInput('cancel-game',0); await wait();
  pending.shift().resolve(result(0)); await prepare;
  const displayedBeforeCancel=state.reviewData;
  const canceled=switchSeatPerspective(2); await wait();
  const beforeCancel=calls.length;
  await switchSeatPerspective(0);
  progressEvents=[]; pending[0].progress(25,'stale');
  assert.deepStrictEqual(progressEvents,[]);
  pending.shift().resolve(result(2)); await canceled;
  assert.strictEqual(calls.length,beforeCancel);
  assert.strictEqual(state.reviewData,displayedBeforeCancel); assert.strictEqual(state.targetActor,0);
  assert.strictEqual(loads.at(-1)[0],false);

  // Failure retains the displayed result; only a manual retry starts a new stream.
  const displayed = state.reviewData;
  const failed=switchSeatPerspective(1); await wait();
  assert.strictEqual(state.reviewData, displayed);
  pending.shift().reject(Error('offline')); await failed;
  assert(errors.at(-1).message.includes('offline'));
  assert.strictEqual(state.reviewData, displayed); assert.strictEqual(fallbackCalls,0);
  const beforeRetry = calls.length;
  const retry=errors.at(-1).retry(); await wait(); assert.strictEqual(calls.length,beforeRetry+1);
  assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await retry;
  assert.strictEqual(state.targetActor,1);

  const json='  \r\n{"type":"start_game"}\n{"type":"end_game"}\n  ';
  const upload=startAnalysisFromInput(json,0); await wait();
  assert.strictEqual(pending[0].body.content,json);
  const uploadResult=result(0); pending.shift().resolve(uploadResult); await upload;
  const uploadB=switchSeatPerspective(1); await wait();
  assert.strictEqual(pending[0].body.content,json); assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await uploadB;
  const beforeUploadBack=calls.length;
  await switchSeatPerspective(0);
  assert.strictEqual(calls.length,beforeUploadBack); assert.strictEqual(state.reviewData,uploadResult);
  const uploadFailed=switchSeatPerspective(2); await wait();
  assert.strictEqual(pending[0].body.content,json);
  pending.shift().reject(Error('offline')); await uploadFailed;
  const uploadRetry=errors.at(-1).retry(); await wait();
  assert.strictEqual(pending[0].body.content,json);
  assert.strictEqual(pending[0].body.target_actor,2);
  pending.shift().resolve(result(2)); await uploadRetry;
  assert.strictEqual(fallbackCalls,0);

  const stale=switchSeatPerspective(3); await wait();
  const next=startAnalysisFromInput('game-two',0); await wait();
  const staleWork=pending.splice(0), nextResult=result(0);
  staleWork.find(x=>x.body.paipu==='game-two').resolve(nextResult); await next;
  staleWork.find(x=>x.body.content===json).resolve(result(3)); await stale;
  assert.strictEqual(state.reviewData,nextResult); assert.strictEqual(state.targetActor,0);
  const bad=switchSeatPerspective(1); await wait(); pending.shift().resolve(result(2)); await bad;
  assert(errors.at(-1).message.includes('座位不匹配')); assert.strictEqual(state.targetActor,0);
  const untrusted=switchSeatPerspective(2); await wait(); pending.shift().resolve(result(2,false)); await untrusted;
  assert(errors.at(-1).message.includes('untrusted')); assert.strictEqual(state.targetActor,0);

  // URL-based paipu analysis and subsequent switches send the parsed UUID.
  const url='https://game.maj-soul.com/?paipu=240101-abc123&other=value';
  const byUrl=startAnalysisFromInput(url,0); await wait();
  assert.strictEqual(pending[0].body.paipu,'240101-abc123');
  pending.shift().resolve(result(0)); await byUrl;
  const urlSeat=switchSeatPerspective(1); await wait();
  assert.strictEqual(pending[0].body.paipu,'240101-abc123');
  assert.strictEqual(pending[0].body.target_actor,1);
  pending.shift().resolve(result(1)); await urlSeat;
})().then(()=>clearTimeout(watchdog)).catch(e=>{console.error(e);process.exit(1)});
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf8", timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_review_requests_coalesce_cache_and_validate_status():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    constants = source[source.index("const REVIEW_SCHEMA ="):source.index("function isTrustedReview(")]
    code = source[source.index("let analysisViewVersion = 0;"):source.index("function setPerspectiveLoading(")]
    script = r'''
const assert = require('assert');
let releaseStatus, statusCalls = 0, requests = [], fallbackCalls = [], validationError = null;
let backend = 'https://backend.example';
const getBackendUrl=()=>backend, buildApiUrl=(base,path)=>base+path;
const gate = new Promise(resolve => releaseStatus = resolve);
const requireModelStatus = async () => {
  statusCalls++; await gate;
  if (validationError) throw validationError;
};
const isTrustedReview = r => !!r && r.trusted;
const requireTrustedReview = r => { if (!isTrustedReview(r)) throw Error('untrusted'); };
const response = seat => ({target_actor:seat, trusted:true});
const fetchReviewStream = (body, onProgress, url) =>
  new Promise((resolve, reject) => requests.push({body,onProgress,url,resolve,reject}));
const apiFetch = async (...args) => { fallbackCalls.push(args); return response(1); };
const wait = () => new Promise(resolve => setImmediate(resolve));
''' + constants + code + r'''
const watchdog = setTimeout(()=>{console.error('async test timed out'); process.exit(1)}, 5000);
(async () => {
  const source = {kind:'paipu',identity:'game',input:'https://example.test/?paipu=game'};
  const otherSource = {kind:'paipu',identity:'other-game',input:'other-game'};
  const progress=[], concurrentProgress=[], lateProgress=[];
  const first = getReview(source, 0, (...args)=>progress.push(args));
  const concurrent = getReview(source, 0, (...args)=>concurrentProgress.push(args));
  const other = getReview(otherSource, 0, ()=>{});
  await wait();
  assert.strictEqual(statusCalls,2); assert.strictEqual(requests.length,0);
  assert.deepStrictEqual(progress,[[0,'正在验证 Mortal S42 ONNX 模型...']]);
  assert.deepStrictEqual(concurrentProgress,progress);
  releaseStatus(); await wait();
  assert.strictEqual(requests.length,2);
  const one=requests.find(r=>r.body.paipu==='game');
  const two=requests.find(r=>r.body.paipu==='other-game');
  assert.deepStrictEqual(one.body,{paipu:'game',engine:'auto',temperature:0.1,target_actor:0});
  assert.deepStrictEqual(two.body,{paipu:'other-game',engine:'auto',temperature:0.1,target_actor:0});
  assert.strictEqual(one.url,'https://backend.example/api/analyze/stream');
  one.onProgress(42,'working');
  assert.deepStrictEqual(progress.at(-1),[42,'working']);
  assert.deepStrictEqual(concurrentProgress.at(-1),[42,'working']);
  const late = getReview(source, 0, (...args)=>lateProgress.push(args));
  assert.deepStrictEqual(lateProgress,[[42,'working']]);
  assert.strictEqual(requests.length,2); assert.strictEqual(statusCalls,2);
  const firstResult=response(0), otherResult=response(0);
  two.resolve(otherResult); one.resolve(firstResult);
  assert.strictEqual(await first,firstResult); assert.strictEqual(await concurrent,firstResult);
  assert.strictEqual(await late,firstResult); assert.strictEqual(await other,otherResult);

  const cachedProgress=[];
  assert.strictEqual(await getReview(otherSource,0,()=>{}),otherResult);
  assert.strictEqual(await getReview(source,0,(...args)=>cachedProgress.push(args)),firstResult);
  assert.strictEqual(cachedProgress[0][0],100); assert(cachedProgress[0][1].includes('复用'));
  assert.strictEqual(await getReview({kind:'paipu',identity:'game',input:'game'},0,()=>{}),firstResult);
  assert.strictEqual(requests.length,2); assert.strictEqual(statusCalls,2);

  // A different seat has its own request and result for the same game.
  const seatOne=getReview(source,1,()=>{}); await wait();
  assert.strictEqual(requests.length,3); assert.strictEqual(statusCalls,3);
  assert.strictEqual(requests.at(-1).body.target_actor,1);
  const seatOneResult=response(1); requests.at(-1).resolve(seatOneResult);
  assert.strictEqual(await seatOne,seatOneResult);
  assert.strictEqual(await getReview(source,1,()=>{}),seatOneResult);
  assert.strictEqual(await getReview(source,0,()=>{}),firstResult);
  assert.strictEqual(requests.length,3);

  // A stream failure is shared, never falls back, and leaves a manual retry possible.
  const failed=getReview(otherSource,1,()=>{});
  const failedDuplicate=getReview(otherSource,1,()=>{});
  await wait(); assert.strictEqual(requests.length,4);
  const failedBody=requests.at(-1).body;
  requests.at(-1).reject(Error('stream unavailable'));
  await Promise.all([assert.rejects(failed,/stream unavailable/),
    assert.rejects(failedDuplicate,/stream unavailable/)]);
  assert.deepStrictEqual(fallbackCalls,[]);
  const retry=getReview(otherSource,1,()=>{}); await wait();
  assert.strictEqual(requests.length,5); assert.deepStrictEqual(requests.at(-1).body,failedBody);
  const retryResult=response(1); requests.at(-1).resolve(retryResult);
  assert.strictEqual(await retry,retryResult); assert.deepStrictEqual(fallbackCalls,[]);

  // Identical records on different backends must not share cached results.
  backend='https://other-backend.example';
  const backendRequest=getReview(source,0,()=>{}); await wait();
  assert.strictEqual(requests.length,6);
  assert.strictEqual(requests.at(-1).url,'https://other-backend.example/api/analyze/stream');
  const backendResult=response(0); requests.at(-1).resolve(backendResult);
  assert.strictEqual(await backendRequest,backendResult);
  assert.notStrictEqual(backendResult,firstResult);
  backend='https://backend.example';

  const json='{"type":"start_game"}\n{"type":"end_game"}';
  const jsonSource={kind:'json',identity:json,input:json};
  const reported=Object.assign(new Error('保存失败'),{serverReported:true});
  const jsonFailure=getReview(jsonSource,0,()=>{}); await wait();
  assert.strictEqual(requests.at(-1).body.content,json);
  requests.at(-1).reject(reported);
  await assert.rejects(jsonFailure,error=>error===reported);
  assert.deepStrictEqual(fallbackCalls,[]);
  const jsonRetry=getReview(jsonSource,0,()=>{}); await wait();
  assert.strictEqual(requests.at(-1).body.content,json);
  const jsonResult=response(0); requests.at(-1).resolve(jsonResult);
  assert.strictEqual(await jsonRetry,jsonResult);

  // Failed model validation must not send an analysis request.
  const beforeInvalidModel=requests.length;
  validationError=Error('invalid model status');
  await assert.rejects(getReview({kind:'paipu',identity:'unready',input:'unready'},0,()=>{}),/invalid model status/);
  assert.strictEqual(requests.length,beforeInvalidModel);
  validationError=null;

  // A default-seat result can be reused both by default and by its resolved seat.
  const defaultSource={kind:'paipu',identity:'default-game',input:'default-game'};
  const defaultRequest=getReview(defaultSource,null); await wait();
  assert(!Object.prototype.hasOwnProperty.call(requests.at(-1).body,'target_actor'));
  const defaultResult=response(2); requests.at(-1).resolve(defaultResult);
  assert.strictEqual(await defaultRequest,defaultResult);
  const beforeDefaultReuse=requests.length;
  assert.strictEqual(await getReview(defaultSource,null),defaultResult);
  assert.strictEqual(await getReview(defaultSource,2,()=>{}),defaultResult);
  assert.strictEqual(requests.length,beforeDefaultReuse);
  assert.deepStrictEqual(fallbackCalls,[]);
})().then(()=>clearTimeout(watchdog)).catch(e => { console.error(e); process.exit(1); });
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf8", timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_no_automatic_review_preloading():
    source = javascript_source()
    assert "preloadOtherPerspectives" not in source
    assert "requestIdleCallback" not in source
    assert "idbReviewCache" not in source


def test_uploaded_file_is_passed_without_trimming():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    start = source.index("function readAndAnalyzeFile(")
    code = source[start:source.index("\n// ===", start)]
    script = r'''const assert = require('assert');
const state={selectedModel:'mortal-s42-onnx'}, notices=[];
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
assert.deepStrictEqual(observed,{content,model:undefined});
assert(notices.some(args=>args[0].includes('original.json')));
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf8", timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_stream_parses_chunks_reports_completion_and_releases_reader():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    code = source[source.index("async function fetchReviewStream("):source.index("const DEFAULT_MODELSCOPE_BACKEND")]
    script = r'''const assert = require('assert');
const getBackendUrl=()=>'', buildApiUrl=(_backend,path)=>path;
const isPagesProxyMode=()=>false, apiAuthHeaders=()=>({});
let chunks=[], toasts=[], progress=[], fetchCalls=[], readError=null, fetchError=null;
let responseOverride=null, releases=0, cancels=0;
const showToast=(...args)=>toasts.push(args);
const encode=text=>new TextEncoder().encode(text);
const fetch=async (url,options)=>{
  fetchCalls.push({url,options});
  if (fetchError) throw fetchError;
  if (responseOverride) return responseOverride;
  return {ok:true,body:{getReader:()=>({
    read:async()=>{
      if (chunks.length) return {done:false,value:chunks.shift()};
      if (readError) throw readError;
      return {done:true};
    },
    cancel:()=>{cancels++; return Promise.resolve();},
    releaseLock:()=>{releases++;},
  })}};
};
''' + code + r'''
const watchdog = setTimeout(()=>{console.error('async test timed out'); process.exit(1)}, 5000);
(async()=>{
  for (const persisted of [true,false,undefined]) {
    for (const newline of [true,false]) {
      const complete=JSON.stringify({type:'complete',result:{ok:true,nested:{value:7}},cache_persisted:persisted});
      const wire=': keepalive\r\nevent: progress\r\n'+
        'data: {"percent":37,\r\ndata: "message":"分析中"}\r\n\r\n'+
        'data: {"type":"queued","message":"queued"}\n\n'+
        'data: '+complete+(newline?'\n\n':'');
      const bytes=encode(wire), byteBuffer=Buffer.from(bytes);
      const cr=byteBuffer.indexOf(Buffer.from('\r\n'));
      const chinese=byteBuffer.indexOf(Buffer.from('分析中'));
      const completePart=byteBuffer.indexOf(Buffer.from('"value":7'));
      const cuts=[0,cr+1,chinese+1,chinese+2,completePart+4,bytes.length];
      chunks=cuts.slice(1).map((end,i)=>bytes.slice(cuts[i],end));
      // A terminated complete event must finish without waiting for stream EOF.
      readError=newline ? Error('read after complete') : null;
      progress=[];
      const result=await fetchReviewStream({paipu:'demo'},(...args)=>progress.push(args));
      readError=null;
      assert.deepStrictEqual(result,{ok:true,nested:{value:7}});
      assert.deepStrictEqual(progress,[[37,'分析中'],[5,'queued'],[100,'分析完成，正在加载回放...']]);
      assert.deepStrictEqual(toasts,[]);
    }
  }
  assert.strictEqual(fetchCalls.length,6);
  assert.strictEqual(fetchCalls[0].url,'/api/analyze/stream');
  assert.strictEqual(fetchCalls[0].options.method,'POST');
  const expectedBody = {paipu:'demo'};
  try {
    if (typeof DecompressionStream === 'function' && typeof Blob === 'function' &&
        typeof Blob.prototype.stream === 'function' && typeof Response === 'function' &&
        typeof Response.prototype.arrayBuffer === 'function' && typeof atob === 'function') {
      new DecompressionStream('gzip');
      expectedBody.accept_compression = 'gzip+base64';
    }
  } catch (_) {}
  assert.deepStrictEqual(JSON.parse(fetchCalls[0].options.body), expectedBody);
  assert.strictEqual(releases,6); assert.strictEqual(cancels,3);

  chunks=[encode('data: {"type":"error","detail":"bad JSON analysis"}\n\n')];
  await assert.rejects(fetchReviewStream({},()=>{}), error =>
    error.serverReported && /bad JSON analysis/.test(error.message));
  assert.strictEqual(releases,7); assert.strictEqual(cancels,4);

  chunks=[encode('data: {"type":\n\n')];
  await assert.rejects(fetchReviewStream({},()=>{}), error => error.receiveError && /数据格式/.test(error.message));
  assert.strictEqual(releases,8); assert.strictEqual(cancels,5);

  chunks=[encode('data: {"type":"progress","percent":42}\n\n')];
  await assert.rejects(fetchReviewStream({},()=>{}), error => error.receiveError && /未完整接收/.test(error.message));
  assert.strictEqual(releases,9); assert.strictEqual(cancels,5); // normal EOF, no complete event

  chunks=[]; readError=Error('connection lost');
  await assert.rejects(fetchReviewStream({},()=>{}), error => error.receiveError && /连接中断/.test(error.message));
  readError=null;
  assert.strictEqual(releases,10); assert.strictEqual(cancels,6);

  fetchError=Error('offline');
  await assert.rejects(fetchReviewStream({},()=>{}), /重复推理/);
  fetchError=null;
  responseOverride={ok:false,status:503,statusText:'Unavailable',text:async()=>''};
  await assert.rejects(fetchReviewStream({},()=>{}), /HTTP 503/);
  responseOverride={ok:true,body:null};
  await assert.rejects(fetchReviewStream({},()=>{}), error => error.receiveError && /无法读取/.test(error.message));
  assert.strictEqual(releases,10); assert.strictEqual(cancels,6);
})().then(()=>clearTimeout(watchdog)).catch(e=>{console.error(e);process.exit(1)});
'''
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf8", timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_frontend_requires_complete_onnx_attestation():
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    source = javascript_source()
    code = source[source.index("const REVIEW_SCHEMA ="):source.index("// Only the latest request")]
    script = r"""const assert=require('assert');
const state={serverStatus:{engine:{has_mortal_weights:true,active_engine:'mortal',
  model_revision:'2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a',
  weight_sha256:'30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4',
  review_schema:'mortal-onnx-2f7ad48-v1'}}};
const refreshServerStatus=async()=>{};
""" + code + r"""
const engine={type:'mortal',id:'mortal-s42-onnx',weight_file:'model.onnx',version:4,steps:null,
 revision:MODEL_REVISION,weight_sha256:MODEL_SHA256,q_source:'neural_network',review_schema:REVIEW_SCHEMA};
assert(isTrustedReview({engine}));
for (const [key,value] of Object.entries({type:'builtin',id:'akagi',weight_file:'old.pth',version:3,
 steps:582500,revision:'bad',weight_sha256:'bad',q_source:'heuristic',review_schema:'old'})) {
  assert(!isTrustedReview({engine:{...engine,[key]:value}}),key);
  const missing={...engine}; delete missing[key]; assert(!isTrustedReview({engine:missing}),key);
}
for (const key of ['has_mortal_weights','active_engine','model_revision','weight_sha256','review_schema']) {
 const old=state.serverStatus.engine[key]; state.serverStatus.engine[key]=null;
 assert(!isTrustedReview({engine}),key); state.serverStatus.engine[key]=old;
}
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf8", timeout=10)
    assert proc.returncode == 0, proc.stderr
