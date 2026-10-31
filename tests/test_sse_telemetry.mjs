import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import vm from "node:vm";
import { createSseByteMeter } from "../shared/sse-metrics.js";
import { onRequest } from "../functions/api/[[path]].js";

const encoder = new TextEncoder();
const requestId = "1234567890abcdef1234567890abcdef";
const progress = 'data: {"type": "progress", "percent": 100, "message": "完成"}\n\n';
const complete = 'data: {"type": "complete", "result": {"text": "测试"}}\n\n';

test("SSE meter counts UTF-8 bytes and handles split or combined frames", () => {
  const bytes = encoder.encode(progress + complete);
  const progressBytes = encoder.encode(progress).byteLength;
  const pieces = [bytes.slice(0, 6), bytes.slice(6, progressBytes + 8), bytes.slice(progressBytes + 8)];
  const meter = createSseByteMeter();
  const events = [];
  for (const [i, piece] of pieces.entries()) {
    const found = meter.add(piece, i + 1, 10 + i);
    meter.forwarded(i + 1, 20 + i, found.finished);
    events.push(...found.finished);
  }
  assert.deepEqual(events.map(event => event.kind), ["100%", "complete"]);
  assert.equal(events[0].bytes, progressBytes);
  assert.equal(events[1].bytes, encoder.encode(complete).byteLength);
  assert.equal(events[0].lastChunk, events[1].firstChunk);
  assert.equal(events[1].firstForwardAt, 21);
});

test("SSE meter counts CRLF delimiters split across chunks", () => {
  const wire = encoder.encode((progress + complete).replaceAll("\n", "\r\n"));
  const meter = createSseByteMeter();
  const finished = [];
  for (let index = 0; index < wire.length; index++) {
    const found = meter.add(wire.slice(index, index + 1), index + 1, index);
    meter.forwarded(index + 1, index, found.finished);
    finished.push(...found.finished);
  }
  assert.deepEqual(finished.map(event => event.kind), ["100%", "complete"]);
  assert.equal(finished[0].bytes, encoder.encode(progress.replaceAll("\n", "\r\n")).byteLength);
  assert.equal(finished[1].bytes, encoder.encode(complete.replaceAll("\n", "\r\n")).byteLength);
});

test("Pages proxy forwards each chunk before the upstream finishes and propagates cancellation", async () => {
  const realFetch = globalThis.fetch;
  let controller;
  let cancelled = false;
  let upstreamRequestId;
  const logs = [];
  const originalInfo = console.info;
  console.info = (...args) => logs.push(args.join(" "));
  const upstream = new ReadableStream({
    start(c) { controller = c; c.enqueue(encoder.encode(progress)); },
    cancel() { cancelled = true; },
  });
  globalThis.fetch = async (_url, init) => {
    upstreamRequestId = new Headers(init.headers).get("X-Request-ID");
    return new Response(upstream, { headers: { "Content-Type": "text/event-stream", "X-Request-ID": requestId } });
  };
  try {
    const request = new Request("https://pages.example/api/analyze/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
      body: JSON.stringify({ paipu: "demo" }),
    });
    const response = await onRequest({ request, env: { MAJSOUL_API_TOKEN: "secret" } });
    assert.equal(upstreamRequestId, requestId);
    assert.equal(response.headers.get("X-Request-ID"), requestId);
    const reader = response.body.getReader();
    const first = await reader.read();
    assert.equal(new TextDecoder().decode(first.value), progress);
    const completeBytes = encoder.encode(complete);
    controller.enqueue(completeBytes.slice(0, 32));
    const second = await reader.read();
    assert.deepEqual(second.value, completeBytes.slice(0, 32));
    assert.ok(logs.some(line => line.includes("事件=complete 首块=")));
    controller.enqueue(completeBytes.slice(32));
    const third = await reader.read();
    assert.deepEqual(third.value, completeBytes.slice(32));
    await reader.cancel();
    assert.equal(cancelled, true);
    assert.ok(logs.every(line => !line.includes("测试")));
  } finally {
    console.info = originalInfo;
    globalThis.fetch = realFetch;
  }
});

