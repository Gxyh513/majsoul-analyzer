let currentAnalysisRetryCb = null;

function updateAnalysisProgress(percent, message) {
  const fill = $("analysisProgressFill");
  const pctEl = $("analysisProgressPct");
  const textEl = $("analysisProgressText");
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  if (fill) fill.style.width = `${p}%`;
  if (pctEl) pctEl.textContent = `${p}%`;
  if (textEl && message) textEl.textContent = message;
}

function setLoading(visible, title = "AI 牌谱智能检讨", sub = null) {
  const overlay = $("loadingOverlay");
  if (!overlay) return;
  overlay.style.pointerEvents = "";
  overlay.style.background = "";
  overlay.style.backdropFilter = "";
  overlay.style.webkitBackdropFilter = "";
  overlay.style.alignItems = "";
  overlay.style.justifyContent = "";
  if (visible) {
    const normalState = $("analysisNormalState");
    const errorBox = $("analysisErrorBox");
    if (normalState) normalState.classList.remove("hidden");
    if (errorBox) errorBox.classList.add("hidden");
    if ($("loadingTitle")) $("loadingTitle").textContent = title;

    let effectiveSub = sub;
    if (!effectiveSub) {
      effectiveSub = "Mortal S42 ONNX 神经网络正在检讨每一手决策";
    }
    if ($("loadingSub")) $("loadingSub").textContent = effectiveSub;
    updateAnalysisProgress(0, "正在连接检讨引擎...");
    overlay.classList.remove("hidden");
  } else {
    overlay.classList.add("hidden");
  }
}

function showAnalysisError(errMsg, retryCallback = null) {
  currentAnalysisRetryCb = retryCallback;
  const overlay = $("loadingOverlay");
  const normalState = $("analysisNormalState");
  const errorBox = $("analysisErrorBox");
  const errorDetail = $("analysisErrorDetail");
  if (!overlay || !errorBox) return;

  if (normalState) normalState.classList.add("hidden");
  if (!isPagesProxyMode() && errMsg && (errMsg.includes("MAJSOUL_API_TOKEN") || errMsg.includes("401"))) {
    if (errorDetail) {
      errorDetail.innerHTML = `
        <div style="margin-bottom: 10px; font-weight: 500; color: #ffffff;">本服务器已启用远程访问口令保护，请输入口令：</div>
        <div style="display: flex; gap: 8px; justify-content: center; max-width: 400px; margin: 0 auto 10px;">
          <input id="promptApiTokenInput" type="password" placeholder="请输入 MAJSOUL_API_TOKEN" class="kd-landing-input" style="padding: 8px 12px; font-size: 13px; text-align: center; border-radius: 8px;" value="${escapeHtml(getApiToken())}" />
          <button id="btnSavePromptToken" type="button" class="kd-retry-btn" style="white-space: nowrap; padding: 8px 14px; border-radius: 8px;">保存并继续</button>
        </div>
        <div style="font-size: 11.5px; color: rgba(235, 235, 245, 0.45);">口令仅保存在当前浏览器标签页，关闭标签页后需要重新输入。</div>
      `;
      setTimeout(() => {
        $("btnSavePromptToken")?.addEventListener("click", () => {
          const val = $("promptApiTokenInput")?.value || "";
          setApiToken(val);
          showToast("访问口令已保存", "success");
          if (currentAnalysisRetryCb) {
            errorBox.classList.add("hidden");
            currentAnalysisRetryCb();
          }
        });
        $("promptApiTokenInput")?.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            $("btnSavePromptToken")?.click();
          }
        });
      }, 50);
    }
  } else {
    if (errorDetail) errorDetail.textContent = errMsg || "检讨服务暂时不可用，请检查网络或重试。";
  }
  errorBox.classList.remove("hidden");
  overlay.classList.remove("hidden");
}

// The public Cloudflare deployment uses a same-origin Pages Function proxy.
// Never let public Pages URLs override the fixed server-side upstream.
function isPagesProxyMode() {
  const host = (window.location.hostname || "").toLowerCase();
  return host === "majsoul.ccwu.cc" || host === "majsoul-analyzer.pages.dev" || host.endsWith(".majsoul-analyzer.pages.dev");
}

// Remove legacy client credentials from public Pages sessions without reading
// or exposing their values. The proxy injects its Secret on the server side.
function clearPagesProxyCredentials() {
  clearLegacyApiTokens();
  try {
    const keys = [];
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key && key.startsWith("majsoul_api_token:")) keys.push(key);
    }
    for (const key of keys) sessionStorage.removeItem(key);
  } catch (_) {}
  try {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (fragment.has("api_token")) {
      fragment.delete("api_token");
      const url = new URL(window.location.href);
      url.hash = fragment.toString();
      window.history.replaceState(null, "", url.toString());
    }
  } catch (_) {}
}

// API tokens are scoped to the selected direct backend and retained only in this tab.
function tokenStorageKey() {
  const backend = getBackendUrl();
  const origin = backend ? new URL(backend.startsWith("http") ? backend : `http://${backend}`).origin : window.location.origin;
  return `majsoul_api_token:${origin}`;
}

