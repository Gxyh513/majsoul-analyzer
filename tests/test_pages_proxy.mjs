import test from "node:test";
import assert from "node:assert/strict";
import { onRequest } from "../functions/api/[[path]].js";

const UPSTREAM = "https://gxyh513-majsoul-analyzer.ms.show";
const realFetch = globalThis.fetch;

test("status is public but sanitized and pinned to the upstream", async () => {
  let observed;
  globalThis.fetch = async (url, init) => {
    observed = { url: String(url), init };
    return new Response(JSON.stringify({
      ok: true,
      majsoul: { logged_in: true, account_pool_count: 9 },
      engine: {
        has_mortal_weights: true, model_ready: true, active_engine: "mortal", model_name: "Mortal S42 ONNX",
        model_id: "mortal-s42-onnx", model_revision: "2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a",
        model_tag: "v4", weight_sha256: "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4",
        review_schema: "mortal-onnx-2f7ad48-v1", device: "CPU", selected_model: "mortal-s42-onnx",
        model_path: "/private/model.onnx", models_dir: "/private/models", load_error: "private error",
        available_models: [{ id: "mortal-s42-onnx", name: "Mortal", path: "/private/model.onnx" }],
      },
    }), { status: 200, headers: { "Content-Type": "application/json", "Set-Cookie": "secret=hidden" } });
  };
  try {
    const req = new Request("https://pages.example/api/status?api_token=query-secret&studio_token=studio-secret", {
      headers: { "User-Agent": "untrusted-client-agent" },
    });
    const res = await onRequest({ request: req, env: {} });
    assert.equal(res.status, 200);
    assert.equal(observed.url, `${UPSTREAM}/api/status`);
    assert.equal(new Headers(observed.init.headers).has("X-Api-Token"), false);
    assert.equal(new Headers(observed.init.headers).get("User-Agent"), "majsoul-analyzer-pages/1.0");
    const body = await res.json();
    assert.equal(body.engine.review_schema, "mortal-onnx-2f7ad48-v1");
    assert.equal(body.engine.model_id, "mortal-s42-onnx");
    assert.equal(body.engine.model_revision, "2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a");
    assert.equal(body.engine.weight_sha256, "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4");
    assert.equal(body.engine.model_ready, true);
    assert.equal(body.engine.available_models, undefined);
    assert.equal(JSON.stringify(body).includes("/private"), false);
    assert.equal(JSON.stringify(body).includes("account_pool_count"), false);
    assert.equal(res.headers.get("set-cookie"), null);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("analysis injects only the server token and streams SSE without buffering", async () => {
  let observed;
  const chunks = [
    'data: {"type":"progress","percent":5}\n\n',
    'data: {"type":"complete","result":{},"cache_persisted":false}\n\n',
  ];
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    },
  });
  globalThis.fetch = async (url, init) => {
    observed = { url: String(url), init };
    return new Response(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Review-Cache-Persisted": "true",
        "Set-Cookie": "private=1",
        Connection: "keep-alive",
      },
    });
  };
  try {
    const req = new Request("https://pages.example/api/analyze/stream?api_token=client-query&studio_token=studio", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "X-Api-Token": "client-token",
        Authorization: "Bearer client",
        Cookie: "session=private",
        Origin: "https://evil.example",
        "User-Agent": "untrusted-client-agent",
        "X-Forwarded-For": "203.0.113.9",
      },
      body: JSON.stringify({ paipu: "demo" }),
    });
    const res = await onRequest({ request: req, env: { MAJSOUL_API_TOKEN: "server-only-secret" } });
    assert.equal(res.status, 200);
    assert.equal(observed.url, `${UPSTREAM}/api/analyze/stream`);
    assert.equal(observed.init.method, "POST");
    assert.equal(observed.init.redirect, "manual");
    const headers = new Headers(observed.init.headers);
    assert.equal(headers.get("X-Api-Token"), "server-only-secret");
    for (const name of ["Authorization", "Cookie", "X-Forwarded-For", "X-Studio-Token"]) {
      assert.equal(headers.has(name), false);
    }
    assert.equal(headers.get("Origin"), "https://majsoul.ccwu.cc");
    assert.equal(headers.get("Referer"), "https://majsoul.ccwu.cc/");
    assert.equal(headers.get("User-Agent"), "majsoul-analyzer-pages/1.0");
    assert.notEqual(headers.get("Origin"), "https://evil.example");
    assert.equal(new TextDecoder().decode(observed.init.body), JSON.stringify({ paipu: "demo" }));
    assert.equal(await res.text(), chunks.join(""));
    assert.equal(res.headers.get("content-type"), "text/event-stream; charset=utf-8");
    assert.equal(res.headers.get("cache-control"), "no-cache, no-transform");
    assert.equal(res.headers.get("x-accel-buffering"), "no");
    assert.equal(res.headers.get("x-review-cache-persisted"), null);
    assert.equal(res.headers.get("set-cookie"), null);
    assert.equal(res.headers.get("connection"), null);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("analysis omits the upstream cache persistence header", async () => {
  try {
    for (const path of ["paipu", "json"]) {
      for (const value of ["true", "false", "untrusted"]) {
        globalThis.fetch = async () => new Response(JSON.stringify({ summary: {} }), {
          status: 200,
          headers: {
            "Content-Type": "application/json", "X-Review-Cache-Persisted": value,
            "Set-Cookie": "secret=hidden", "Retry-After": "3",
          },
        });
        const request = new Request(`https://pages.example/api/analyze/${path}`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paipu: "demo" }),
        });
        const response = await onRequest({ request, env: { MAJSOUL_API_TOKEN: "server-only-secret" } });
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("x-review-cache-persisted"), null);
        assert.equal(response.headers.get("set-cookie"), null);
        assert.equal(response.headers.get("retry-after"), "3");
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.deepEqual(await response.json(), { summary: {} });
      }
    }
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("proxy rejects private routes, methods, missing secrets, and oversized bodies", async () => {
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls++;
    return new Response("{}", { status: 200 });
  };
  try {
    const makeRequest = (path, method = "GET", body, contentType = "application/json") => {
      const init = { method };
      if (body !== undefined) {
        init.body = body;
        init.headers = { "Content-Type": contentType };
      }
      return new Request(`https://pages.example${path}`, init);
    };
    for (const [path, method] of [
      ["/api/records/local", "GET"],
      ["/api/engine/config", "POST"],
      ["/api/login", "POST"],
      ["/api/admin/cleanup_cache", "POST"],
    ]) {
      const body = method === "POST" ? "{}" : undefined;
      const res = await onRequest({ request: makeRequest(path, method, body), env: { MAJSOUL_API_TOKEN: "server-token" } });
      assert.equal(res.status, 404);
    }
    assert.equal((await onRequest({ request: makeRequest("/api/analyze/json"), env: { MAJSOUL_API_TOKEN: "server-token" } })).status, 405);
    assert.equal((await onRequest({ request: makeRequest("/api/analyze/json", "POST", "{}"), env: {} })).status, 503);
    assert.equal((await onRequest({ request: makeRequest("/api/analyze/json", "POST", "{}", "text/plain"), env: { MAJSOUL_API_TOKEN: "server-token" } })).status, 415);
    const tooLarge = new Uint8Array(4 * 1024 * 1024 + 1);
    const largeReq = makeRequest("/api/analyze/json", "POST", tooLarge);
    assert.equal((await onRequest({ request: largeReq, env: { MAJSOUL_API_TOKEN: "server-token" } })).status, 413);
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("proxy rejects redirects and masks upstream authentication errors", async () => {
  const makeRequest = () => new Request("https://pages.example/api/analyze/paipu", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  globalThis.fetch = async () => new Response(null, {
    status: 302,
    headers: { Location: "https://attacker.example/", "Set-Cookie": "private=1" },
  });
  try {
    let res = await onRequest({ request: makeRequest(), env: { MAJSOUL_API_TOKEN: "server-only-secret" } });
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("location"), null);
    assert.equal((await res.text()).includes("server-only-secret"), false);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "remote token rejected" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
    res = await onRequest({ request: makeRequest(), env: { MAJSOUL_API_TOKEN: "server-only-secret" } });
    assert.equal(res.status, 502);
    const text = await res.text();
    assert.equal(text.includes("remote token rejected"), false);
    assert.equal(text.includes("server-only-secret"), false);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("admin auth errors preserve safe diagnostics while analysis auth errors stay masked", async () => {
  const makeAdminRequest = (path = "/api/admin/login") => new Request(`https://pages.example${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: "wrong-key" }),
  });
  const cases = [
    [401, "管理员密钥错误"],
    [401, "管理员未登录或登录已过期"],
    [401, "远程访问需要有效的 MAJSOUL_API_TOKEN"],
    [403, "来源不允许访问此服务"],
  ];
  try {
    for (const [status, detail] of cases) {
      globalThis.fetch = async () => new Response(JSON.stringify({ detail }), {
        status, headers: { "Content-Type": "application/json" },
      });
      const response = await onRequest({
        request: makeAdminRequest(),
        env: { MAJSOUL_API_TOKEN: "server-only-secret" },
      });
      assert.equal(response.status, status);
      assert.deepEqual(await response.json(), { detail });
    }

    globalThis.fetch = async () => new Response(JSON.stringify({
      detail: "管理员请求未通过鉴权，请检查管理员密钥、登录状态或服务鉴权配置",
    }), { status: 401, headers: { "Content-Type": "application/json" } });
    const unknown = await onRequest({
      request: makeAdminRequest(),
      env: { MAJSOUL_API_TOKEN: "server-only-secret" },
    });
    assert.equal(unknown.status, 401);
    assert.equal((await unknown.json()).detail.includes("server-only-secret"), false);

    globalThis.fetch = async () => new Response(JSON.stringify({
      detail: "请先在魔搭环境变量设置 MAJSOUL_ADMIN_KEY",
    }), { status: 503, headers: { "Content-Type": "application/json" } });
    const missingAdminKey = await onRequest({
      request: makeAdminRequest(),
      env: { MAJSOUL_API_TOKEN: "server-only-secret" },
    });
    assert.equal(missingAdminKey.status, 503);
    assert.equal((await missingAdminKey.json()).detail, "请先在魔搭环境变量设置 MAJSOUL_ADMIN_KEY");
  } finally {
    globalThis.fetch = realFetch;
  }
});