test("Pages proxy passes gzip through unread and requests identity when gzip is refused", async () => {
  const realFetch = globalThis.fetch;
  const originalInfo = console.info;
  const raw = gzipSync(Buffer.from(progress + complete));
  let upstream, upstreamEncoding, controller;
  console.info = () => {};
  try {
    globalThis.fetch = async (_url, init) => {
      upstreamEncoding = new Headers(init.headers).get("Accept-Encoding");
      upstream = new Response(new ReadableStream({
        start(c) { controller = c; c.enqueue(raw); },
      }, { highWaterMark: 0 }), { headers: { "Content-Type": "text/event-stream", "Content-Encoding": "gzip" } });
      return upstream;
    };
    const makeRequest = encoding => new Request("https://pages.example/api/analyze/stream", {
      method: "POST", headers: { "Content-Type": "application/json", ...(encoding == null ? {} : { "Accept-Encoding": encoding }), "X-Request-ID": requestId },
      body: JSON.stringify({ paipu: "demo" }),
    });
    const response = await onRequest({ request: makeRequest("gzip"), env: { MAJSOUL_API_TOKEN: "secret" } });
    assert.equal(upstreamEncoding, "gzip");
    assert.equal(upstream.bodyUsed, false);
    assert.equal(response.headers.get("Content-Encoding"), "gzip");
    assert.equal(response.body, upstream.body);
    const reader = response.body.getReader();
    assert.deepEqual(Buffer.from((await reader.read()).value), raw);
    await reader.cancel();

    for (const encoding of [null, "gzip;q=0", "gzip;q=0, *;q=1", "br"]) {
      globalThis.fetch = async (_url, init) => {
        upstreamEncoding = new Headers(init.headers).get("Accept-Encoding");
        return new Response(progress + complete, { headers: { "Content-Type": "text/event-stream" } });
      };
      const identity = await onRequest({ request: makeRequest(encoding), env: { MAJSOUL_API_TOKEN: "secret" } });
      assert.equal(upstreamEncoding, "identity");
      assert.equal(identity.headers.get("Content-Encoding"), "identity");
      assert.equal(await identity.text(), progress + complete);
    }
    const debugRequest = new Request("https://pages.example/api/analyze/stream", {
      method: "POST", headers: { "Content-Type": "application/json", "Accept-Encoding": "gzip", "X-Paipu-Proxy-Debug": "1" },
      body: JSON.stringify({ paipu: "demo" }),
    });
    const debugResponse = await onRequest({ request: debugRequest, env: { MAJSOUL_API_TOKEN: "secret" } });
    assert.equal(upstreamEncoding, "identity");
    assert.equal(await debugResponse.text(), progress + complete);
  } finally {
    console.info = originalInfo;
    globalThis.fetch = realFetch;
  }
});

test("Pages proxy streams the independent performance frame after complete", async () => {
  const realFetch = globalThis.fetch;
  const originalInfo = console.info;
  let upstreamController;
  const diagnostic = `data: ${JSON.stringify({ type: "performance", performance: {
    "事件": "牌谱性能统计", "schema版本": 2, "请求ID": requestId,
    "耗时_ms": { "未采集": null }, "数据大小_KB": { "结果": Number.MAX_SAFE_INTEGER },
    "状态": { "标签": '中文 "quote"\nbackslash\\' },
  } })}\n\n`;
  console.info = () => {};
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) { upstreamController = controller; controller.enqueue(encoder.encode(complete)); },
  }), { headers: { "Content-Type": "text/event-stream", "X-Request-ID": requestId } });
  try {
    const request = new Request("https://pages.example/api/analyze/stream", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
      body: JSON.stringify({ paipu: "demo" }),
    });
    const response = await onRequest({ request, env: { MAJSOUL_API_TOKEN: "secret" } });
    const reader = response.body.getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), complete);
    upstreamController.enqueue(encoder.encode(diagnostic));
    const received = new TextDecoder().decode((await reader.read()).value);
    assert.equal(received, diagnostic);
    assert.equal(JSON.parse(received.slice(6)).performance["数据大小_KB"]["结果"], Number.MAX_SAFE_INTEGER);
    upstreamController.close();
    assert.equal((await reader.read()).done, true);
  } finally {
    globalThis.fetch = realFetch;
    console.info = originalInfo;
  }
});

