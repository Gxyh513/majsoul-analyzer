import { createSseByteMeter } from "../../shared/sse-metrics.js";

const UPSTREAM_ORIGIN = "https://gxyh513-majsoul-analyzer.ms.show";
const UPSTREAM_USER_AGENT = "majsoul-analyzer-pages/1.0";
const MAX_JSON_BODY_BYTES = 4 * 1024 * 1024;

const ROUTES = new Map([
  ["GET /api/status", { auth: false, stream: false, status: true }],
  ["POST /api/analyze/stream", { auth: true, stream: true }],
  ["POST /api/analyze/paipu", { auth: true, stream: false }],
  ["POST /api/analyze/json", { auth: true, stream: false }],
  ["POST /api/admin/login", { auth: true, stream: false, admin: true }],
  ["POST /api/admin/logout", { auth: true, stream: false, admin: true }],
  ["GET /api/admin/status", { auth: true, stream: false, admin: true }],
  ["GET /api/admin/accounts", { auth: true, stream: false, admin: true }],
  ["POST /api/admin/accounts", { auth: true, stream: false, admin: true }],
  ["POST /api/admin/accounts/import", { auth: true, stream: false, admin: true }],
]);

const ALLOWED_METHODS = new Map([
  ["/api/status", ["GET"]],
  ["/api/analyze/stream", ["POST"]],
  ["/api/analyze/paipu", ["POST"]],
  ["/api/analyze/json", ["POST"]],
  ["/api/admin/login", ["POST"]],
  ["/api/admin/logout", ["POST"]],
  ["/api/admin/status", ["GET"]],
  ["/api/admin/accounts", ["GET", "POST"]],
  ["/api/admin/accounts/import", ["POST"]],
]);

function jsonError(status, detail) {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

async function readBodyLimited(request) {
  const lengthHeader = request.headers.get("content-length");
  const contentLength = lengthHeader ? Number(lengthHeader) : NaN;
  if (Number.isFinite(contentLength) && contentLength > MAX_JSON_BODY_BYTES) {
    return { tooLarge: true };
  }

  if (!request.body) return { body: new Uint8Array() };
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_JSON_BODY_BYTES) {
        await reader.cancel();
        return { tooLarge: true };
      }
      chunks.push(value);
    }
  } catch (_) {
    return { error: true };
  }

  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { body };
}

function publicStatus(payload) {
  const engine = payload?.engine || {};
  return {
    ok: payload?.ok === true,
    engine: {
      has_mortal_weights: Boolean(engine.has_mortal_weights),
      active_engine: engine.active_engine || "unavailable",
      model_ready: engine.model_ready === true,
      model_name: typeof engine.model_name === "string" ? engine.model_name : "Mortal S42 ONNX",
      model_id: typeof engine.model_id === "string" ? engine.model_id : "mortal-s42-onnx",
      model_revision: typeof engine.model_revision === "string" ? engine.model_revision : null,
      model_tag: typeof engine.model_tag === "string" ? engine.model_tag : "",
      weight_sha256: typeof engine.weight_sha256 === "string" ? engine.weight_sha256 : null,
      review_schema: typeof engine.review_schema === "string" ? engine.review_schema : null,
      device: typeof engine.device === "string" ? engine.device : "N/A",
    },
  };
}

function acceptsGzip(header) {
  const encodings = new Map();
  for (const entry of (header || "").split(",")) {
    const [name, ...options] = entry.trim().toLowerCase().split(";");
    let quality = 1;
    for (const option of options) {
      const match = /^\s*q\s*=\s*(.+)\s*$/.exec(option);
      if (match) quality = Number(match[1]);
    }
    if (name.trim()) encodings.set(name.trim(), Number.isFinite(quality) && quality >= 0 && quality <= 1 ? quality : 0);
  }
  return (encodings.get("gzip") ?? encodings.get("*") ?? 0) > 0;
}

function safeResponseHeaders(upstream, isStream) {
  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  if (isStream && upstream.ok && /^text\/event-stream\b/i.test(contentType || "")) {
    headers.set("Content-Type", "text/event-stream; charset=utf-8");
    headers.set("Cache-Control", "no-cache, no-transform");
    headers.set("Content-Encoding", upstream.headers.get("content-encoding") || "identity");
    headers.set("Vary", "Accept-Encoding");
    headers.set("X-Accel-Buffering", "no");
  } else {
    headers.set("Cache-Control", "no-store");
  }

  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) headers.set("Retry-After", retryAfter);
  return headers;
}

