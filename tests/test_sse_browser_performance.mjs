import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const apiSource = await readFile(new URL("../web/js/api.js", import.meta.url), "utf8");
const apiCode = apiSource.slice(apiSource.indexOf("async function fetchReviewStream("), apiSource.indexOf("const DEFAULT_MODELSCOPE_BACKEND"));
const reviewSource = await readFile(new URL("../web/js/review.js", import.meta.url), "utf8");
const encoder = new TextEncoder();
const clientId = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const serverId = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const progress = 'data: {"type":"progress","percent":100,"message":"完成"}\n\n';
const complete = 'data: {"type":"complete","result":{"text":"private-result"}}\n\n';
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`;
const kb = (bytes) => Number((bytes / 1024).toFixed(2));

function captureConsole() {
  const groups = [];
  const stack = [];
  return {
    groups,
    console: {
      groupCollapsed(title) {
        const group = { title, entries: [], closed: false };
        groups.push(group);
        stack.push(group);
      },
      log(label, value) {
        assert.ok(stack.length, "performance output must be grouped");
        assert.equal(typeof value, "object", "performance output must be an object or array");
        assert.notEqual(value, null);
        stack.at(-1).entries.push({ label, value });
      },
      groupEnd() {
        assert.ok(stack.length, "groupEnd must match groupCollapsed");
        stack.pop().closed = true;
      },
    },
  };
}

function groupFor(groups, prefix) {
  return groups.filter((group) => group.title.startsWith(prefix));
}

function entryFor(groups, prefix, label) {
  return groupFor(groups, prefix).flatMap((group) => group.entries.filter((entry) => entry.label === label).map((entry) => entry.value));
}

function createHarness({ reader, debug = false, responseId = null, headersVisible = true,
  clock = { now: 0 }, observer = null, token = "credential-value", origin = "https://app.example", timers = {} } = {}) {
  const capture = captureConsole();
  const requests = [];
  const listeners = new Map();
  const document = {
    visibilityState: "visible",
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name, fn) { if (listeners.get(name) === fn) listeners.delete(name); },
  };
  const location = new URL(`${origin}/`);
  const context = {
    TextDecoder, Uint8Array, URL,
    setTimeout: timers.setTimeout || setTimeout, clearTimeout: timers.clearTimeout || clearTimeout,
    crypto: { randomUUID: () => uuid },
    performance: { timeOrigin: 1700000000000, now: () => clock.now },
    console: capture.console,
    document,
    window: { location, __PAIPU_DEBUG_SSE__: debug },
    getBackendUrl: () => "", buildApiUrl: (_backend, path) => path,
    isPagesProxyMode: () => false, apiAuthHeaders: () => ({ "X-Api-Token": token }),
    fetch: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        // Fetch supplies decoded body bytes even when the HTTP response is gzip encoded.
        headers: { get: (name) => name === "Content-Encoding" ? "gzip" : (headersVisible ? responseId : null) },
        body: { getReader: () => reader },
      };
    },
  };
  if (observer) context.PerformanceObserver = observer;
  vm.createContext(context);
  vm.runInContext(apiCode + "\nglobalThis.runStream = fetchReviewStream;", context);
  return {
    context, logs: capture.groups, requests, clock, document,
    visibility(state) { document.visibilityState = state; listeners.get("visibilitychange")?.(); },
    listenerCount: () => listeners.size,
  };
}

function recordedReader(chunks, waits, clock, onRead = () => {}) {
  let index = 0;
  let cancelled = false;
  let released = false;
  return {
    async read() {
      const current = index++;
      clock.now += waits[current] ?? 0;
      onRead(current);
      if (current >= chunks.length) return { done: true, value: undefined };
      const item = chunks[current];
      if (item instanceof Error) throw item;
      return { done: false, value: item };
    },
    async cancel() { cancelled = true; },
    releaseLock() { released = true; },
    get cancelled() { return cancelled; },
    get released() { return released; },
  };
}

function splitBytes(bytes, count) {
  const parts = [];
  const size = Math.ceil(bytes.length / count);
  for (let offset = 0; offset < bytes.length; offset += size) {
    parts.push(bytes.subarray(offset, Math.min(bytes.length, offset + size)));
  }
  return parts;
}

async function waitFor(predicate, description = "expected event", timeout = 2000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`Timed out waiting for ${description}`);
}

test("default grouped summary cancels browser console logs while preserving stream delivery and timings", async () => {
  const clock = { now: 0 };
  const performanceEvent = { type: "performance", performance: {
    "事件": "牌谱性能统计", "schema版本": 2, "请求ID": clientId, "总耗时_ms": 12.5,
    "耗时_ms": { "雀魂抓取": 12.5, "原始牌谱获取总耗时": 4 },
    "数据大小_KB": { "结果": 5.25, "原始牌谱": 1.25 }, "计数": { "请求": 1 },
    "状态": { "连接": "成功", password: "do-not-log-password",
      nested: { authorization: "Bearer do-not-log-auth", note: "credential-value" },
      link: "https://user:pass@example.test/?token=not-for-logs", review: { text: "private-result" } },
    "压缩比例": { "HTTP": 0.42 }, "计时口径": { "雀魂抓取": "抓取开始至结束", "原始牌谱获取总耗时": "原始牌谱获取范围" },
    "当前进行中阶段": ["雀魂抓取", "ONNX推理"],
    result: { text: "private-result" },
  } };
  const wire = encoder.encode(progress + complete + frame(performanceEvent));
  const chunks = splitBytes(wire, 20);
  let harness;
  const reader = recordedReader(chunks, Array.from({ length: 21 }, (_, i) => i + 1), clock,
    (index) => { if (index === 5) harness.visibility("hidden"); });
  class LongTaskObserver {
    static supportedEntryTypes = ["longtask"];
    constructor(callback) { this.callback = callback; }
    observe() {}
    takeRecords() { return [{ startTime: 2, duration: 55 }]; }
    disconnect() {}
  }
  harness = createHarness({ reader, responseId: clientId, clock, observer: LongTaskObserver, debug: true });
  const updates = [];
  const timings = {};
  const result = await harness.context.runStream({ paipu: "input-never-logged" }, (...args) => updates.push(args), "/api/analyze/stream", timings);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { text: "private-result" });
  assert.deepEqual(updates.map(([percent]) => percent), [99, 100]);
  await new Promise((resolve) => setTimeout(resolve, 50));

  // Console output is canceled
  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0);
  assert.equal(harness.requests[0].options.headers["X-Request-ID"], clientId);
  assert.equal(timings.requestId, clientId);
  assert.equal(timings.serverRequestId, clientId);
  assert.equal(timings.diagnostics.longTaskCount, 1);
});

test("default output and debug mode produce no custom browser console logs", async () => {
  for (const debug of [false, true]) {
    const clock = { now: 0 };
    const count = 1200;
    const wire = encoder.encode(frame({ type: "complete", result: { text: "private-result".repeat(400) } }));
    const chunks = splitBytes(wire, count);
    const reader = recordedReader(chunks, [], clock);
    const harness = createHarness({ reader, debug, clock });
    const result = await harness.context.runStream({}, () => {}, "/api/analyze/stream");
    assert.equal(result.text.length, "private-result".length * 400);
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
    assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0);
    assert.equal(JSON.stringify(harness.logs).includes("private-result"), false);
  }
});

test("failed reads record safe error information without leaking the original error", async () => {
  const clock = { now: 0 };
  const failure = new TypeError("secret-read-error https://example.test/?token=private");
  const reader = recordedReader([encoder.encode('data: {"type":"progress","percent":42}\n\n'), failure], [7, 13], clock);
  const harness = createHarness({ reader, clock, debug: true });
  await assert.rejects(harness.context.runStream({}, () => {}, "/api/analyze/stream"), (error) => error.receiveError === true);
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(reader.cancelled, true);
  assert.equal(reader.released, true);
  assert.equal(JSON.stringify(harness.logs).includes("secret-read-error"), false);
  assert.equal(JSON.stringify(harness.logs).includes("example.test"), false);

  const emptyReader = recordedReader([new Error("no data")], [5], clock);
  const empty = createHarness({ reader: emptyReader, clock });
  await assert.rejects(empty.context.runStream({}, () => {}, "/api/analyze/stream"));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(groupFor(empty.logs, "[Paipu SSE Browser]").length, 0);
});

test("complete is delivered before a delayed post-complete performance event and EOF", async () => {
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; c.enqueue(encoder.encode(progress + complete)); } });
  const harness = createHarness({ reader: body.getReader(), responseId: clientId });
  const updates = [];
  const timings = {};
  const result = await Promise.race([
    harness.context.runStream({}, (...args) => updates.push(args), "/api/analyze/stream", timings),
    new Promise((_, reject) => setTimeout(() => reject(new Error("completion waited for diagnostics")), 300)),
  ]);
  assert.equal(result.text, "private-result");
  assert.deepEqual(updates.map(([percent]) => percent), [99, 100]);
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0);
  assert.equal(body.locked, true);
  const bytes = encoder.encode(frame({ type: "performance", performance: { "请求ID": serverId, "总耗时_ms": 123 } }).replaceAll("\n", "\r\n"));
  controller.enqueue(bytes.slice(0, 17));
  controller.enqueue(bytes.slice(17, bytes.length - 3));
  controller.enqueue(bytes.slice(bytes.length - 3));
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(timings.serverRequestId, serverId);
  controller.close();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(body.locked, false);
});

test("stalled diagnostic tail times out and releases the reader without delaying complete", async () => {
  let timeoutCallback;
  const timers = {
    setTimeout(fn, delay) {
      if (delay === 10000) { timeoutCallback = fn; return 9999; }
      return setTimeout(fn, delay);
    },
    clearTimeout(id) { if (id !== 9999) clearTimeout(id); },
  };
  const body = new ReadableStream({ start(controller) { controller.enqueue(encoder.encode(complete)); } });
  const harness = createHarness({ reader: body.getReader(), timers, debug: true });
  const result = await harness.context.runStream({}, () => {}, "/api/analyze/stream");
  assert.equal(result.text, "private-result");
  await waitFor(() => timeoutCallback, "tail timeout installation");
  assert.equal(body.locked, true);
  timeoutCallback();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(body.locked, false);
  assert.equal(harness.listenerCount(), 0);
});

test("same-chunk, duplicate, malformed and absent performance events remain optional", async () => {
  const clock = { now: 0 };
  const event = frame({ type: "performance", performance: { "总耗时_ms": 42 } });
  const reader = recordedReader([encoder.encode(progress + complete + event + event)], [1, 2], clock);
  const harness = createHarness({ reader, clock });
  const updates = [];
  const result = await harness.context.runStream({}, (percent) => updates.push(percent), "/api/analyze/stream");
  assert.equal(result.text, "private-result");
  assert.deepEqual(updates, [99, 100]);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0);

  const bad = 'event: performance\ndata: {"type":"performance","performance":broken}\n\n';
  const trailingBusiness = 'data: {"type":"progress","percent":1}\n\ndata: {"type":"error","detail":"late"}\n\n';
  const reader2 = recordedReader([encoder.encode(bad + complete + trailingBusiness)], [1, 2], clock);
  const oldServer = createHarness({ reader: reader2, clock });
  const progressValues = [];
  assert.equal((await oldServer.context.runStream({}, (value) => progressValues.push(value), "/api/analyze/stream")).text, "private-result");
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(progressValues, [100]);
  assert.equal(groupFor(oldServer.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(groupFor(oldServer.logs, "[Paipu Performance]").length, 0);
  assert.equal(reader2.released, true);

  const unterminated = encoder.encode('data: {"type":"complete","result":{"ok":true}}');
  const reader3 = recordedReader([unterminated], [1, 2], clock);
  const legacy = createHarness({ reader: reader3, clock });
  assert.equal((await legacy.context.runStream({}, () => {}, "/api/analyze/stream")).ok, true);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(groupFor(legacy.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(groupFor(legacy.logs, "[Paipu Performance]").length, 0);
  assert.equal(reader3.released, true);
});

test("same-origin and cross-origin IDs remain distinct, including hidden response headers", async () => {
  for (const scenario of [
    { url: "/api/analyze/stream", responseId: clientId, eventId: clientId, sendsHeader: true },
    { url: "/api/analyze/stream", responseId: serverId, eventId: serverId, sendsHeader: true },
    { url: "/api/analyze/stream", responseId: clientId, eventId: serverId, sendsHeader: true },
    { url: "/api/analyze/stream", responseId: clientId, eventId: "invalid-private-event", sendsHeader: true },
    { url: "https://backend.example/api/analyze/stream", responseId: serverId, eventId: serverId, sendsHeader: false },
    { url: "https://backend.example/api/analyze/stream", responseId: serverId, eventId: serverId, sendsHeader: false, headersVisible: false },
    { url: "https://backend.example/api/analyze/stream", responseId: "invalid-private-header", eventId: null, sendsHeader: false },
  ]) {
    const clock = { now: 0 };
    const payload = scenario.eventId ? { "请求ID": scenario.eventId, "总耗时_ms": 3 } : { "总耗时_ms": 3 };
    const reader = recordedReader([encoder.encode(complete + frame({ type: "performance", performance: payload }))], [1, 2], clock);
    const harness = createHarness({ reader, responseId: scenario.responseId, headersVisible: scenario.headersVisible, clock });
    const timings = {};
    await harness.context.runStream({}, () => {}, scenario.url, timings);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const sentId = harness.requests[0].options.headers["X-Request-ID"];
    assert.equal(sentId, scenario.sendsHeader ? clientId : undefined);
    const expectedServer = /^[0-9a-f]{32}$/.test(scenario.eventId || "") ? scenario.eventId
      : (/^[0-9a-f]{32}$/.test(scenario.responseId || "") && scenario.headersVisible !== false ? scenario.responseId : null);
    assert.equal(timings.requestId, clientId);
    assert.equal(timings.serverRequestId, expectedServer);
    assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
    assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0);
  }
});

test("raw JSON complete uses HTTP-level decoding without application compression negotiation", async () => {
  assert.doesNotMatch(apiCode, /DecompressionStream|\batob\b|gzip\+base64/);
  const expected = { text: "牌谱", nested: { values: [1, 2, 3] } };
  const rawComplete = frame({ type: "complete", result: expected });
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; c.enqueue(encoder.encode(progress + rawComplete)); } });
  const harness = createHarness({ reader: body.getReader(), responseId: clientId });
  const updates = [];
  const timings = {};
  const result = await harness.context.runStream({ paipu: "demo", accept_compression: "obsolete" },
    (percent) => updates.push(percent), "/api/analyze/stream", timings);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), expected);
  assert.deepEqual(updates, [99, 100]);
  assert.deepEqual(JSON.parse(harness.requests[0].options.body), { paipu: "demo" });
  assert.equal(harness.requests[0].options.headers.Accept, "text/event-stream");
  assert.ok(timings.sse.completeParsedAt >= timings.sse.completeEventParsedAt);
  assert.ok(timings.sse.completeJsonParseMs >= 0);
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0);
  controller.enqueue(encoder.encode(frame({ type: "performance", performance: { "事件": "牌谱性能统计", "请求ID": serverId } })));
  controller.close();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0);
  assert.equal(groupFor(harness.logs, "[Paipu Compression]").length, 0);
});

test("broken console diagnostics cannot alter the raw complete result", async () => {
  const clock = { now: 0 };
  const event = frame({ type: "performance", performance: { "请求ID": serverId } });
  const reader = recordedReader([encoder.encode(event + complete)], [1, 2], clock);
  const harness = createHarness({ reader, clock });
  let groupEnds = 0;
  harness.context.console.log = () => { throw new Error("console unavailable"); };
  harness.context.console.groupEnd = () => { groupEnds++; };
  const timings = {};
  const result = await harness.context.runStream({}, () => {}, "/api/analyze/stream", timings);
  assert.equal(result.text, "private-result");
  assert.equal(timings.serverRequestId, serverId);
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(reader.released, true);
});

test("review coordinator groups UX timings as an object, preserving late server ID", async () => {
  const code = reviewSource.slice(reviewSource.indexOf("let analysisViewVersion = 0;"), reviewSource.indexOf("function setPerspectiveLoading("));
  const capture = captureConsole();
  const frames = [];
  const context = {
    MODEL_REVISION: "revision", MODEL_SHA256: "hash", REVIEW_SCHEMA: "schema",
    getBackendUrl: () => "", buildApiUrl: (_backend, path) => path,
    requireModelStatus: async () => {}, isTrustedReview: () => true, requireTrustedReview() {},
    fetchReviewStream: async (_body, _notify, _url, timings) => {
      timings.requestId = clientId;
      timings.clientRequestId = clientId;
      timings.serverRequestId = null;
      timings.progress100ReceivedAt = 90;
      timings.completeReceivedAt = 100;
      timings.sse = { completeParsedAt: 112, completeJsonParseMs: 4 };
      return { target_actor: 0 };
    },
    requestAnimationFrame: (fn) => frames.push(fn),
    performance: { now: () => 145 },
    console: capture.console,
  };
  vm.createContext(context);
  vm.runInContext(code + "\nglobalThis.getReviewForTest = getReview; globalThis.logView = logReviewViewCompletion; globalThis.setVersion = value => { analysisViewVersion = value; };", context);
  const timings = {};
  const result = await context.getReviewForTest({ kind: "paipu", identity: "demo" }, 0, () => {}, timings);
  assert.equal(result.target_actor, 0);
  assert.equal(timings.progress100ReceivedAt, 90);
  assert.equal(timings.completeParsedAt, 112);
  context.setVersion(1);
  context.logView(timings, 1);
  timings.streamTimings.serverRequestId = serverId;
  frames.shift()();
  frames.shift()();
  // No UX performance logs in browser console
  assert.equal(capture.groups.length, 0);
  assert.equal(timings.pageSwitchDurationMs, 45);

  context.logView({ completeReceivedAt: 100 }, 1);
  context.setVersion(2);
  frames.shift()();
  frames.shift()();
  assert.equal(capture.groups.length, 0, "stale views do not log a completed transition");
});