function clearLegacyApiTokens() {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key && key.startsWith("majsoul_api_token:")) localStorage.removeItem(key);
    }
  } catch (_) {}
}

function getApiToken() {
  if (isPagesProxyMode()) {
    clearPagesProxyCredentials();
    return "";
  }
  clearLegacyApiTokens();
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("api_token");
  const key = tokenStorageKey();
  if (token) {
    fragment.delete("api_token");
    const url = new URL(window.location.href);
    url.hash = fragment.toString();
    window.history.replaceState(null, "", url.toString());
    sessionStorage.setItem(key, token.trim());
  }
  return sessionStorage.getItem(key) || "";
}

function setApiToken(token) {
  if (isPagesProxyMode()) {
    clearPagesProxyCredentials();
    return;
  }
  clearLegacyApiTokens();
  const key = tokenStorageKey();
  if (token && token.trim()) sessionStorage.setItem(key, token.trim());
  else sessionStorage.removeItem(key);
}

function apiAuthHeaders() {
  if (isPagesProxyMode()) {
    clearPagesProxyCredentials();
    return {};
  }
  const token = getApiToken();
  // ModelScope may consume Authorization for its own gateway credentials.
  return token ? { "X-Api-Token": token } : {};
}

async function fetchReviewStream(postBody, onProgress) {
  const serverError = (message) => Object.assign(new Error(message), { serverReported: true });
  const url = buildApiUrl(getBackendUrl(), "/api/analyze/stream");
  const resp = await fetch(url, {
    method: "POST",
    credentials: isPagesProxyMode() ? "omit" : "same-origin",
    redirect: "error",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...apiAuthHeaders(),
    },
    body: JSON.stringify(postBody),
  });

  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${errText || resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalResult = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep last incomplete line in buffer

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const jsonStr = trimmed.slice(5).trim();
      if (!jsonStr) continue;
      try {
        const ev = JSON.parse(jsonStr);
        if (ev.type === "progress") {
          onProgress(ev.percent || 0, ev.message || "");
        } else if (ev.type === "queued") {
          onProgress(5, ev.message || "服务器正忙，当前正在排队中...");
        } else if (ev.type === "complete") {
          finalResult = ev.result;
        } else if (ev.type === "error") {
          throw serverError(ev.detail || "分析发生错误");
        }
      } catch (err) {
        if (err.serverReported || (err.message && !err.message.includes("Unexpected token") && !err.message.includes("JSON"))) {
          throw err;
        }
      }
    }
  }

  if (buffer.trim().startsWith("data:")) {
    try {
      const ev = JSON.parse(buffer.trim().slice(5).trim());
      if (ev.type === "complete") {
        finalResult = ev.result;
      } else if (ev.type === "error") throw serverError(ev.detail || "分析发生错误");
    } catch (e) {
      if (e.serverReported || (e.message && !e.message.includes("Unexpected token") && !e.message.includes("JSON"))) {
        throw e;
      }
    }
  }

  if (!finalResult) {
    throw new Error("服务端未正常返回分析结果");
  }
  return finalResult;
}

const DEFAULT_MODELSCOPE_BACKEND = "https://gxyh513-majsoul-analyzer.ms.show";

function getBackendUrl() {
  if (isPagesProxyMode()) {
    clearPagesProxyCredentials();
    return "";
  }
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has("backend")) {
    const b = urlParams.get("backend").trim().replace(/\/+$/, "");
    localStorage.setItem("majsoul_backend_url", b);
    return b;
  }
  const saved = localStorage.getItem("majsoul_backend_url");
  if (saved !== null && saved !== undefined && saved !== "") return saved.trim().replace(/\/+$/, "");

  if (window.location.hostname.endsWith("pages.dev") || window.location.hostname.includes("ccwu.cc")) {
    return DEFAULT_MODELSCOPE_BACKEND;
  }
  return "";
}

