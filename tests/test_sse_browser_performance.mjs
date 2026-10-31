import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
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

function createHarness({ reader, debug = false, responseId = null, headersVisible = true,
  clock = { now: 0 }, observer = null, token = "credential-value", origin = "https://app.example", timers = {} } = {}) {
  const logs = [];
  const requests = [];
  const listeners = new Map();
  const document = {
    visibilityState: "visible",
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name, fn) { if (listeners.get(name) === fn) listeners.delete(name); },
  };
  const location = new URL(`${origin}/`);
  const context = {
    TextDecoder, Uint8Array, URL, Blob, Response, DecompressionStream, atob,
    setTimeout: timers.setTimeout || setTimeout, clearTimeout: timers.clearTimeout || clearTimeout,
    crypto: { randomUUID: () => uuid },
    performance: { timeOrigin: 1700000000000, now: () => clock.now },
    console: { info: (...args) => logs.push(args.join(" ")) },
    document,
    window: { location, __PAIPU_DEBUG_SSE__: debug },
    getBackendUrl: () => "", buildApiUrl: (_backend, path) => path,
    isPagesProxyMode: () => false, apiAuthHeaders: () => ({ "X-Api-Token": token }),
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, headers: { get: () => headersVisible ? responseId : null }, body: { getReader: () => reader } };
    },
  };
  if (observer) context.PerformanceObserver = observer;
  vm.createContext(context);
  vm.runInContext(apiCode + "\nglobalThis.runStream = fetchReviewStream;", context);
  return {
    context, logs, requests, clock, document,
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
      const value = chunks[current];
      if (value instanceof Error) throw value;
      return value ? { done: false, value } : { done: true };
    },
    async cancel() { cancelled = true; },
    releaseLock() { released = true; },
    get cancelled() { return cancelled; },
    get released() { return released; },
  };
}

function logsFor(logs, prefix) {
  return logs.filter((line) => line.startsWith(`${prefix} `)).map((line) => JSON.parse(line.slice(prefix.length + 1)));
}

