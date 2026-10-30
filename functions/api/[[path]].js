const UPSTREAM_ORIGIN = "https://gxyh513-majsoul-analyzer.ms.show";
const UPSTREAM_USER_AGENT = "majsoul-analyzer-pages/1.0";
const MAX_JSON_BODY_BYTES = 4 * 1024 * 1024;

const ROUTES = new Map([
  ["GET /api/status", { auth: false, stream: false, status: true }],
  ["POST /api/analyze/stream", { auth: true, stream: true }],
  ["POST /api/analyze/paipu", { auth: true, stream: false }],
  ["POST /api/analyze/json", { auth: true, stream: false }],
]);

const ALLOWED_METHODS = new Map([
  ["/api/status", ["GET"]],
  ["/api/analyze/stream", ["POST"]],
  ["/api/analyze/paipu", ["POST"]],
  ["/api/analyze/json", ["POST"]],
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

function safeResponseHeaders(upstream, isStream) {
  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  if (isStream) {
    headers.set("Content-Type", "text/event-stream; charset=utf-8");
    headers.set("Cache-Control", "no-cache, no-transform");
    headers.set("X-Accel-Buffering", "no");
  } else {
    headers.set("Cache-Control", "no-store");
  }

  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) headers.set("Retry-After", retryAfter);
  return headers;
}

export async function onRequest({ request, env }) {
  const path = new URL(request.url).pathname;
  const route = ROUTES.get(`${request.method} ${path}`);

  if (!route) {
    const methods = ALLOWED_METHODS.get(path);
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
  if (request.method === "POST") {
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
  if (request.method === "POST") headers.set("Content-Type", "application/json");
  if (token) headers.set("X-Api-Token", token);

  // The path comes from the exact allowlist. Query strings, cookies, Origin,
  // client auth headers, and forwarding headers are never copied upstream.
  const upstreamUrl = new URL(path, UPSTREAM_ORIGIN);
  let upstream;
  try {
    upstream = await fetch(upstreamUrl.toString(), {
      method: request.method,
      headers,
      body: request.method === "POST" ? body : undefined,
      redirect: "manual",
      cache: "no-store",
    });
  } catch (_) {
    return jsonError(502, "分析服务暂时不可用，请稍后重试");
  }

  if (upstream.status >= 300 && upstream.status < 400) {
    try { await upstream.body?.cancel(); } catch (_) {}
    return jsonError(502, "分析服务返回了不支持的重定向");
  }
  if (upstream.status === 401 || upstream.status === 403) {
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

  // Keep the SSE body streaming; do not buffer or parse it in the proxy.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: safeResponseHeaders(upstream, route.stream),
  });
}
