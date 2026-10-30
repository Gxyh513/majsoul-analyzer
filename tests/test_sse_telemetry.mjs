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
    return new Response(upstream, { headers: { "Content-Type": "text/event-stream" } });
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
    TextDecoder, Uint8Array, performance, crypto: globalThis.crypto,
    console: { info: (...args) => logs.push(args.join(" ")) },
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
  assert.equal(cancelled, true);
  assert.ok(logs.some(line => line.includes("read明细")));
  assert.ok(logs.every(line => !line.includes("测试")));
});

test("browser negotiates gzip and restores the complete result before 100%", {
  skip: typeof DecompressionStream !== "function" || typeof atob !== "function" ||
    typeof Blob !== "function" || typeof Response !== "function",
}, async () => {
  const source = await readFile(new URL("../web/js/api.js", import.meta.url), "utf8");
  const code = source.slice(source.indexOf("async function fetchReviewStream("), source.indexOf("const DEFAULT_MODELSCOPE_BACKEND"));
  const expected = { text: "牌谱内容", nested: { values: [1, 2, 3] } };
  const data = gzipSync(Buffer.from(JSON.stringify(expected))).toString("base64");
  const wire = encoder.encode(progress + `data: ${JSON.stringify({ type: "complete", encoding: "gzip+base64", data })}\n\n`);
  const chunks = [wire.slice(0, 40), wire.slice(40)];
  const updates = [];
  let requestBody;
  const context = {
    TextDecoder, Uint8Array, Blob, Response, DecompressionStream, atob, performance, crypto: globalThis.crypto,
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
  const timings = {};
  const result = await context.runStream({ paipu: "demo" }, (pct) => updates.push(pct), "/api/analyze/stream", timings);
  assert.equal(requestBody.accept_compression, "gzip+base64");
  assert.deepEqual(JSON.parse(JSON.stringify(result)), expected);
  assert.deepEqual(updates, [99, 100]);
  assert.ok(timings.sse.completeParsedAt >= timings.sse.completeEventParsedAt);

  chunks.push(encoder.encode(complete));
  updates.length = 0;
  const legacy = await context.runStream({ paipu: "demo" }, (pct) => updates.push(pct), "/api/analyze/stream");
  assert.equal(requestBody.accept_compression, "gzip+base64");
  assert.equal(legacy.text, "测试");
  assert.deepEqual(updates, [100]);
});

test("browser without gzip support requests and accepts the original JSON", async () => {
  const source = await readFile(new URL("../web/js/api.js", import.meta.url), "utf8");
  const code = source.slice(source.indexOf("async function fetchReviewStream("), source.indexOf("const DEFAULT_MODELSCOPE_BACKEND"));
  const chunks = [encoder.encode(complete)];
  let requestBody;
  const context = {
    TextDecoder, Uint8Array, performance, crypto: globalThis.crypto,
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
  const data = gzipSync(Buffer.from(JSON.stringify({ text: "测试" }))).toString("base64");
  chunks.push(encoder.encode(`data: ${JSON.stringify({ type: "complete", encoding: "gzip+base64", data })}\n\n`));
  const updates = [];
  await assert.rejects(context.runStream({ paipu: "demo" }, (pct) => updates.push(pct), "/api/analyze/stream"),
    error => error.receiveError === true);
  assert.equal(requestBody.accept_compression, undefined);
  assert.deepEqual(updates, []);
});