async function waitFor(predicate, label = "expected event") {
  const deadline = Date.now() + 2000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

function splitBytes(bytes, count) {
  return Array.from({ length: count }, (_, index) =>
    bytes.slice(Math.floor(index * bytes.length / count), Math.floor((index + 1) * bytes.length / count)));
}

test("default summary measures every actual read and nearest-rank P95 without chunk records", async () => {
  const clock = { now: 0 };
  const performanceEvent = { type: "performance", performance: {
    request_id: clientId, "雀魂抓取": 12.5, password: "do-not-log-password",
    nested: { authorization: "Bearer do-not-log-auth", note: "credential-value" },
    link: "https://user:pass@example.test/?token=not-for-logs",
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
  harness = createHarness({ reader, responseId: clientId, clock, observer: LongTaskObserver });
  const updates = [];
  const timings = {};
  const result = await harness.context.runStream({ paipu: "input-never-logged" }, (...args) => updates.push(args), "/api/analyze/stream", timings);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { text: "private-result" });
  assert.deepEqual(updates.map(([percent]) => percent), [99, 100]);
  await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1, "summary");

  const [summary] = logsFor(harness.logs, "[Paipu SSE Browser]");
  const [backend] = logsFor(harness.logs, "[Paipu Performance]");
  assert.equal(summary.event, "paipu_sse_browser");
  assert.equal(summary.schema_version, 1);
  assert.equal(summary.first_chunk_ms, 1);
  assert.equal(summary.last_chunk_ms, 210);
  assert.equal(summary.first_to_last_ms, 209);
  assert.equal(summary.chunk_time_basis, "browser_performance_now_ms_at_read_completion");
  assert.equal(summary.client_request_id, clientId);
  assert.equal(summary.server_request_id, clientId);
  assert.equal(harness.requests[0].options.headers["X-Request-ID"], clientId);
  assert.equal(timings.requestId, clientId);
  assert.equal(timings.serverRequestId, clientId);
  assert.equal(summary.read_count, 21);
  assert.equal(summary.chunk_count, 20);
  assert.equal(summary.total_bytes, wire.byteLength);
  assert.equal(summary.read_wait_total_ms, 231);
  assert.equal(summary.read_wait_max_ms, 21);
  assert.equal(summary.read_wait_p95_ms, 20);
  assert.equal(summary.read_error_count, 0);
  assert.equal(summary.error_count, 0);
  assert.equal(summary.first_chunk.bytes, chunks[0].byteLength);
  assert.equal(summary.last_chunk.bytes, chunks.at(-1).byteLength);
  assert.deepEqual(summary.visibility.states, ["visible", "hidden"]);
  assert.equal(summary.visibility.changes[0].state, "hidden");
  assert.equal(summary.long_tasks.count, 1);
  assert.equal(summary.long_tasks.total_ms, 55);
  assert.equal(summary.long_tasks.max_ms, 55);
  assert.equal(summary.long_tasks.entries[0].start_ms, 2);
  assert.equal(summary.frames.progress100.bytes, encoder.encode(progress).byteLength);
  assert.equal(summary.frames.complete.bytes, encoder.encode(complete).byteLength);
  assert.equal(summary.frames.same_chunk, summary.frames.progress100.last_chunk === summary.frames.complete.first_chunk);
  assert.equal(summary.tail_status, "eof");
  assert.equal(summary.read_details_enabled, false);
  assert.equal("read_details" in summary, false);
  assert.equal(backend.performance["雀魂抓取"], 12.5);
  assert.equal(backend.performance.password, "[REDACTED]");
  assert.equal(backend.performance.nested.authorization, "[REDACTED]");
  assert.equal(backend.performance.nested.note, "[REDACTED]");
  assert.equal(backend.performance.link, "[REDACTED]");
  assert.equal(logsFor(harness.logs, "[Paipu Performance]").length, 1);
  assert.equal(harness.listenerCount(), 0);
  assert.equal(reader.released, true);
  for (const secret of ["credential-value", "do-not-log-password", "do-not-log-auth", "private-result", "input-never-logged", "not-for-logs"]) {
    assert.equal(harness.logs.join("\n").includes(secret), false, `leaked ${secret}`);
  }
});

test("debug records include completed and failed reads, but never the error text", async () => {
  const clock = { now: 0 };
  const reader = recordedReader([encoder.encode('data: {"type":"progress","percent":42}\n\n'), new Error("secret-read-error")], [7, 13], clock);
  const harness = createHarness({ reader, debug: true, clock });
  await assert.rejects(harness.context.runStream({}, () => {}, "/api/analyze/stream"), (error) => error.receiveError === true);
  await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  const [summary] = logsFor(harness.logs, "[Paipu SSE Browser]");
  assert.equal(summary.read_count, 2);
  assert.equal(summary.read_error_count, 1);
  assert.equal(summary.error_count, 1);
  assert.equal(summary.read_wait_total_ms, 20);
  assert.equal(summary.read_wait_max_ms, 13);
  assert.equal(summary.read_wait_p95_ms, 13);
  assert.equal(summary.read_details_enabled, true);
  assert.deepEqual(summary.read_details.map((sample) => sample.wait_ms), [7, 13]);
  assert.deepEqual(summary.read_details.map((sample) => sample.outcome), ["chunk", "error"]);
  assert.equal(reader.cancelled, true);
  assert.equal(reader.released, true);
  assert.equal(harness.logs.join("\n").includes("secret-read-error"), false);

  const emptyReader = recordedReader([new Error("no data")], [5], clock);
  const empty = createHarness({ reader: emptyReader, clock });
  await assert.rejects(empty.context.runStream({}, () => {}, "/api/analyze/stream"));
  await waitFor(() => logsFor(empty.logs, "[Paipu SSE Browser]").length === 1);
  const [emptySummary] = logsFor(empty.logs, "[Paipu SSE Browser]");
  assert.equal(emptySummary.first_chunk_ms, null);
  assert.equal(emptySummary.last_chunk_ms, null);
  assert.equal(emptySummary.first_to_last_ms, null);
});

test("complete is delivered before a delayed post-complete performance event and EOF", async () => {
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; c.enqueue(encoder.encode(progress + complete)); } });
  const harness = createHarness({ reader: body.getReader(), responseId: clientId });
  const updates = [];
  const result = await Promise.race([
    harness.context.runStream({}, (...args) => updates.push(args), "/api/analyze/stream"),
    new Promise((_, reject) => setTimeout(() => reject(new Error("completion waited for diagnostics")), 300)),
  ]);
  assert.equal(result.text, "private-result");
  assert.deepEqual(updates.map(([percent]) => percent), [99, 100]);
  assert.equal(logsFor(harness.logs, "[Paipu Performance]").length, 0);
  assert.equal(body.locked, true);
  const bytes = encoder.encode(frame({ type: "performance", performance: { request_id: clientId, "耗时": 123 } }).replaceAll("\n", "\r\n"));
  controller.enqueue(bytes.slice(0, 17));
  controller.enqueue(bytes.slice(17, bytes.length - 3));
  controller.enqueue(bytes.slice(bytes.length - 3));
  await waitFor(() => logsFor(harness.logs, "[Paipu Performance]").length === 1, "delayed backend performance");
  assert.equal(logsFor(harness.logs, "[Paipu SSE Browser]").length, 0, "summary waits for stream cleanup, not the UI");
  controller.close();
  await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(logsFor(harness.logs, "[Paipu Performance]")[0].performance["耗时"], 123);
  assert.equal(logsFor(harness.logs, "[Paipu SSE Browser]")[0].tail_status, "eof");
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
  const harness = createHarness({ reader: body.getReader(), timers });
  const result = await harness.context.runStream({}, () => {}, "/api/analyze/stream");
  assert.equal(result.text, "private-result");
  await waitFor(() => timeoutCallback, "tail timeout installation");
  assert.equal(body.locked, true);
  timeoutCallback();
  await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1, "timeout summary");
  const [summary] = logsFor(harness.logs, "[Paipu SSE Browser]");
  assert.equal(summary.tail_status, "timeout");
  assert.equal(summary.diagnostic_error_count, 1);
  assert.equal(summary.error_count, 1);
  assert.equal(body.locked, false);
  assert.equal(harness.listenerCount(), 0);
});

