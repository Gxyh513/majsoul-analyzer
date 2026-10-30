"""Regression checks for Pages same-origin proxy mode in the browser client."""
import shutil
import subprocess

import pytest

from frontend_assets import javascript_source


def source_between(start, end):
    text = javascript_source()
    return text[text.index(start):text.index(end)]


def run_js(script):
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_pages_mode_uses_same_origin_and_never_sends_client_credentials():
    code = source_between("function isPagesProxyMode()", "function isBackendOnline(")
    script = r"""const assert = require('assert');
const saved = new Map([['majsoul_api_token:https://gxyh513-majsoul-analyzer.ms.show','session-secret']]);
const legacy = new Map([['majsoul_api_token:https://gxyh513-majsoul-analyzer.ms.show','legacy-secret']]);
const sessionStorage = {get length(){return saved.size;},key:i=>[...saved.keys()][i],getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
const localStorage = {get length(){return legacy.size;},key:i=>[...legacy.keys()][i],getItem:k=>legacy.get(k)||null,setItem:(k,v)=>legacy.set(k,v),removeItem:k=>legacy.delete(k)};
const window = {location:new URL('https://majsoul.ccwu.cc/?backend=https%3A%2F%2Fevil.example#api_token=fragment-secret'),history:{replaceState:(_a,_b,u)=>{window.location=new URL(u)}}};
let calls=[];
const fetch=async (url,options)=>{
  calls.push({url,options});
  if(url==='/api/analyze/stream'){
    let sent=false;
    const line='data: {"type":"complete","result":{"ok":true}}\n\n';
    return {ok:true,body:{getReader:()=>({read:async()=>sent?{done:true}:(sent=true,{done:false,value:new TextEncoder().encode(line)}),cancel:async()=>{},releaseLock:()=>{}})}};
  }
  return {ok:true,json:async()=>({ok:true})};
};
""" + code + r"""
(async()=>{
  assert.strictEqual(isPagesProxyMode(),true);
  assert.strictEqual(getBackendUrl(),'');
  assert.strictEqual(buildApiUrl('https://evil.example','/api/status'),'/api/status');
  await apiFetch('/api/status',{headers:{'X-Api-Token':'caller-token','Authorization':'Bearer caller','Cookie':'browser-cookie'}});
  const result=await fetchReviewStream({paipu:'demo'},()=>{});
  assert.deepStrictEqual(result,{ok:true});
  assert.strictEqual(window.location.hash,'');
  assert.strictEqual(saved.size,0);
  assert.strictEqual(legacy.size,0);
  assert.deepStrictEqual(calls.map(c=>c.url),['/api/status','/api/analyze/stream']);
  for(const call of calls){
    const headers=new Headers(call.options.headers);
    for(const name of ['X-Api-Token','Authorization','Cookie','X-Studio-Token']) assert.strictEqual(headers.has(name),false);
    assert.strictEqual(call.options.credentials,'omit');
    assert.strictEqual(call.options.redirect,'error');
    assert(!String(call.url).includes('evil.example'));
    assert(!String(call.url).includes('api_token'));
  }
})().catch(e=>{console.error(e);process.exit(1)});
"""
    run_js(script)


def test_pages_preview_domains_use_proxy_mode():
    code = source_between("function isPagesProxyMode()", "function clearLegacyApiTokens()")
    script = r"""const assert = require('assert');
const window={location:new URL('https://f5b7d21f.majsoul-analyzer.pages.dev/')};
""" + code + r"""
assert.strictEqual(isPagesProxyMode(),true);
window.location=new URL('https://majsoul-analyzer.pages.dev/');
assert.strictEqual(isPagesProxyMode(),true);
window.location=new URL('https://gxyh513-majsoul-analyzer.ms.show/');
assert.strictEqual(isPagesProxyMode(),false);
"""
    run_js(script)