test("Pages proxy reports only an actual upstream request ID", async () => {
  const realFetch = globalThis.fetch;
  const originalInfo = console.info;
  const logs = [];
  const serverId = "abcdef1234567890abcdef1234567890";
  console.info = (...args) => logs.push(args.join(" "));
  try {
    for (const upstreamId of [serverId, null, "invalid-id"]) {
      globalThis.fetch = async () => {
        const headers = { "Content-Type": "text/event-stream" };
        if (upstreamId) headers["X-Request-ID"] = upstreamId;
        return new Response(complete, { headers });
      };
      const request = new Request("https://pages.example/api/analyze/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
        body: JSON.stringify({ paipu: "demo" }),
      });
      const response = await onRequest({ request, env: { MAJSOUL_API_TOKEN: "secret" } });
      assert.equal(response.headers.get("X-Request-ID"), upstreamId === serverId ? serverId : null);
      assert.equal(await response.text(), complete);
    }
    assert.ok(logs.some(line => line.includes(`客户端请求ID=${requestId} 服务端请求ID=${serverId}`)));
  } finally {
    globalThis.fetch = realFetch;
    console.info = originalInfo;
  }
});

test("browser metrics use read boundaries and defer 100% until complete parses", async () => {
  const source = await readFile(new URL("../web/js/api.js", import.meta.url), "utf8");
  const code = source.slice(source.indexOf("async function fetchReviewStream("), source.indexOf("const DEFAULT_MODELSCOPE_BACKEND"));
  const bytes = encoder.encode(progress + complete);
  const progressBytes = encoder.encode(progress).byteLength;
  const chunks = [bytes.slice(0, progressBytes + 4), bytes.slice(progressBytes + 4, progressBytes + 17), bytes.slice(progressBytes + 17)];
  const updates = [];
  const logs = [];
  let receivedHeaders;
  let cancelled = false;
  const context = {
    TextDecoder, Uint8Array, URL, performance, crypto: globalThis.crypto,
    console: {
      groupCollapsed: (title) => logs.push({ title, entries: [] }),
      log: (label, value) => logs.at(-1).entries.push({ label, value }),
      groupEnd() {},
    },
    window: { location: new URL("https://example.com/"), __PAIPU_DEBUG_SSE__: true },
    document: { visibilityState: "visible", addEventListener() {}, removeEventListener() {} },
    getBackendUrl: () => "", buildApiUrl: (_backend, path) => path,
    isPagesProxyMode: () => true, apiAuthHeaders: () => ({}),
    fetch: async (_url, options) => {
      receivedHeaders = options.headers;
      return { ok: true, body: { getReader: () => ({
        read: async () => chunks.length ? { done: false, value: chunks.shift() } : { done: true },
        cancel: async () => { cancelled = true; }, releaseLock() {},
      }) } };
    },
  };
  vm.createContext(context);
  vm.runInContext(code + "\nglobalThis.runStream = fetchReviewStream;", context);
  const timings = {};
  const result = await context.runStream({ paipu: "demo" }, (...values) => updates.push(values), "/api/analyze/stream", timings);
  assert.equal(result.text, "测试");
  assert.deepEqual(updates.map(item => item[0]), [99, 100]);
  assert.match(updates[0][1], /正在接收结果/);
  assert.equal(timings.sse.completeBytes, encoder.encode(complete).byteLength);
  assert.equal(timings.sse.sameChunk, true);
  assert.equal(timings.sse.chunkCount, 3);
  assert.ok(timings.diagnostics.completeReadWaitMs >= 0);
  assert.ok(timings.diagnostics.completeBetweenReadsMs >= 0);
  assert.match(receivedHeaders["X-Request-ID"], /^[0-9a-f]{32}$/);
  assert.equal(timings.requestId, receivedHeaders["X-Request-ID"]);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(cancelled, false);
  assert.equal(logs.length, 0);
});