function monitorSseStream(body, requestId, started, wallOrigin) {
  const reader = body.getReader();
  const meter = createSseByteMeter();
  let chunkIndex = 0;
  let totalBytes = 0;
  let samples = [];
  let progress = null;
  let cancelled = false;
  let released = false;

  function releaseReader() {
    if (!released) {
      released = true;
      reader.releaseLock();
    }
  }

  function flushSamples() {
    if (!samples.length) return;
    console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 块明细=序号,接收ms,转发ms,字节 ${JSON.stringify(samples)}`);
    samples = [];
  }

  return new ReadableStream({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        const arrivedAt = performance.now();
        if (cancelled) return;
        if (done) {
          flushSamples();
          console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 流结束 块数=${chunkIndex} 字节数=${totalBytes}`);
          releaseReader();
          controller.close();
          return;
        }
        chunkIndex++;
        totalBytes += value.byteLength;
        const events = meter.add(value, chunkIndex, arrivedAt);
        controller.enqueue(value);
        const forwardedAt = performance.now();
        meter.forwarded(chunkIndex, forwardedAt, events.finished);
        samples.push([chunkIndex, +(arrivedAt - started).toFixed(1), +(forwardedAt - started).toFixed(1), value.byteLength]);
        if (samples.length >= 128) flushSamples();
        for (const event of events.started) {
          if (event.kind === "100%") progress = event;
          const upstreamGap = progress && event.kind === "complete" ? event.firstAt - progress.firstAt : null;
          const downstreamGap = progress && event.kind === "complete" ? event.firstForwardAt - progress.firstForwardAt : null;
          console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 事件=${event.kind} 首块=${event.firstChunk} 上游首块=${(event.firstAt - started).toFixed(1)}ms 上游首块Unix=${(wallOrigin + event.firstAt).toFixed(1)}ms 下游首块入队=${(event.firstForwardAt - started).toFixed(1)}ms 上游事件首块间隔=${upstreamGap == null ? "N/A" : `${upstreamGap.toFixed(1)}ms`} 下游事件首块入队间隔=${downstreamGap == null ? "N/A" : `${downstreamGap.toFixed(1)}ms`}`);
        }
        for (const event of events.finished) {
          console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 事件=${event.kind} 字节数=${event.bytes} 末块=${event.lastChunk} 上游末块=${(event.lastAt - started).toFixed(1)}ms 下游末块入队=${(event.lastForwardAt - started).toFixed(1)}ms`);
        }
      } catch (error) {
        if (!cancelled) {
          flushSamples();
          console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 流中断 块数=${chunkIndex} 字节数=${totalBytes}`);
          controller.error(error);
          releaseReader();
        }
      }
    },
    async cancel() {
      cancelled = true;
      flushSamples();
      console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 下游取消 块数=${chunkIndex} 字节数=${totalBytes}`);
      try { await reader.cancel(); } finally { releaseReader(); }
    },
  }, { highWaterMark: 0 });
}

