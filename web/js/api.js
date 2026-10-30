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
  let requestId = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll("-", "")
    : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  timings.requestId = requestId;
  const visibility = () => typeof document === "undefined" ? "unknown" : document.visibilityState;
  const wallOrigin = performance.timeOrigin || (Date.now() - performance.now());
  const monitorStarted = performance.now();
  let monitorEnded = Infinity;
  let observer = null;
  let readSamples = [];
  let readCount = 0;
  let chunkCount = 0;
  let totalBytes = 0;
  let previousReadFinished = null;
  let completeReadWaitMs = 0;
  let completeBetweenReadsMs = 0;
  let longTaskCount = 0;
  let longTaskMs = 0;
  let longestTaskMs = 0;
  const visibilityStates = new Set([visibility()]);
  let progressFrame = null;
  let completeFrame = null;
  let frame = null;
  let lineHasContent = false;

  function flushReads() {
    if (!readSamples.length) return;
    console.info(`[Paipu SSE Browser] 请求ID=${requestId} read明细=序号,开始ms,完成ms,字节,可见状态或错误 ${JSON.stringify(readSamples)}`);
    readSamples = [];
  }
  function logLongTasks(entries) {
    for (const entry of entries) {
      if (entry.startTime > monitorEnded || entry.startTime + entry.duration < monitorStarted) continue;
      longTaskCount++;
      longTaskMs += entry.duration;
      longestTaskMs = Math.max(longestTaskMs, entry.duration);
      console.info(`[Paipu SSE Browser] 请求ID=${requestId} 长任务开始=${entry.startTime.toFixed(1)}ms 耗时=${entry.duration.toFixed(1)}ms 当前可见状态=${visibility()}`);
    }
  }
  function visibilityChanged() {
    visibilityStates.add(visibility());
    console.info(`[Paipu SSE Browser] 请求ID=${requestId} 可见状态=${visibility()} 时间=${performance.now().toFixed(1)}ms`);
  }
  function stopDiagnostics() {
    monitorEnded = performance.now();
    flushReads();
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visibilityChanged);
    const finish = () => {
      if (observer) {
        logLongTasks(observer.takeRecords());
        observer.disconnect();
        observer = null;
      }
      timings.diagnostics = { longTaskCount, longTaskMs, longestTaskMs, completeReadWaitMs, completeBetweenReadsMs,
        visibilityStates: [...visibilityStates] };
      console.info(`[Paipu SSE Browser] 请求ID=${requestId} 数据块数量=${chunkCount} 接收字节=${totalBytes} complete后续read等待=${completeReadWaitMs.toFixed(1)}ms complete读块间隔=${completeBetweenReadsMs.toFixed(1)}ms 长任务数量=${longTaskCount} 长任务总耗时=${longTaskMs.toFixed(1)}ms 可见状态=${visibility()}`);
    };
    if (observer) setTimeout(finish, 0);
    else finish();
  }
  if (typeof PerformanceObserver !== "undefined" && PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
    try {
      observer = new PerformanceObserver((list) => logLongTasks(list.getEntries()));
      observer.observe({ type: "longtask", buffered: false });
    } catch (_) {
      observer = null;
    }
  }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibilityChanged);
  console.info(`[Paipu SSE Browser] 请求ID=${requestId} 起始可见状态=${visibility()} performance时间原点Unix=${wallOrigin.toFixed(1)}ms 长任务监控=${observer ? "启用" : "不可用"} 监控范围=SSE请求至结果解析 说明=read完成是JS可观察时间，跨机时钟未校准`);

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
        if (frame.kind) {
          frame.prefix = "";
          console.info(`[Paipu SSE Browser] 请求ID=${requestId} 事件=${frame.kind} 首块=${frame.firstChunk} 首块read完成=${frame.firstAt.toFixed(1)}ms 可见状态=${visibility()}`);
        }
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
    resp = await fetch(url, {
      method: "POST",
      credentials: isPagesProxyMode() ? "omit" : "same-origin",
      redirect: "error",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...apiAuthHeaders(),
        ...(sameOrigin ? { "X-Request-ID": requestId } : {}),
      },
      body: JSON.stringify(requestBody),
    });
  } catch (_) {
    stopDiagnostics();
    throw new Error("无法确认分析请求是否送达，服务器可能已开始处理。立即重试可能导致重复推理。");
  }

  const responseRequestId = resp.headers?.get?.("X-Request-ID");
  if (typeof responseRequestId === "string" && /^[0-9a-f]{32}$/.test(responseRequestId) && responseRequestId !== requestId) {
    console.info(`[Paipu SSE Browser] 本地请求ID=${requestId} 服务端请求ID=${responseRequestId}`);
    requestId = responseRequestId;
    timings.requestId = requestId;
  }
  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    const detail = `HTTP ${resp.status}: ${errText || resp.statusText}`;
    stopDiagnostics();
    if (resp.status >= 400 && resp.status < 500 && resp.status !== 408) {
      throw new Error(`分析请求被拒绝（${detail}）。`);
    }
    throw new Error(`分析服务没有正常响应（${detail}）。服务器可能已开始处理，立即重试可能导致重复推理。`);
  }
  if (!resp.body) {
    stopDiagnostics();
    throw receiveError("无法读取分析响应，服务器可能仍在处理，立即重试可能导致重复推理。");
  }

  const reader = resp.body.getReader();
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
      throw receiveError("接收的分析数据格式不完整，服务器可能仍在处理，立即重试可能导致重复推理。");
    }
    const parsedAt = performance.now();
    const type = ev.type || name;
    if (type === "progress" || type === "queued") {
      const percent = Number(ev.percent);
      if (type === "progress" && percent === 100) timings.progress100ReceivedAt = progressFrame?.lastAt ?? receivedAt;
      const shown = Number.isFinite(percent) ? Math.min(percent, 99) : (type === "queued" ? 5 : 0);
      onProgress(shown, type === "progress" && percent >= 100 ? "AI 检讨完成，正在接收结果..." :
        (ev.message || (type === "queued" ? "服务器正忙，当前正在排队中..." : "正在分析牌谱...")));
    } else if (type === "complete") {
      timings.completeReceivedAt = completeFrame?.lastAt ?? receivedAt;
      const progressAt = timings.progress100ReceivedAt;
      const progressTime = Number.isFinite(progressAt) ? `${progressAt.toFixed(1)}ms` : "N/A";
      const receiveInterval = Number.isFinite(progressAt) ? `${(timings.completeReceivedAt - progressAt).toFixed(1)}ms` : "N/A";
      const first = completeFrame?.firstAt;
      const firstTime = Number.isFinite(first) ? `${first.toFixed(1)}ms` : "N/A";
      const parseDuration = Number.isFinite(first) ? `${(parsedAt - first).toFixed(1)}ms` : "N/A";
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
      console.info(`[Paipu UX Performance] 请求ID=${requestId} 100%事件接收时间=${progressTime} complete事件接收时间=${timings.completeReceivedAt.toFixed(1)}ms 100%事件至complete事件接收=${receiveInterval} 时间基准=页面performance.now`);
      console.info(`[Paipu SSE Browser] 请求ID=${requestId} complete首块=${firstTime} complete末块=${timings.completeReceivedAt.toFixed(1)}ms complete首块Unix=${Number.isFinite(first) ? (wallOrigin + first).toFixed(1) : "N/A"}ms complete事件字节数=${completeFrame?.bytes ?? "N/A"} 字节范围=Fetch解码后的SSE帧 首块至事件解析完成=${parseDuration} 100%与complete同块=${sameChunk} 可见状态=${visibility()}`);
      completionEvent = { event: ev, eventParseMs: parsedAt - eventParseStarted };
    } else if (type === "error") {
      throw Object.assign(new Error(`服务器分析失败：${ev.detail || "分析发生错误"}`), { serverReported: true });
    }
  }

  async function restoreComplete({ event, eventParseMs }) {
    if (event.encoding == null) {
      console.info(`[Paipu Compression] 请求ID=${requestId} 格式=原始JSON 接收压缩数据大小=N/A 解压耗时=0.0ms 解压后数据大小=N/A JSON解析耗时=${eventParseMs.toFixed(1)}ms`);
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
      console.info(`[Paipu Compression] 请求ID=${requestId} 格式=gzip+base64 接收压缩数据大小=${compressed.byteLength}B Base64数据大小=${event.data.length}B Base64解码耗时=${base64Ms.toFixed(1)}ms 解压耗时=${decompressMs.toFixed(1)}ms 解压后数据大小=${decoded.byteLength}B JSON解析耗时=${parseMs.toFixed(1)}ms 事件JSON解析耗时=${eventParseMs.toFixed(1)}ms`);
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

  function processText(text) {
    if (!text) return;
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
    }
    if (start < text.length) lineParts.push(text.slice(start));
  }

  try {
    while (true) {
      const readStarted = performance.now();
      const readingComplete = Boolean(completeFrame);
      if (readingComplete && previousReadFinished !== null) completeBetweenReadsMs += readStarted - previousReadFinished;
      let packet;
      try {
        packet = await reader.read();
      } catch (error) {
        readCount++;
        readSamples.push([readCount, +readStarted.toFixed(1), +performance.now().toFixed(1), 0, "error"]);
        throw error;
      }
      const { done, value } = packet;
      const readFinished = performance.now();
      if (readingComplete) completeReadWaitMs += readFinished - readStarted;
      previousReadFinished = readFinished;
      readCount++;
      const size = done ? 0 : value.byteLength;
      visibilityStates.add(visibility());
      readSamples.push([readCount, +readStarted.toFixed(1), +readFinished.toFixed(1), size, visibility()]);
      if (readSamples.length >= 128) flushReads();
      if (done) {
        ended = true;
        break;
      }
      chunkCount++;
      totalBytes += size;
      measureChunk(value, chunkCount, readFinished);
      processText(decoder.decode(value, { stream: true }));
      if (completionEvent) break;
    }
    if (!completionEvent) {
      processText(decoder.decode());
      if (lineParts.length) processLine(lineParts.join(""));
      dispatchEvent();
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
    return result;
  } catch (err) {
    if (err?.serverReported || err?.receiveError) throw err;
    throw receiveError("分析连接中断，服务器可能仍在处理，立即重试可能导致重复推理。");
  } finally {
    if (!ended) reader.cancel().catch(() => {});
    reader.releaseLock();
    stopDiagnostics();
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