function buildApiUrl(backend, path) {
  if (isPagesProxyMode()) {
    if (/^https?:\/\//i.test(path)) throw new Error("公开 Pages 只能使用同源 API 代理。");
    return path.startsWith("/") ? path : `/${path}`;
  }
  if (!backend && !window.location.search.includes("studio_token")) return path;
  try {
    const currentSearch = new URLSearchParams(window.location.search);
    const studioToken = currentSearch.get("studio_token");

    if (!backend) {
      if (!studioToken) return path;
      const u = new URL(path, window.location.origin);
      u.searchParams.set("studio_token", studioToken);
      return u.toString();
    }

    const base = new URL(backend.startsWith("http") ? backend : `http://${backend}`);
    const baseParams = new URLSearchParams(base.search);
    base.search = "";
    base.hash = "";
    const full = new URL(path.replace(/^\//, ""), base.href.endsWith("/") ? base.href : base.href + "/");
    for (const [k, v] of baseParams.entries()) {
      if (k !== "api_token" && !full.searchParams.has(k)) full.searchParams.set(k, v);
    }
    if (studioToken && !full.searchParams.has("studio_token")) {
      full.searchParams.set("studio_token", studioToken);
    }
    full.searchParams.delete("api_token");
    return full.toString();
  } catch (e) {
    throw new Error("后端地址无效，请检查配置。");
  }
}

async function apiFetch(path, options = {}) {
  const proxyMode = isPagesProxyMode();
  const isAbs = /^https?:\/\//i.test(path);
  const backend = getBackendUrl();
  const url = isAbs ? path : buildApiUrl(backend, path);
  if (proxyMode && isAbs && new URL(url).origin !== window.location.origin) {
    throw new Error("公开 Pages 只能使用同源 API 代理。");
  }

  const optionHeaders = {};
  if (options.headers instanceof Headers) {
    options.headers.forEach((value, key) => { optionHeaders[key] = value; });
  } else {
    Object.assign(optionHeaders, options.headers || {});
  }
  if (proxyMode) {
    for (const key of Object.keys(optionHeaders)) {
      if (["authorization", "x-api-token", "cookie", "x-studio-token"].includes(key.toLowerCase())) {
        delete optionHeaders[key];
      }
    }
  }

  const headers = {
    Accept: "application/json",
    ...(!proxyMode && new URL(url, window.location.origin).origin === new URL(backend ? (backend.startsWith("http") ? backend : `http://${backend}`) : window.location.origin).origin ? apiAuthHeaders() : {}),
    ...optionHeaders,
  };
  if (options.body && !Object.keys(headers).some((key) => key.toLowerCase() === "content-type")) {
    headers["Content-Type"] = "application/json";
  }

  const fetchOptions = {
    mode: "cors",
    ...options,
    redirect: "error",
    headers,
  };
  if (proxyMode) fetchOptions.credentials = "omit";
  const resp = await fetch(url, fetchOptions);

  if (!resp.ok) {
    let detail = `HTTP ${resp.status} ${resp.statusText}`;
    try {
      const errJson = await resp.json();
      detail = errJson.detail || errJson.error || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return await resp.json();
}

function isBackendOnline() {
  return Boolean(state.serverStatus && (state.serverStatus.ok || state.serverStatus.engine));
}

async function refreshServerStatus() {
  try {
    const st = await apiFetch("/api/status");
    state.serverStatus = { ok: true, ...st };
    updateTopbarStatus(state.serverStatus);
  } catch (err) {
    console.warn("Backend offline or unreachable:", err);
    state.serverStatus = { ok: false, offline: true };
    updateTopbarStatus(state.serverStatus);
  }
}

function updateEngineStatusDisplay() {
  const eng = state.serverStatus?.engine || {};
  const ready = eng.has_mortal_weights === true && eng.active_engine === "mortal";
  const device = typeof eng.device === "string" && eng.device ? eng.device : "未知";
  const label = `${ready ? "READY" : "NOT READY"} · ${device}`;
  const topbar = $("engineStatusText");
  if (topbar) topbar.textContent = `Mortal S42 ONNX · ${label}`;
  const landing = $("landingModelDevice");
  if (landing) landing.textContent = label;
  const badge = landing?.parentElement;
  if (badge) {
    badge.style.color = ready ? "#30d158" : "#ff9f0a";
    const dot = badge.querySelector(".kd-single-model-dot");
    if (dot) dot.style.background = ready ? "#30d158" : "#ff9f0a";
  }
}

function updateTopbarStatus(st) {
  const eng = st.engine || {};
  const ready = eng.has_mortal_weights === true && eng.active_engine === "mortal";
  const value = (v) => escapeHtml(typeof v === "string" && v ? v : "未知");
  updateEngineStatusDisplay();
  const detail = $("engineModalDetail");
  if (detail) {
    detail.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:6px; overflow-wrap:anywhere;">
        <div>模型：<strong>Mortal S42 ONNX</strong></div>
        <div>状态：<strong>${ready ? "READY" : "NOT READY"}</strong></div>
        <div>模型 revision：<code>${value(eng.model_revision)}</code></div>
        <div>权重 SHA-256：<code>${value(eng.weight_sha256)}</code></div>
        <div>推理设备：<span>${value(eng.device)}</span></div>
      </div>`;
  }
  const retry = $("btnRetryModelDownload");
  if (retry) {
    const downloading = (eng.model_download || st.model_download)?.status === "downloading";
    retry.classList.toggle("hidden", ready || isPagesProxyMode() || st.offline);
    retry.disabled = downloading;
    retry.textContent = downloading ? "模型下载中" : "重试下载模型";
  }
}

function parsePaipuUuid(raw) {
  if (!raw) return "";
  const trimmed = raw.trim();
  const urlMatch = trimmed.match(/[?&]paipu=([0-9a-zA-Z\-_]+)/);
  if (urlMatch) return urlMatch[1];
  const directMatch = trimmed.match(/^([0-9]{6}-[0-9a-fA-F\-_]+)/);
  if (directMatch) return directMatch[1];
  return trimmed;
}