export async function onRequest({ request, env }) {
  const path = new URL(request.url).pathname;
  const accountPath = /^\/api\/admin\/accounts\/[1-9][0-9]*$/.test(path);
  const route = ROUTES.get(`${request.method} ${path}`) ||
    (accountPath && ["PATCH", "DELETE"].includes(request.method) ? { auth: true, stream: false, admin: true } : null);

  if (!route) {
    const methods = accountPath ? ["PATCH", "DELETE"] : ALLOWED_METHODS.get(path);
    if (methods) {
      return new Response(null, {
        status: 405,
        headers: { Allow: methods.join(", "), "Cache-Control": "no-store" },
      });
    }
    return jsonError(404, "接口不存在");
  }

  let token = "";
  if (route.auth) {
    token = typeof env?.MAJSOUL_API_TOKEN === "string" ? env.MAJSOUL_API_TOKEN.trim() : "";
    if (!token) return jsonError(503, "分析服务暂未完成配置，请稍后重试");
  }

  let body;
  if (request.method === "POST" || request.method === "PATCH") {
    const contentType = (request.headers.get("content-type") || "")
      .split(";")[0]
      .trim()
      .toLowerCase();
    if (contentType !== "application/json") return jsonError(415, "仅支持 JSON 请求体");

    const bodyResult = await readBodyLimited(request);
    if (bodyResult.tooLarge) return jsonError(413, "请求体超过 4 MiB 限制");
    if (bodyResult.error) return jsonError(400, "无法读取请求体");
    body = bodyResult.body;
  }

  const headers = new Headers({
    Accept: route.stream ? "text/event-stream" : "application/json",
    // Identify this proxy explicitly; do not forward a caller-controlled agent.
    "User-Agent": UPSTREAM_USER_AGENT,
    // Use only the configured public site origin expected by the backend allowlist.
    // Never reflect the caller-provided Origin or Referer.
    Origin: "https://majsoul.ccwu.cc",
    Referer: "https://majsoul.ccwu.cc/",
  });
  if (request.method === "POST" || request.method === "PATCH") headers.set("Content-Type", "application/json");
  if (token) headers.set("X-Api-Token", token);
  if (route.admin) {
    headers.set("Accept-Encoding", "identity");
    const adminKey = request.headers.get("X-Admin-Key") || "";
    if (adminKey) headers.set("X-Admin-Key", adminKey);
  }
  let requestId = "";
  if (route.stream) {
    const supplied = request.headers.get("X-Request-ID") || "";
    requestId = /^[0-9a-f]{32}$/.test(supplied) ? supplied : crypto.randomUUID().replaceAll("-", "");
    headers.set("X-Request-ID", requestId);
    const clientEncoding = request.cf?.clientAcceptEncoding ?? request.headers.get("Accept-Encoding");
    const debugProxy = request.headers.get("X-Paipu-Proxy-Debug") === "1";
    headers.set("Accept-Encoding", !debugProxy && acceptsGzip(clientEncoding) ? "gzip" : "identity");
  }

  // The path comes from the exact allowlist. Query strings, cookies, Origin,
  // client auth headers, and forwarding headers are never copied upstream.
  const upstreamUrl = new URL(path, UPSTREAM_ORIGIN);
  const started = performance.now();
  const wallOrigin = Date.now() - started;
  let upstream;
  try {
    upstream = await fetch(upstreamUrl.toString(), {
      method: request.method,
      headers,
      body: ["POST", "PATCH"].includes(request.method) ? body : undefined,
      signal: request.signal,
      redirect: "manual",
      cache: "no-store",
    });
  } catch (_) {
    return jsonError(502, "分析服务暂时不可用，请稍后重试");
  }

  if (route.stream) {
    const headersAt = performance.now();
    console.info(`[Paipu SSE Proxy] 请求ID=${requestId} 上游响应头到达=${(headersAt - started).toFixed(1)}ms 上游响应头Unix=${(wallOrigin + headersAt).toFixed(1)}ms 状态=${upstream.status} 相对时间基准=上游fetch开始 说明=Worker计时在计算期间可能冻结，Unix时间未经跨机校准`);
  }
  if (upstream.status >= 300 && upstream.status < 400) {
    try { await upstream.body?.cancel(); } catch (_) {}
    return jsonError(502, "分析服务返回了不支持的重定向");
  }
  if (upstream.status === 401 || upstream.status === 403) {
    if (route.admin) {
      // Admin endpoints have their own 401 responses (wrong admin key or
      // expired session). Preserve only known, non-sensitive diagnostics so
      // the Pages proxy does not mislabel them as an upstream API-token fault.
      let detail = "管理员密钥无效，请检查管理员密钥或服务鉴权配置";
      try {
        const payload = await upstream.json();
        const safeDetails = new Set([
          "管理员密钥错误",          "远程访问需要有效的 MAJSOUL_API_TOKEN",
          "来源不允许访问此服务",
        ]);
        if (safeDetails.has(payload?.detail)) detail = payload.detail;
      } catch (_) {}
      return jsonError(upstream.status, detail);
    }
    try { await upstream.body?.cancel(); } catch (_) {}
    return jsonError(502, "分析服务鉴权配置异常，请联系管理员");
  }

  if (route.status) {
    if (!upstream.ok) {
      try { await upstream.body?.cancel(); } catch (_) {}
      return jsonError(502, "分析服务状态暂不可用");
    }
    try {
      return new Response(JSON.stringify(publicStatus(await upstream.json())), {
        status: 200,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store",
        },
      });
    } catch (_) {
      return jsonError(502, "分析服务状态暂不可用");
    }
  }

  const responseHeaders = safeResponseHeaders(upstream, route.stream);
  if (route.admin) responseHeaders.set("Cache-Control", "no-store");
  const upstreamId = route.stream ? upstream.headers.get("X-Request-ID") : null;
  const serverRequestId = typeof upstreamId === "string" && /^[0-9a-f]{32}$/.test(upstreamId) ? upstreamId : null;
  if (serverRequestId) responseHeaders.set("X-Request-ID", serverRequestId);
  if (route.stream && serverRequestId && serverRequestId !== requestId) {
    console.info(`[Paipu SSE Proxy] 客户端请求ID=${requestId} 服务端请求ID=${serverRequestId}`);
  }
  const isSse = route.stream && upstream.ok && /^text\/event-stream\b/i.test(upstream.headers.get("content-type") || "");
  const encoding = (upstream.headers.get("content-encoding") || "identity").trim().toLowerCase();
  if (isSse && encoding !== "identity") {
    // Reading a compressed Workers fetch body may transparently decode it.
    // Keep the original stream unread so Content-Encoding remains accurate.
    console.info(`[Paipu SSE Proxy] 请求ID=${serverRequestId || requestId} 压缩流直通，代理逐帧统计不可用`);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  }
  return new Response(isSse && upstream.body ? monitorSseStream(upstream.body, serverRequestId || requestId, started, wallOrigin) : upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
