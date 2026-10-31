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

test("default grouped summary retains every read, frames, long tasks and redacted server metrics", async () => {
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
  harness = createHarness({ reader, responseId: clientId, clock, observer: LongTaskObserver });
  const updates = [];
  const timings = {};
  const result = await harness.context.runStream({ paipu: "input-never-logged" }, (...args) => updates.push(args), "/api/analyze/stream", timings);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { text: "private-result" });
  assert.deepEqual(updates.map(([percent]) => percent), [99, 100]);
  await waitFor(() => entryFor(harness.logs, "[Paipu SSE Browser]", "汇总").length === 1, "summary");

  const [summary] = entryFor(harness.logs, "[Paipu SSE Browser]", "汇总");
  const [reads] = entryFor(harness.logs, "[Paipu SSE Browser]", "读取明细");
  const [association] = entryFor(harness.logs, "[Paipu Performance]", "请求关联");
  const [backend] = entryFor(harness.logs, "[Paipu Performance]", "性能统计");
  assert.equal(summary.事件, "浏览器SSE性能统计");
  assert.equal(summary.版本, 1);
  assert.equal(summary.首分块接收时间_ms, 1);
  assert.equal(summary.末分块接收时间_ms, 210);
  assert.equal(summary.首末分块间隔_ms, 209);
  assert.match(summary.分块计时口径, /performance\.now/);
  assert.match(summary.数据口径, /自动解压/);
  assert.equal(summary.客户端请求ID, clientId);
  assert.equal(summary.服务端请求ID, clientId);
  assert.equal(association.客户端请求ID, clientId);
  assert.equal(association.服务端请求ID, clientId);
  assert.equal(harness.requests[0].options.headers["X-Request-ID"], clientId);
  assert.equal(timings.requestId, clientId);
  assert.equal(timings.serverRequestId, clientId);
  assert.equal(summary.读取次数, 21);
  assert.equal(summary.分块数, 20);
  assert.equal(summary.总数据大小_KB, kb(wire.byteLength));
  assert.equal(typeof summary.总数据大小_KB, "number");
  assert.equal(summary.读取等待总耗时_ms, 231);
  assert.equal(summary.读取等待最大耗时_ms, 21);
  assert.equal(summary.读取等待P95耗时_ms, 20);
  assert.equal(summary.读取错误次数, 0);
  assert.equal(summary.错误总次数, 0);
  assert.equal(summary.首分块.数据大小_KB, kb(chunks[0].byteLength));
  assert.equal(summary.末分块.数据大小_KB, kb(chunks.at(-1).byteLength));
  assert.deepEqual(Array.from(summary.可见状态.状态集合), ["可见", "隐藏"]);
  assert.equal(summary.可见状态.变化[0].状态, "隐藏");
  assert.equal(summary.长任务.次数, 1);
  assert.equal(summary.长任务.总耗时_ms, 55);
  assert.equal(summary.长任务.最大耗时_ms, 55);
  assert.equal(summary.长任务.明细[0].开始时间_ms, 2);
  assert.equal(summary.事件帧.进度100.数据大小_KB, kb(encoder.encode(progress).byteLength));
  assert.equal(summary.事件帧.complete.数据大小_KB, kb(encoder.encode(complete).byteLength));
  assert.equal(summary.事件帧.位于同一分块, summary.事件帧.进度100.末分块序号 === summary.事件帧.complete.首分块序号);
  assert.equal(summary.事件帧.complete事件JSON解析耗时_ms, 0);
  assert.equal(summary.结果传输.格式, "原始JSON SSE");
  assert.equal(summary.结果传输.压缩后数据大小_KB, null);
  assert.equal(summary.结果传输.Base64数据大小_KB, null);
  assert.equal(summary.结果传输.Base64解码耗时_ms, null);
  assert.equal(summary.结果传输.手动解压耗时_ms, null);
  assert.equal(summary.结果传输.手动解压后数据大小_KB, null);
  assert.equal(summary.结果传输.JSON解析耗时_ms, 0);
  assert.equal(summary.结果传输.事件JSON解析耗时_ms, 0);
  assert.equal(summary.尾部状态, "流结束");
  assert.equal(reads.length, 21);
  assert.equal(reads[0].序号, 1);
  assert.equal(reads[0].开始时间_ms, 0);
  assert.equal(reads[0].结束时间_ms, 1);
  assert.equal(reads[0].等待耗时_ms, 1);
  assert.equal(reads[0].数据大小_KB, kb(chunks[0].byteLength));
  assert.equal(reads[0].可见状态, "可见");
  assert.equal(reads[0].错误信息, null);
  assert.equal(reads[5].可见状态, "隐藏");
  assert.equal(reads.at(-1).状态, "流结束");
  assert.equal(reads.at(-1).数据大小_KB, 0);
  assert.equal("读取明细" in summary, false, "large read array is logged only once");
  assert.equal(entryFor(harness.logs, "[Paipu SSE Browser]", "读取明细").length, 1);
  assert.equal(backend.事件, "牌谱性能统计");
  assert.equal(backend.schema版本, 2);
  assert.equal(backend.请求ID, clientId);
  assert.equal(backend.耗时_ms.雀魂抓取, 12.5);
  assert.equal(backend.耗时_ms.原始牌谱获取总耗时, 4);
  assert.equal(backend.数据大小_KB.原始牌谱, 1.25);
  assert.equal(backend.计时口径.原始牌谱获取总耗时, "原始牌谱获取范围");
  assert.deepEqual(Array.from(backend.当前进行中阶段), ["雀魂抓取", "ONNX推理"]);
  assert.equal(backend.状态.password, "[已脱敏]");
  assert.equal(backend.状态.nested.authorization, "[已脱敏]");
  assert.equal(backend.状态.nested.note, "[已脱敏]");
  assert.equal(backend.状态.link, "[已脱敏]");
  assert.equal(backend.状态.review, "[已脱敏]");
  assert.equal("result" in backend, false);
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 1);
  assert.ok(harness.logs.every((group) => group.closed));
  assert.equal(harness.listenerCount(), 0);
  assert.equal(reader.released, true);
  const output = JSON.stringify(harness.logs);
  for (const secret of ["credential-value", "do-not-log-password", "do-not-log-auth", "private-result", "input-never-logged", "not-for-logs"]) {
    assert.equal(output.includes(secret), false, `leaked ${secret}`);
  }
});