test("browser receives HTTP-decoded JSON and completes before diagnostics", async () => {
  const source = await readFile(new URL("../web/js/api.js", import.meta.url), "utf8");
  const code = source.slice(source.indexOf("async function fetchReviewStream("), source.indexOf("const DEFAULT_MODELSCOPE_BACKEND"));
  const expected = { text: "牌谱内容", nested: { values: [1, 2, 3] } };
  let controller, requestBody;
  const wire = encoder.encode(progress + `data: ${JSON.stringify({ type: "complete", result: expected })}\n\n`);
  const stream = new ReadableStream({ start(c) { controller = c; c.enqueue(wire); } });
  const updates = [], groups = [];
  const context = {
    TextDecoder, Uint8Array, URL, performance, crypto: globalThis.crypto, setTimeout, clearTimeout,
    console: { groupCollapsed: title => groups.push({ title, entries: [] }),
      log: (label, value) => groups.at(-1).entries.push({ label, value }), groupEnd() {} },
    document: { visibilityState: "visible", addEventListener() {}, removeEventListener() {} },
    getBackendUrl: () => "", buildApiUrl: (_backend, path) => path,
    isPagesProxyMode: () => true, apiAuthHeaders: () => ({}),
    fetch: async (_url, options) => {
      requestBody = JSON.parse(options.body);
      // Fetch exposes automatically decompressed bytes even for a gzip response.
      return { ok: true, headers: { get: name => name === "Content-Encoding" ? "gzip" : requestId }, body: stream };
    },
  };
  vm.createContext(context);
  vm.runInContext(code + "\nglobalThis.runStream = fetchReviewStream;", context);
  const timings = {};
  const result = await Promise.race([
    context.runStream({ paipu: "demo", accept_compression: "gzip+base64" }, pct => updates.push(pct), "/api/analyze/stream", timings),
    new Promise((_, reject) => setTimeout(() => reject(Error("complete waited for diagnostics")), 300)),
  ]);
  assert.deepEqual(requestBody, { paipu: "demo" });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), expected);
  assert.deepEqual(updates, [99, 100]);
  assert.equal(groups.length, 0);
  assert.ok(timings.sse.completeParsedAt >= timings.sse.completeEventParsedAt);
  const diagnostic = { "请求ID": requestId, "耗时_ms": { "complete Base64编码耗时": null } };
  controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "performance", performance: diagnostic })}\n\n`));
  controller.close();
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(groups.length, 0);
});

test("browser accepts original JSON and rejects a missing complete result", async () => {
  const source = await readFile(new URL("../web/js/api.js", import.meta.url), "utf8");
  const code = source.slice(source.indexOf("async function fetchReviewStream("), source.indexOf("const DEFAULT_MODELSCOPE_BACKEND"));
  const chunks = [encoder.encode(complete)];
  let requestBody;
  const context = {
    TextDecoder, Uint8Array, URL, performance, crypto: globalThis.crypto,
    console: { info() {} },
    document: { visibilityState: "visible", addEventListener() {}, removeEventListener() {} },
    getBackendUrl: () => "", buildApiUrl: (_backend, path) => path,
    isPagesProxyMode: () => true, apiAuthHeaders: () => ({}),
    fetch: async (_url, options) => {
      requestBody = JSON.parse(options.body);
      return { ok: true, body: { getReader: () => ({
        read: async () => chunks.length ? { done: false, value: chunks.shift() } : { done: true },
        cancel: async () => {}, releaseLock() {},
      }) } };
    },
  };
  vm.createContext(context);
  vm.runInContext(code + "\nglobalThis.runStream = fetchReviewStream;", context);
  const result = await context.runStream({ paipu: "demo", accept_compression: "gzip+base64" }, () => {}, "/api/analyze/stream");
  assert.equal(requestBody.accept_compression, undefined);
  assert.equal(result.text, "测试");
  chunks.push(encoder.encode(`data: ${JSON.stringify({ type: "complete" })}\n\n`));
  const updates = [];
  await assert.rejects(context.runStream({ paipu: "demo" }, (pct) => updates.push(pct), "/api/analyze/stream"),
    error => error.receiveError === true);
  assert.equal(requestBody.accept_compression, undefined);
  assert.deepEqual(updates, []);
});
