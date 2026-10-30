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

async function fetchReviewStream(postBody, onProgress, url = buildApiUrl(getBackendUrl(), "/api/analyze/stream"), timings = {}) {
  const receiveError = (message) => Object.assign(new Error(message), { receiveError: true });
  let supportsCompression = false;
  try {
    if (typeof DecompressionStream === "function" && typeof Blob === "function" &&
        typeof Blob.prototype.stream === "function" && typeof Response === "function" &&
        typeof Response.prototype.arrayBuffer === "function" && typeof atob === "function") {
      new DecompressionStream("gzip");
      supportsCompression = true;
    }
  } catch (_) {}
  const clientRequestId = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll("-", "")
    : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  let serverRequestId = null;
  // requestId remains the local ID for existing callers; it is not evidence of
  // a server-side match when the cross-origin request could not send the header.
  timings.requestId = clientRequestId;
  timings.clientRequestId = clientRequestId;
  timings.serverRequestId = null;
  const debugReads = typeof window !== "undefined" && window.__PAIPU_DEBUG_SSE__ === true;
  const visibility = () => {
    const value = typeof document === "undefined" ? "unknown" : document.visibilityState;
    return ["visible", "hidden", "prerender", "unloaded"].includes(value) ? value : "unknown";
  };
  const initialVisibility = visibility();
  const wallOrigin = performance.timeOrigin || (Date.now() - performance.now());
  const monitorStarted = performance.now();
  let monitorEnded = Infinity;
  let monitorFinished = false;
  let streamFinished = false;
  let summaryLogged = false;
  let observer = null;
  let longTaskMonitoring = false;
  const readSamples = debugReads ? [] : null;
  const readWaits = [];
  let readCount = 0;
  let readErrorCount = 0;
  let errorCount = 0;
  let diagnosticErrorCount = 0;
  let chunkCount = 0;
  let totalBytes = 0;
  let firstChunk = null;
  let lastChunk = null;
  let firstChunkAt = null;
  let lastChunkAt = null;
  let previousReadFinished = null;
  let completeReadWaitMs = 0;
  let completeBetweenReadsMs = 0;
  let longTaskCount = 0;
  let longTaskMs = 0;
  let longestTaskMs = 0;
  const longTasks = [];
  const visibilityStates = new Set([initialVisibility]);
  const visibilityChanges = [];
  let progressFrame = null;
  let completeFrame = null;
  let frame = null;
  let lineHasContent = false;
  let tailStatus = "not_started";
  let performanceLogged = false;
  const sensitiveValues = [];
  const sensitiveKey = /token|secret|password|passwd|authorization|cookie|credential|api[-_]?key/i;
  const sensitiveUrlKey = /(?:^|[_-])(key|signature|sig|auth|credential|session)(?:$|[_-])|token|secret|password/i;
  const roundMs = (value) => Number.isFinite(value) ? Math.round(value * 1000) / 1000 : null;
  const validRequestId = (value) => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);

  function safeLog(prefix, payload) {
    try {
      const json = JSON.stringify(payload, (key, value) => {
        if (sensitiveKey.test(key)) return "[REDACTED]";
        if (typeof value === "string" && (sensitiveValues.some((secret) => value.includes(secret)) ||
            /https?:\/\/|\b(?:bearer|basic)\s+\S+|(?:token|secret|password|authorization|cookie|api[-_]?key)\s*[:=]\s*\S+/i.test(value))) {
          return "[REDACTED]";
        }
        return value;
      });
      console.info(`${prefix} ${json}`);
    } catch (_) {
      // Diagnostics must never prevent displaying a completed review.
    }
  }
  function recordLongTasks(entries) {
    for (const entry of entries) {
      if (entry.startTime > monitorEnded || entry.startTime + entry.duration < monitorStarted) continue;
      longTaskCount++;
      longTaskMs += entry.duration;
      longestTaskMs = Math.max(longestTaskMs, entry.duration);
      longTasks.push({ start_ms: roundMs(entry.startTime), duration_ms: roundMs(entry.duration), visibility: visibility() });
    }
  }
  function visibilityChanged() {
    const state = visibility();
    visibilityStates.add(state);
    visibilityChanges.push({ state, at_ms: roundMs(performance.now()) });
  }
  function frameSummary(item) {
    return item ? { bytes: item.bytes, first_chunk: item.firstChunk, last_chunk: item.lastChunk,
      first_at_ms: roundMs(item.firstAt), last_at_ms: roundMs(item.lastAt),
      first_at_unix_ms: roundMs(wallOrigin + item.firstAt) } : null;
  }
  function reportDiagnostics() {
    if (!monitorFinished || !streamFinished || summaryLogged) return;
    summaryLogged = true;
    const sortedWaits = [...readWaits].sort((a, b) => a - b);
    const p95 = sortedWaits.length ? sortedWaits[Math.ceil(sortedWaits.length * 0.95) - 1] : null;
    const sse = timings.sse || {};
    timings.diagnostics = { longTaskCount, longTaskMs, longestTaskMs, completeReadWaitMs, completeBetweenReadsMs,
      visibilityStates: [...visibilityStates] };
    const summary = {
      event: "paipu_sse_browser", schema_version: 1,
      client_request_id: clientRequestId, server_request_id: serverRequestId,
      read_count: readCount, chunk_count: chunkCount, total_bytes: totalBytes,
      first_chunk_ms: firstChunk?.received_at_ms ?? null, last_chunk_ms: lastChunk?.received_at_ms ?? null,
      first_to_last_ms: firstChunkAt !== null && lastChunkAt !== null ? roundMs(lastChunkAt - firstChunkAt) : null,
      chunk_time_basis: "browser_performance_now_ms_at_read_completion",
      read_wait_total_ms: roundMs(readWaits.reduce((sum, wait) => sum + wait, 0)),
      read_wait_max_ms: sortedWaits.length ? roundMs(sortedWaits[sortedWaits.length - 1]) : null,
      read_wait_p95_ms: roundMs(p95), read_error_count: readErrorCount, error_count: errorCount,
      diagnostic_error_count: diagnosticErrorCount, first_chunk: firstChunk, last_chunk: lastChunk,
      visibility: { initial: initialVisibility, final: visibility(), states: [...visibilityStates], changes: visibilityChanges },
      monitor: { started_at_ms: roundMs(monitorStarted), ended_at_ms: roundMs(monitorEnded),
        time_origin_unix_ms: roundMs(wallOrigin), long_task_enabled: longTaskMonitoring,
        scope: "request_to_result_parsed", time_basis: "browser_performance_now_uncalibrated" },
      long_tasks: { count: longTaskCount, total_ms: roundMs(longTaskMs), max_ms: roundMs(longestTaskMs), entries: longTasks },
      frames: { progress100: frameSummary(progressFrame), complete: frameSummary(completeFrame),
        same_chunk: typeof sse.sameChunk === "boolean" ? sse.sameChunk : null,
        complete_event_parsed_at_ms: roundMs(sse.completeEventParsedAt),
        complete_first_to_event_parsed_ms: Number.isFinite(sse.completeFirstAt)
          ? roundMs(sse.completeEventParsedAt - sse.completeFirstAt) : null,
        complete_read_wait_ms: roundMs(completeReadWaitMs), complete_between_reads_ms: roundMs(completeBetweenReadsMs) },
      tail_status: tailStatus, read_details_enabled: debugReads,
    };
    if (debugReads) summary.read_details = readSamples;
    safeLog("[Paipu SSE Browser]", summary);
  }
  function stopMonitoring() {
    if (monitorEnded !== Infinity) return;
    monitorEnded = performance.now();
    // Preserve the immediate timing snapshot expected by existing callers;
    // long-task records and the final stream summary may finish asynchronously.
    timings.diagnostics = { longTaskCount, longTaskMs, longestTaskMs, completeReadWaitMs, completeBetweenReadsMs,
      visibilityStates: [...visibilityStates] };
    const finish = () => {
      if (observer) {
        try { recordLongTasks(observer.takeRecords()); } catch (_) {}
        try { observer.disconnect(); } catch (_) {}
        observer = null;
      }
      monitorFinished = true;
      reportDiagnostics();
    };
    // The current long task becomes observable only after yielding to the browser.
    if (observer && typeof setTimeout === "function") setTimeout(finish, 0);
    else finish();
  }
  function finishStreamDiagnostics() {
    if (streamFinished) return;
    streamFinished = true;
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visibilityChanged);
    reportDiagnostics();
  }
  if (typeof PerformanceObserver !== "undefined" && PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
    try {
      observer = new PerformanceObserver((list) => recordLongTasks(list.getEntries()));
      observer.observe({ type: "longtask", buffered: false });
      longTaskMonitoring = true;
    } catch (_) {
      observer = null;
    }
  }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibilityChanged);

  function measureChunk(chunk, index, receivedAt) {
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline < 0 ? chunk.length : newline + 1;
      if (!frame) frame = { bytes: 0, prefix: "", firstChunk: index, firstAt: receivedAt };
      frame.bytes += end - offset;
      frame.lastChunk = index;
      frame.lastAt = receivedAt;
      if (!frame.kind) {
        const prefixEnd = Math.min(end, offset + Math.max(0, 256 - frame.prefix.length));
        for (let i = offset; i < prefixEnd; i++) frame.prefix += String.fromCharCode(chunk[i]);
        const complete = /"type"\s*:\s*"complete"/.test(frame.prefix) || /(?:^|[\r\n])event:\s*complete\s*[\r\n]/.test(frame.prefix);
        const progress = /"type"\s*:\s*"progress"/.test(frame.prefix) || /(?:^|[\r\n])event:\s*progress\s*[\r\n]/.test(frame.prefix);
        if (complete) {
          frame.kind = "complete";
          completeFrame = frame;
        } else if (progress && /"percent"\s*:\s*100\s*[,}]/.test(frame.prefix)) {
          frame.kind = "100%";
          progressFrame = frame;
        }
        if (frame.kind) frame.prefix = "";
      }
      const contentEnd = newline < 0 ? end : newline;
      if (contentEnd - offset > 1 || (contentEnd - offset === 1 && chunk[offset] !== 13)) lineHasContent = true;
      if (newline >= 0) {
        if (!lineHasContent) {
          frame = null;
        }
        lineHasContent = false;
      }
      offset = end;
    }
  }

  const requestBody = { ...postBody };
  delete requestBody.accept_compression;
  if (supportsCompression) requestBody.accept_compression = "gzip+base64";
  let sameOrigin = true;
  if (typeof window !== "undefined" && window.location) {
    try {
      sameOrigin = new URL(url, window.location.href).origin === window.location.origin;
    } catch (_) {
      sameOrigin = false;
    }
  }
  let resp;
  try {
    const authHeaders = apiAuthHeaders();
    for (const [key, value] of Object.entries(authHeaders)) {
      if (sensitiveKey.test(key) && typeof value === "string" && value) sensitiveValues.push(value);
    }
    try {
      const parsedUrl = new URL(url, typeof window !== "undefined" ? window.location.href : undefined);
      if (parsedUrl.username) sensitiveValues.push(parsedUrl.username);
      if (parsedUrl.password) sensitiveValues.push(parsedUrl.password);
      for (const [key, value] of parsedUrl.searchParams) {
        if (sensitiveUrlKey.test(key) && value) sensitiveValues.push(value);
      }
    } catch (_) {}
    resp = await fetch(url, {
      method: "POST",
      credentials: isPagesProxyMode() ? "omit" : "same-origin",
      redirect: "error",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...authHeaders,
        ...(sameOrigin ? { "X-Request-ID": clientRequestId } : {}),
      },
      body: JSON.stringify(requestBody),
    });
  } catch (_) {
    errorCount++;
    tailStatus = "fetch_error";
    stopMonitoring();
    finishStreamDiagnostics();
    throw new Error("无法确认分析请求是否送达，服务器可能已开始处理。立即重试可能导致重复推理。");
  }

  const responseRequestId = resp.headers?.get?.("X-Request-ID");
  if (validRequestId(responseRequestId)) {
    serverRequestId = responseRequestId;
    timings.serverRequestId = serverRequestId;
  }
  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    const detail = `HTTP ${resp.status}: ${errText || resp.statusText}`;
    errorCount++;
    tailStatus = "http_error";
    stopMonitoring();
    finishStreamDiagnostics();
    if (resp.status >= 400 && resp.status < 500 && resp.status !== 408) {
      throw new Error(`分析请求被拒绝（${detail}）。`);
    }
    throw new Error(`分析服务没有正常响应（${detail}）。服务器可能已开始处理，立即重试可能导致重复推理。`);
  }
  if (!resp.body) {
    errorCount++;
    tailStatus = "no_body";
    stopMonitoring();
    finishStreamDiagnostics();
    throw receiveError("无法读取分析响应，服务器可能仍在处理，立即重试可能导致重复推理。");
  }

  let reader;
  try {
    reader = resp.body.getReader();
  } catch (_) {
    errorCount++;
    tailStatus = "reader_error";
    stopMonitoring();
    finishStreamDiagnostics();
    throw receiveError("无法读取分析响应，服务器可能仍在处理，立即重试可能导致重复推理。");
  }
  const decoder = new TextDecoder("utf-8");
  let lineParts = [];
  let dataLines = [];
  let eventName = "";
  let skipLeadingLf = false;
  let completionEvent = null;
  let ended = false;

  function dispatchEvent() {
    const receivedAt = performance.now();
    const data = dataLines.join("\n");
    dataLines = [];
    const name = eventName;
    eventName = "";
    if (!data) return;
    let ev;
    const eventParseStarted = performance.now();
    try {
      ev = JSON.parse(data);
    } catch (_) {
      // A malformed optional diagnostic must not change the business outcome.
      if (completionEvent || name === "performance" || /"type"\s*:\s*"performance"/.test(data.slice(0, 512))) {
        diagnosticErrorCount++;
        errorCount++;
        return;
      }
      throw receiveError("接收的分析数据格式不完整，服务器可能仍在处理，立即重试可能导致重复推理。");
    }
    const parsedAt = performance.now();
    const type = ev?.type || name;
    if (type === "performance") {
      if (!ev?.performance || typeof ev.performance !== "object" || Array.isArray(ev.performance)) {
        diagnosticErrorCount++;
        errorCount++;
        return;
      }
      if (performanceLogged) return;
      // The performance payload is emitted by the backend itself and therefore
      // takes precedence over a response header supplied by an intermediary.
      const eventRequestId = ev.performance.request_id;
      if (validRequestId(eventRequestId)) {
        serverRequestId = eventRequestId;
        timings.serverRequestId = serverRequestId;
      }
      performanceLogged = true;
      safeLog("[Paipu Performance]", { client_request_id: clientRequestId,
        server_request_id: serverRequestId, performance: ev.performance });
      return;
    }
    // Once complete has been received, trailing progress/errors are diagnostic
    // input only and must not alter the already completed review.
    if (completionEvent) return;
    if (type === "progress" || type === "queued") {
      const percent = Number(ev.percent);
      if (type === "progress" && percent === 100) timings.progress100ReceivedAt = progressFrame?.lastAt ?? receivedAt;
      const shown = Number.isFinite(percent) ? Math.min(percent, 99) : (type === "queued" ? 5 : 0);
      onProgress(shown, type === "progress" && percent >= 100 ? "AI 检讨完成，正在接收结果..." :
        (ev.message || (type === "queued" ? "服务器正忙，当前正在排队中..." : "正在分析牌谱...")));
    } else if (type === "complete") {
      timings.completeReceivedAt = completeFrame?.lastAt ?? receivedAt;
      const progressAt = timings.progress100ReceivedAt;
      const first = completeFrame?.firstAt;
      const sameChunk = progressFrame && completeFrame ? progressFrame.lastChunk === completeFrame.firstChunk : "N/A";
      timings.sse = {
        completeBytes: completeFrame?.bytes ?? null,
        completeFirstAt: first ?? null,
        completeLastAt: timings.completeReceivedAt,
        completeEventParsedAt: parsedAt,
        progressLastAt: progressAt ?? null,
        sameChunk,
        chunkCount,
        totalBytes,
      };
      completionEvent = { event: ev, eventParseMs: parsedAt - eventParseStarted };
    } else if (type === "error") {
      throw Object.assign(new Error(`服务器分析失败：${ev.detail || "分析发生错误"}`), { serverReported: true });
    }
  }

  async function restoreComplete({ event, eventParseMs }) {
    if (event.encoding == null) {
      safeLog("[Paipu Compression]", { client_request_id: clientRequestId, server_request_id: serverRequestId,
        format: "raw_json", compressed_bytes: null, decompression_ms: 0, decompressed_bytes: null,
        json_parse_ms: roundMs(eventParseMs) });
      return event.result;
    }
    if (event.encoding !== "gzip+base64" || typeof event.data !== "string" || !supportsCompression) {
      throw receiveError("接收的压缩分析结果格式不受支持，请刷新页面后重试。");
    }
    try {
      const base64Started = performance.now();
      const binary = atob(event.data);
      const compressed = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) compressed[i] = binary.charCodeAt(i);
      const base64Ms = performance.now() - base64Started;
      const decompressStarted = performance.now();
      const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("gzip"));
      const decoded = await new Response(stream).arrayBuffer();
      const decompressMs = performance.now() - decompressStarted;
      const text = new TextDecoder("utf-8", { fatal: true }).decode(decoded);
      const parseStarted = performance.now();
      const result = JSON.parse(text);
      const parseMs = performance.now() - parseStarted;
      safeLog("[Paipu Compression]", { client_request_id: clientRequestId, server_request_id: serverRequestId,
        format: "gzip+base64", compressed_bytes: compressed.byteLength, base64_bytes: event.data.length,
        base64_decode_ms: roundMs(base64Ms), decompression_ms: roundMs(decompressMs),
        decompressed_bytes: decoded.byteLength, json_parse_ms: roundMs(parseMs), event_json_parse_ms: roundMs(eventParseMs) });
      return result;
    } catch (_) {
      throw receiveError("接收的压缩分析结果无法恢复，请稍后重试。");
    }
  }

  function processLine(line) {
    if (!line) {
      dispatchEvent();
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") dataLines.push(value);
    else if (field === "event") eventName = value;
  }

  function processText(text, stopAtComplete = false) {
    if (!text) return "";
    let start = 0;
    if (skipLeadingLf) {
      if (text[0] === "\n") start = 1;
      skipLeadingLf = false;
    }
    while (start < text.length) {
      let i = text.indexOf("\n", start);
      const carriage = text.indexOf("\r", start);
      if (carriage >= 0 && (i < 0 || carriage < i)) i = carriage;
      if (i < 0) break;
      lineParts.push(text.slice(start, i));
      processLine(lineParts.join(""));
      lineParts = [];
      if (text[i] === "\r") {
        if (text[i + 1] === "\n") i++;
        else if (i + 1 === text.length) skipLeadingLf = true;
      }
      start = i + 1;
      if (stopAtComplete && completionEvent) return text.slice(start);
    }
    if (start < text.length) lineParts.push(text.slice(start));
    return "";
  }

  let readFailed = false;
  async function measuredRead() {
    const readStarted = performance.now();
    // These two legacy metrics cover reads from the first complete frame byte
    // through complete parsing, not the optional diagnostic tail.
    const readingComplete = Boolean(completeFrame) && !completionEvent;
    if (readingComplete && previousReadFinished !== null) completeBetweenReadsMs += readStarted - previousReadFinished;
    let packet;
    let failure;
    let failed = false;
    try {
      packet = await reader.read();
    } catch (error) {
      failed = true;
      failure = error;
      readFailed = true;
      readErrorCount++;
      errorCount++;
    }
    const readFinished = performance.now();
    const wait = readFinished - readStarted;
    if (readingComplete) completeReadWaitMs += wait;
    previousReadFinished = readFinished;
    readCount++;
    readWaits.push(wait);
    const size = failed || packet.done ? 0 : packet.value.byteLength;
    const currentVisibility = visibility();
    visibilityStates.add(currentVisibility);
    if (!failed && !packet.done) {
      chunkCount++;
      totalBytes += size;
      const chunk = { index: chunkCount, read_index: readCount, bytes: size, received_at_ms: roundMs(readFinished) };
      if (!firstChunk) {
        firstChunk = chunk;
        firstChunkAt = readFinished;
      }
      lastChunk = chunk;
      lastChunkAt = readFinished;
    }
    if (readSamples) readSamples.push({ index: readCount, started_at_ms: roundMs(readStarted),
      finished_at_ms: roundMs(readFinished), wait_ms: roundMs(wait), bytes: size,
      outcome: failed ? "error" : (packet.done ? "eof" : "chunk"), visibility: currentVisibility });
    if (failed) throw failure;
    return { ...packet, readFinished };
  }
  function cancelReader() {
    try { Promise.resolve(reader.cancel()).catch(() => {}); } catch (_) {}
  }
  function releaseReader() {
    try { reader.releaseLock(); } catch (_) {}
  }

  let tailText = "";
  async function drainDiagnosticTail() {
    // Bound resource use if an old/broken server leaves the stream open forever.
    // This timer never delays the result or the view transition.
    const timer = !ended && typeof setTimeout === "function" ? setTimeout(() => {
      tailStatus = "timeout";
      diagnosticErrorCount++;
      errorCount++;
      cancelReader();
    }, 10000) : null;
    try {
      if (tailText) processText(tailText);
      while (!ended && tailStatus !== "timeout") {
        const { done, value } = await measuredRead();
        if (done) {
          ended = true;
          break;
        }
        processText(decoder.decode(value, { stream: true }));
      }
      if (ended && tailStatus !== "timeout") {
        processText(decoder.decode());
        if (lineParts.length) {
          processLine(lineParts.join(""));
          lineParts = [];
        }
        dispatchEvent();
        tailStatus = "eof";
      }
    } catch (_) {
      if (tailStatus !== "timeout") {
        tailStatus = readFailed ? "read_error" : "diagnostic_error";
        diagnosticErrorCount++;
        if (!readFailed) errorCount++;
      }
    } finally {
      if (timer !== null && typeof clearTimeout === "function") clearTimeout(timer);
      if (!ended) cancelReader();
      releaseReader();
      finishStreamDiagnostics();
    }
  }

  let delivered = false;
  try {
    while (true) {
      const { done, value, readFinished } = await measuredRead();
      if (done) {
        ended = true;
        break;
      }
      measureChunk(value, chunkCount, readFinished);
      tailText = processText(decoder.decode(value, { stream: true }), true);
      if (completionEvent) break;
    }
    if (!completionEvent) {
      tailText = processText(decoder.decode(), true);
      if (!completionEvent) {
        if (lineParts.length) {
          processLine(lineParts.join(""));
          lineParts = [];
        }
        dispatchEvent();
      }
    }
    if (!completionEvent) {
      throw receiveError("分析结果未完整接收，服务器可能仍在处理，立即重试可能导致重复推理。");
    }
    const result = await restoreComplete(completionEvent);
    if (!result) {
      throw receiveError("分析结果未完整接收，服务器可能仍在处理，立即重试可能导致重复推理。");
    }
    timings.sse.completeParsedAt = performance.now();
    onProgress(100, "分析完成，正在加载回放...");
    delivered = true;
    return result;
  } catch (err) {
    if (!readFailed) errorCount++;
    tailStatus = "business_error";
    if (err?.serverReported || err?.receiveError) throw err;
    throw receiveError("分析连接中断，服务器可能仍在处理，立即重试可能导致重复推理。");
  } finally {
    stopMonitoring();
    if (delivered && (!ended || tailText)) {
      tailStatus = "pending";
      // Let the caller render first, including when both events share a chunk.
      if (typeof setTimeout === "function") setTimeout(() => { void drainDiagnosticTail(); }, 0);
      else Promise.resolve().then(drainDiagnosticTail);
    } else {
      if (!ended) cancelReader();
      else if (delivered) tailStatus = "eof";
      releaseReader();
      finishStreamDiagnostics();
    }
  }
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