test("all reads remain available without a debug switch and are not truncated", async () => {
  for (const debug of [false, true]) {
    const clock = { now: 0 };
    const count = 1200;
    const wire = encoder.encode(frame({ type: "complete", result: { text: "private-result".repeat(400) } }));
    const chunks = splitBytes(wire, count);
    const reader = recordedReader(chunks, [], clock);
    const harness = createHarness({ reader, debug, clock });
    const result = await harness.context.runStream({}, () => {}, "/api/analyze/stream");
    assert.equal(result.text.length, "private-result".length * 400);
    await waitFor(() => entryFor(harness.logs, "[Paipu SSE Browser]", "读取明细").length === 1);
    const [reads] = entryFor(harness.logs, "[Paipu SSE Browser]", "读取明细");
    const [summary] = entryFor(harness.logs, "[Paipu SSE Browser]", "汇总");
    assert.equal(reads.length, count + 1);
    assert.equal(summary.读取次数, count + 1);
    assert.equal(reads[0].序号, 1);
    assert.equal(reads[count - 1].序号, count);
    assert.equal(reads[count].序号, count + 1);
    assert.equal(reads[count].状态, "流结束");
    assert.equal(JSON.stringify(harness.logs).includes("private-result"), false);
  }
});

test("failed reads record safe error information without leaking the original error", async () => {
  const clock = { now: 0 };
  const failure = new TypeError("secret-read-error https://example.test/?token=private");
  const reader = recordedReader([encoder.encode('data: {"type":"progress","percent":42}\n\n'), failure], [7, 13], clock);
  const harness = createHarness({ reader, clock });
  await assert.rejects(harness.context.runStream({}, () => {}, "/api/analyze/stream"), (error) => error.receiveError === true);
  await waitFor(() => entryFor(harness.logs, "[Paipu SSE Browser]", "汇总").length === 1);
  const [summary] = entryFor(harness.logs, "[Paipu SSE Browser]", "汇总");
  const [reads] = entryFor(harness.logs, "[Paipu SSE Browser]", "读取明细");
  assert.equal(summary.读取次数, 2);
  assert.equal(summary.读取错误次数, 1);
  assert.equal(summary.错误总次数, 1);
  assert.equal(summary.读取等待总耗时_ms, 20);
  assert.equal(summary.读取等待最大耗时_ms, 13);
  assert.equal(summary.读取等待P95耗时_ms, 13);
  assert.deepEqual(Array.from(reads, (sample) => sample.等待耗时_ms), [7, 13]);
  assert.deepEqual(Array.from(reads, (sample) => sample.状态), ["数据分块", "读取失败"]);
  assert.equal(reads[1].开始时间_ms, 7);
  assert.equal(reads[1].结束时间_ms, 20);
  assert.equal(reads[1].数据大小_KB, 0);
  assert.equal(reads[1].可见状态, "可见");
  assert.match(reads[1].错误信息, /读取异常.*已脱敏/);
  assert.equal(reader.cancelled, true);
  assert.equal(reader.released, true);
  assert.equal(JSON.stringify(harness.logs).includes("secret-read-error"), false);
  assert.equal(JSON.stringify(harness.logs).includes("example.test"), false);

  const emptyReader = recordedReader([new Error("no data")], [5], clock);
  const empty = createHarness({ reader: emptyReader, clock });
  await assert.rejects(empty.context.runStream({}, () => {}, "/api/analyze/stream"));
  await waitFor(() => entryFor(empty.logs, "[Paipu SSE Browser]", "汇总").length === 1);
  const [emptySummary] = entryFor(empty.logs, "[Paipu SSE Browser]", "汇总");
  assert.equal(emptySummary.首分块接收时间_ms, null);
  assert.equal(emptySummary.末分块接收时间_ms, null);
  assert.equal(emptySummary.首末分块间隔_ms, null);
  assert.equal(emptySummary.事件帧.complete, null);
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
  await waitFor(() => groupFor(harness.logs, "[Paipu Performance]").length === 1, "delayed backend performance");
  assert.equal(groupFor(harness.logs, "[Paipu SSE Browser]").length, 0, "summary waits for stream cleanup, not the UI");
  assert.equal(timings.serverRequestId, serverId);
  controller.close();
  await waitFor(() => groupFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(entryFor(harness.logs, "[Paipu Performance]", "性能统计")[0].总耗时_ms, 123);
  assert.equal(entryFor(harness.logs, "[Paipu SSE Browser]", "汇总")[0].尾部状态, "流结束");
  assert.equal(entryFor(harness.logs, "[Paipu SSE Browser]", "汇总")[0].客户端请求ID, clientId);
  assert.equal(entryFor(harness.logs, "[Paipu SSE Browser]", "汇总")[0].服务端请求ID, serverId);
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
  await waitFor(() => groupFor(harness.logs, "[Paipu SSE Browser]").length === 1, "timeout summary");
  const [summary] = entryFor(harness.logs, "[Paipu SSE Browser]", "汇总");
  const [reads] = entryFor(harness.logs, "[Paipu SSE Browser]", "读取明细");
  assert.equal(summary.尾部状态, "超时");
  assert.equal(summary.诊断错误次数, 1);
  assert.equal(summary.错误总次数, 1);
  assert.equal(reads.length, summary.读取次数);
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
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 0, "same-chunk diagnostic was deferred");
  await waitFor(() => groupFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(groupFor(harness.logs, "[Paipu Performance]").length, 1);

  const bad = 'event: performance\ndata: {"type":"performance","performance":broken}\n\n';
  const trailingBusiness = 'data: {"type":"progress","percent":1}\n\ndata: {"type":"error","detail":"late"}\n\n';
  const reader2 = recordedReader([encoder.encode(bad + complete + trailingBusiness)], [1, 2], clock);
  const oldServer = createHarness({ reader: reader2, clock });
  const progressValues = [];
  assert.equal((await oldServer.context.runStream({}, (value) => progressValues.push(value), "/api/analyze/stream")).text, "private-result");
  await waitFor(() => groupFor(oldServer.logs, "[Paipu SSE Browser]").length === 1);
  assert.deepEqual(progressValues, [100]);
  assert.equal(groupFor(oldServer.logs, "[Paipu Performance]").length, 0);
  assert.equal(entryFor(oldServer.logs, "[Paipu SSE Browser]", "汇总")[0].诊断错误次数, 1);
  assert.equal(reader2.released, true);

  const unterminated = encoder.encode('data: {"type":"complete","result":{"ok":true}}');
  const reader3 = recordedReader([unterminated], [1, 2], clock);
  const legacy = createHarness({ reader: reader3, clock });
  assert.equal((await legacy.context.runStream({}, () => {}, "/api/analyze/stream")).ok, true);
  await waitFor(() => groupFor(legacy.logs, "[Paipu SSE Browser]").length === 1);
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
    await waitFor(() => groupFor(harness.logs, "[Paipu SSE Browser]").length === 1);
    const sentId = harness.requests[0].options.headers["X-Request-ID"];
    assert.equal(sentId, scenario.sendsHeader ? clientId : undefined);
    const expectedServer = /^[0-9a-f]{32}$/.test(scenario.eventId || "") ? scenario.eventId
      : (/^[0-9a-f]{32}$/.test(scenario.responseId || "") && scenario.headersVisible !== false ? scenario.responseId : null);
    const [summary] = entryFor(harness.logs, "[Paipu SSE Browser]", "汇总");
    const [association] = entryFor(harness.logs, "[Paipu Performance]", "请求关联");
    assert.equal(summary.客户端请求ID, clientId);
    assert.equal(summary.服务端请求ID, expectedServer);
    assert.equal(association.客户端请求ID, clientId);
    assert.equal(association.服务端请求ID, expectedServer);
    assert.equal(timings.requestId, clientId);
    assert.equal(timings.serverRequestId, expectedServer);
    assert.equal(JSON.stringify(harness.logs).includes("invalid-private-header"), false);
    assert.equal(JSON.stringify(harness.logs).includes("invalid-private-event"), false);
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
  await waitFor(() => groupFor(harness.logs, "[Paipu SSE Browser]").length === 1);
  assert.equal(entryFor(harness.logs, "[Paipu Performance]", "性能统计")[0].请求ID, serverId);
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
  await waitFor(() => groupEnds === 2, "diagnostic groups to close");
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
  const [ux] = entryFor(capture.groups, "[Paipu UX Performance]", "页面切换统计");
  assert.equal(capture.groups.length, 1);
  assert.equal(capture.groups[0].closed, true);
  assert.equal(ux.事件, "页面切换性能统计");
  assert.equal(ux.版本, 1);
  assert.equal(ux.客户端请求ID, clientId);
  assert.equal(ux.服务端请求ID, serverId);
  assert.equal(ux.进度100接收时间_ms, 90);
  assert.equal(ux.complete接收时间_ms, 100);
  assert.equal(ux.complete解析完成时间_ms, 112);
  assert.equal(ux.complete事件JSON解析耗时_ms, 4);
  assert.equal(ux.进度100至complete耗时_ms, 10);
  assert.equal(ux.complete至页面切换耗时_ms, 45);
  assert.equal(ux.进度100至页面切换耗时_ms, 55);
  assert.equal(ux.范围, "页面更新后的第二个动画帧");

  context.logView({ completeReceivedAt: 100 }, 1);
  context.setVersion(2);
  frames.shift()();
  frames.shift()();
  assert.equal(capture.groups.length, 1, "stale views do not log a completed transition");
});