test("same-chunk, duplicate, malformed and absent performance events remain optional", async () => {
  const clock = { now: 0 };
  const event = frame({ type: "performance", performance: { metric: 42 } });
  const wire = encoder.encode(progress + complete + event + event);
  const reader = recordedReader([wire], [1, 2], clock);
  const harness = createHarness({ reader, clock });
  const updates = [];
  const result = await harness.context.runStream({}, (percent) => updates.push(percent), "/api/analyze/stream");
  assert.equal(result.text, "private-result");
  assert.deepEqual(updates, [99, 100]);
  assert.equal(logsFor(harness.logs, "[Paipu Performance]").length, 0, "same-chunk diagnostic was deferred");
  await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(logsFor(harness.logs, "[Paipu Performance]").length, 1);

  const bad = 'event: performance\ndata: {"type":"performance","performance":broken}\n\n';
  const trailingBusiness = 'data: {"type":"progress","percent":1}\n\ndata: {"type":"error","detail":"late"}\n\n';
  const reader2 = recordedReader([encoder.encode(bad + complete + trailingBusiness)], [1, 2], clock);
  const oldServer = createHarness({ reader: reader2, clock });
  const progressValues = [];
  assert.equal((await oldServer.context.runStream({}, (value) => progressValues.push(value), "/api/analyze/stream")).text, "private-result");
  await waitFor(() => logsFor(oldServer.logs, "[Paipu SSE Browser]").length === 1);
  assert.deepEqual(progressValues, [100]);
  assert.equal(logsFor(oldServer.logs, "[Paipu Performance]").length, 0);
  assert.equal(logsFor(oldServer.logs, "[Paipu SSE Browser]")[0].diagnostic_error_count, 1);
  assert.equal(reader2.released, true);

  const unterminated = encoder.encode('data: {"type":"complete","result":{"ok":true}}');
  const reader3 = recordedReader([unterminated], [1, 2], clock);
  const legacy = createHarness({ reader: reader3, clock });
  assert.equal((await legacy.context.runStream({}, () => {}, "/api/analyze/stream")).ok, true);
  await waitFor(() => logsFor(legacy.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(logsFor(legacy.logs, "[Paipu Performance]").length, 0);
  assert.equal(reader3.released, true);
});

test("same-origin and cross-origin IDs remain distinct, including hidden response headers", async () => {
  for (const scenario of [
    { url: "/api/analyze/stream", responseId: clientId, eventId: clientId, sendsHeader: true },
    { url: "/api/analyze/stream", responseId: serverId, eventId: serverId, sendsHeader: true },
    { url: "/api/analyze/stream", responseId: clientId, eventId: serverId, sendsHeader: true },
    { url: "https://backend.example/api/analyze/stream", responseId: serverId, eventId: serverId, sendsHeader: false },
    { url: "https://backend.example/api/analyze/stream", responseId: serverId, eventId: serverId, sendsHeader: false, headersVisible: false },
    { url: "https://backend.example/api/analyze/stream", responseId: "invalid-private-header", eventId: null, sendsHeader: false },
  ]) {
    const clock = { now: 0 };
    const payload = scenario.eventId ? { request_id: scenario.eventId, metric: 3 } : { metric: 3 };
    const reader = recordedReader([encoder.encode(complete + frame({ type: "performance", performance: payload }))], [1, 2], clock);
    const harness = createHarness({ reader, responseId: scenario.responseId, headersVisible: scenario.headersVisible, clock });
    const timings = {};
    await harness.context.runStream({}, () => {}, scenario.url, timings);
    await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1);
    const sentId = harness.requests[0].options.headers["X-Request-ID"];
    assert.equal(sentId, scenario.sendsHeader ? clientId : undefined);
    const expectedServer = scenario.eventId || (/^[0-9a-f]{32}$/.test(scenario.responseId || "") && scenario.headersVisible !== false
      ? scenario.responseId : null);
    const [summary] = logsFor(harness.logs, "[Paipu SSE Browser]");
    const [backend] = logsFor(harness.logs, "[Paipu Performance]");
    assert.equal(summary.client_request_id, clientId);
    assert.equal(summary.server_request_id, expectedServer);
    assert.equal(backend.client_request_id, clientId);
    assert.equal(backend.server_request_id, expectedServer);
    assert.equal(timings.requestId, clientId);
    assert.equal(timings.serverRequestId, expectedServer);
    assert.equal(harness.logs.join("\n").includes("invalid-private-header"), false);
  }
});

test("gzip negotiation restores the result before asynchronously reading performance", async () => {
  const expected = { text: "牌谱", nested: { values: [1, 2, 3] } };
  const data = gzipSync(Buffer.from(JSON.stringify(expected))).toString("base64");
  const completeCompressed = frame({ type: "complete", encoding: "gzip+base64", data });
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; c.enqueue(encoder.encode(progress + completeCompressed)); } });
  const harness = createHarness({ reader: body.getReader(), responseId: clientId });
  const updates = [];
  const result = await harness.context.runStream({}, (percent) => updates.push(percent), "/api/analyze/stream");
  assert.deepEqual(JSON.parse(JSON.stringify(result)), expected);
  assert.deepEqual(updates, [99, 100]);
  assert.equal(JSON.parse(harness.requests[0].options.body).accept_compression, "gzip+base64");
  assert.equal(logsFor(harness.logs, "[Paipu Performance]").length, 0);
  controller.enqueue(encoder.encode(frame({ type: "performance", performance: { compressed: true } })));
  controller.close();
  await waitFor(() => logsFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(logsFor(harness.logs, "[Paipu Performance]")[0].performance.compressed, true);
  assert.equal(logsFor(harness.logs, "[Paipu Compression]")[0].format, "gzip+base64");
});

test("review coordinator emits one UX JSON with progress, complete and second-frame timings", async () => {
  const code = reviewSource.slice(reviewSource.indexOf("let analysisViewVersion = 0;"), reviewSource.indexOf("function setPerspectiveLoading("));
  const logs = [];
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
      timings.sse = { completeParsedAt: 112 };
      return { target_actor: 0 };
    },
    requestAnimationFrame: (fn) => frames.push(fn),
    performance: { now: () => 145 },
    console: { info: (line) => logs.push(line) },
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
  const [ux] = logsFor(logs, "[Paipu UX Performance]");
  assert.equal(logs.length, 1);
  assert.equal(ux.event, "paipu_ux_performance");
  assert.equal(ux.schema_version, 1);
  assert.equal(ux.client_request_id, clientId);
  assert.equal(ux.server_request_id, serverId);
  assert.equal(ux.progress100_received_at_ms, 90);
  assert.equal(ux.complete_received_at_ms, 100);
  assert.equal(ux.complete_parsed_at_ms, 112);
  assert.equal(ux.progress100_to_complete_ms, 10);
  assert.equal(ux.complete_to_view_ms, 45);
  assert.equal(ux.progress100_to_view_ms, 55);
  assert.equal(ux.view_scope, "second_animation_frame_after_update");

  context.logView({ completeReceivedAt: 100 }, 1);
  context.setVersion(2);
  frames.shift()();
  frames.shift()();
  assert.equal(logs.length, 1, "stale views do not log a completed transition");
});
