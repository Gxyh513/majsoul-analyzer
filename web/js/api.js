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
  const clientRequestId = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll("-", "")
    : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  let serverRequestId = null;
  // requestId remains the local ID for existing callers; it is not evidence of
  // a server-side match when the cross-origin request could not send the header.
  timings.requestId = clientRequestId;
  timings.clientRequestId = clientRequestId;
  timings.serverRequestId = null;
  const visibility = () => {
    const value = typeof document === "undefined" ? null : document.visibilityState;
    return ["visible", "hidden", "prerender", "unloaded"].includes(value) ? value : "unknown";
  };
  const displayVisibility = (value) => ({ visible: "可见", hidden: "隐藏", prerender: "预渲染", unloaded: "已卸载" }[value] || "未知");
  const initialVisibility = visibility();
  const wallOrigin = performance.timeOrigin || (Date.now() - performance.now());
  const monitorStarted = performance.now();
  let monitorEnded = Infinity;
  let monitorFinished = false;
  let streamFinished = false;
  let summaryLogged = false;
  let observer = null;
  let longTaskMonitoring = false;
  const readSamples = [];
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
  let serverBusinessStatus = null;
  let delivered = false;

  function isDetailedDiagnosticMode() {
    try {
      if (timings && (timings.debug === true || timings.debug_sse === true)) return true;
      if (typeof window !== "undefined") {
        if (window.__PAIPU_DEBUG_SSE__ === true || window.__PAIPU_DEBUG__ === true) return true;
        if (typeof window.location?.search === "string" && /(?:[?&])debug(?:_sse)?=(?:1|true)\b/i.test(window.location.search)) return true;
        if (window.localStorage && window.localStorage.getItem("paipu_debug_sse") === "true") return true;
      }
    } catch (_) {}
    return false;
  }

  function limitDiagnosticArray(items, maxLimit) {
    if (!Array.isArray(items)) return null;
    const total = items.length;
    const isTruncated = total > maxLimit;
    const list = isTruncated ? items.slice(0, maxLimit) : items.slice();
    Object.defineProperty(list, "总记录数", { value: total, enumerable: true, configurable: true, writable: true });
    Object.defineProperty(list, "已输出数", { value: list.length, enumerable: true, configurable: true, writable: true });
    Object.defineProperty(list, "是否截断", { value: isTruncated, enumerable: true, configurable: true, writable: true });
    Object.defineProperty(list, "采样上限", { value: maxLimit, enumerable: true, configurable: true, writable: true });
    Object.defineProperty(list, "明细", { value: list, enumerable: true, configurable: true, writable: true });
    return list;
  }
  const sensitiveValues = [];
  const sensitiveKey = /token|secret|password|passwd|authorization|cookie|credential|api[-_]?key/i;
  const sensitiveUrlKey = /(?:^|[_-])(key|signature|sig|auth|credential|session)(?:$|[_-])|token|secret|password/i;
  const roundMs = (value) => Number.isFinite(value) ? Math.round(value * 1000) / 1000 : null;
  const sizeKB = (value) => Number.isFinite(value) ? Number((value / 1024).toFixed(2)) : null;
  const validRequestId = (value) => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
  const privateDataKey = /(?:^|[_-])(?:result|review|content|data|payload)(?:$|[_-])|^(?:原始牌谱|原始数据|牌谱内容|牌谱数据|完整牌谱|分析结果|检讨结果|错误信息|错误详情|异常信息|异常详情|堆栈|牌谱|原文)$/i;
  const privateText = /https?:\/\/|\b(?:bearer|basic)\s+\S+|(?:token|secret|password|authorization|cookie|api[-_]?key)\s*[:=]\s*\S+|^\s*[\[{]/i;

  // Only server performance fields are passed here. Copy them before logging so
  // the console never holds the original potentially sensitive payload.
  function sanitizeDiagnostic(value, key = "") {
    if (value == null) return null;
    if (sensitiveKey.test(key) || (privateDataKey.test(key) && typeof value !== "number" && typeof value !== "boolean")) {
      return "[已脱敏]";
    }
    if (typeof value === "string") {
      return privateText.test(value) || sensitiveValues.some((secret) => value.includes(secret)) ? "[已脱敏]" : value;
    }
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value === "boolean") return value;
    if (Array.isArray(value)) return value.map((item) => sanitizeDiagnostic(item));
    if (typeof value === "object") {
      const clean = Object.create(null);
      for (const [name, item] of Object.entries(value)) {
        const safeName = privateText.test(name) || sensitiveValues.some((secret) => name.includes(secret))
          ? "已脱敏字段" : name;
        clean[safeName] = sanitizeDiagnostic(item, name);
      }
      return clean;
    }
    return null;
  }
  function safeReadError(error) {
    // Arbitrary browser/network error text can contain URLs, credentials or the
    // response body. Retain its safe error category, never the original text.
    try {
      const categories = { AbortError: "读取中止", NetworkError: "网络错误", TimeoutError: "读取超时", TypeError: "读取异常" };
      const name = error?.name;
      const category = Object.prototype.hasOwnProperty.call(categories, name) ? categories[name] : "读取失败";
      return `${category}（详细信息已脱敏）`;
    } catch (_) {
      return "读取失败（详细信息已脱敏）";
    }
  }
  function safeLog(_title, _entries) {
    // 取消浏览器端所有自定义 SSE 和页面性能控制台日志输出
  }
  function recordLongTasks(entries) {
    for (const entry of entries) {
      if (entry.startTime > monitorEnded || entry.startTime + entry.duration < monitorStarted) continue;
      longTaskCount++;
      longTaskMs += entry.duration;
      longestTaskMs = Math.max(longestTaskMs, entry.duration);
      longTasks.push({ 开始时间_ms: roundMs(entry.startTime), 耗时_ms: roundMs(entry.duration), 可见状态: displayVisibility(visibility()) });
    }
  }
  function visibilityChanged() {
    const state = visibility();
    visibilityStates.add(state);
    visibilityChanges.push({ 状态: displayVisibility(state), 时间_ms: roundMs(performance.now()) });
  }
  function frameSummary(item) {
    return item ? { 数据大小_KB: sizeKB(item.bytes), 首分块序号: item.firstChunk, 末分块序号: item.lastChunk,
      首分块接收时间_ms: roundMs(item.firstAt), 末分块接收时间_ms: roundMs(item.lastAt),
      首分块接收时间_Unix_ms: roundMs(wallOrigin + item.firstAt) } : null;
  }
  function reportDiagnostics() {
    if (!monitorFinished || !streamFinished || summaryLogged) return;
    summaryLogged = true;
    const debugMode = isDetailedDiagnosticMode();
    const sortedWaits = [...readWaits].sort((a, b) => a - b);
    const p95 = sortedWaits.length ? sortedWaits[Math.ceil(sortedWaits.length * 0.95) - 1] : null;
    const sse = timings.sse || {};
    timings.diagnostics = { longTaskCount, longTaskMs, longestTaskMs, completeReadWaitMs, completeBetweenReadsMs,
      visibilityStates: [...visibilityStates] };
    const tailStates = { not_started: "未开始", fetch_error: "请求失败", http_error: "HTTP错误", no_body: "响应体缺失",
      reader_error: "读取器不可用", timeout: "超时", read_error: "读取失败", diagnostic_error: "诊断失败",
      eof: "流结束", business_error: "业务失败", pending: "待读取" };
    const requestStatus = delivered ? "成功" : (tailStates[tailStatus] || "失败");
    const progress100At = timings.progress100ReceivedAt ?? progressFrame?.lastAt ?? null;
    const completeAt = timings.completeReceivedAt ?? completeFrame?.lastAt ?? null;
    const progress100ToCompleteMs = Number.isFinite(progress100At) && Number.isFinite(completeAt)
      ? roundMs(completeAt - progress100At) : null;
    const totalReadWaitMs = readWaits.reduce((sum, wait) => sum + wait, 0);
    const maxReadWaitMs = sortedWaits.length ? sortedWaits[sortedWaits.length - 1] : null;

    const requestInfo = {
      客户端请求ID: clientRequestId,
      服务端请求ID: serverRequestId,
      请求结果状态: requestStatus,
    };

    const businessInfo = {
      牌谱获取方式: serverBusinessStatus?.牌谱获取方式 ?? null,
      牌谱获取结果: serverBusinessStatus?.牌谱获取结果 ?? null,
      是否命中本地记录: serverBusinessStatus?.是否命中本地记录 ?? null,
      是否等待文件锁: serverBusinessStatus?.是否等待文件锁 ?? null,
    };
    if (serverBusinessStatus?.牌谱获取HTTP状态码 !== undefined && serverBusinessStatus?.牌谱获取HTTP状态码 !== null) {
      businessInfo.牌谱获取HTTP状态码 = serverBusinessStatus.牌谱获取HTTP状态码;
    }

    const getPageSwitchDuration = () => {
      if (Number.isFinite(timings.pageSwitchDurationMs)) return roundMs(timings.pageSwitchDurationMs);
      if (Number.isFinite(timings.viewCompletedAt) && Number.isFinite(completeAt)) {
        return roundMs(timings.viewCompletedAt - completeAt);
      }
      return null;
    };

    const timingInfo = {
      首个分块接收时间_ms: roundMs(firstChunkAt),
      complete接收时间_ms: roundMs(completeAt),
      "100%至complete接收间隔_ms": progress100ToCompleteMs,
      JSON解析耗时_ms: roundMs(sse.completeJsonParseMs),
    };
    Object.defineProperty(timingInfo, "页面切换完成耗时_ms", {
      enumerable: true,
      configurable: true,
      get: getPageSwitchDuration,
    });

    const transferInfo = {
      分块数量: chunkCount,
      读取次数: readCount,
      浏览器实际读取数据总量_KB: sizeKB(totalBytes),
      读取等待总耗时_ms: roundMs(totalReadWaitMs),
      最大读取等待时间_ms: roundMs(maxReadWaitMs),
      P95读取等待时间_ms: roundMs(p95),
      数据口径: "HTTP自动解压后的SSE数据，非传输压缩大小",
    };

    const visibilityReport = {
      初始: displayVisibility(initialVisibility),
      最终: displayVisibility(visibility()),
      状态集合: [...visibilityStates].map(displayVisibility),
    };
    if (debugMode) {
      visibilityReport.变化 = limitDiagnosticArray(visibilityChanges, 100);
      visibilityReport.总记录数 = visibilityChanges.length;
      visibilityReport.是否截断 = visibilityChanges.length > 100;
      visibilityReport.采样上限 = 100;
    }

    const longTaskReport = {
      次数: longTaskCount,
      总耗时_ms: roundMs(longTaskMs),
      最大耗时_ms: roundMs(longestTaskMs),
    };
    if (debugMode) {
      longTaskReport.明细 = limitDiagnosticArray(longTasks, 100);
      longTaskReport.总记录数 = longTasks.length;
      longTaskReport.是否截断 = longTasks.length > 100;
      longTaskReport.采样上限 = 100;
    }

    const browserStatus = {
      长任务次数: longTaskCount,
      长任务总耗时_ms: roundMs(longTaskMs),
      最长长任务耗时_ms: roundMs(longestTaskMs),
      页面可见状态: visibilityReport,
      读取错误次数: readErrorCount,
      诊断错误次数: diagnosticErrorCount,
    };

    const summary = {
      事件: "浏览器SSE性能统计", 版本: 1,
      请求信息: requestInfo,
      牌谱业务: businessInfo,
      时间信息: timingInfo,
      数据传输: transferInfo,
      浏览器状态: browserStatus,
      // 保留原有兼容字段，确保已有测试与排查脚本可用
      客户端请求ID: clientRequestId, 服务端请求ID: serverRequestId,
      请求结果状态: requestStatus,
      牌谱获取方式: businessInfo.牌谱获取方式,
      牌谱获取结果: businessInfo.牌谱获取结果,
      是否命中本地记录: businessInfo.是否命中本地记录,
      是否等待文件锁: businessInfo.是否等待文件锁,
      读取次数: readCount, 分块数: chunkCount, 总数据大小_KB: sizeKB(totalBytes),
      首分块接收时间_ms: roundMs(firstChunkAt), 末分块接收时间_ms: roundMs(lastChunkAt),
      首末分块间隔_ms: firstChunkAt !== null && lastChunkAt !== null ? roundMs(lastChunkAt - firstChunkAt) : null,
      首个分块接收时间_ms: roundMs(firstChunkAt),
      complete接收时间_ms: roundMs(completeAt),
      "100%至complete接收间隔_ms": progress100ToCompleteMs,
      分块计时口径: "浏览器 performance.now()，以读取完成时间为准",
      数据口径: "HTTP自动解压后的SSE数据，非传输压缩大小",
      读取等待总耗时_ms: roundMs(totalReadWaitMs),
      读取等待最大耗时_ms: roundMs(maxReadWaitMs),
      读取等待P95耗时_ms: roundMs(p95), 读取错误次数: readErrorCount, 错误总次数: errorCount,
      诊断错误次数: diagnosticErrorCount, 首分块: firstChunk, 末分块: lastChunk,
      可见状态: visibilityReport,
      监测: { 开始时间_ms: roundMs(monitorStarted), 结束时间_ms: roundMs(monitorEnded),
        时间原点_Unix_ms: roundMs(wallOrigin), 已启用长任务监测: longTaskMonitoring,
        范围: "从请求开始到结果解析完成", 计时口径: "浏览器未校准的 performance.now()" },
      长任务: longTaskReport,
      事件帧: { 进度100: frameSummary(progressFrame), complete: frameSummary(completeFrame),
        位于同一分块: typeof sse.sameChunk === "boolean" ? sse.sameChunk : null,
        complete事件解析完成时间_ms: roundMs(sse.completeEventParsedAt),
        complete结果解析完成时间_ms: roundMs(sse.completeParsedAt),
        complete事件JSON解析耗时_ms: roundMs(sse.completeJsonParseMs),
        complete首分块至事件解析完成耗时_ms: Number.isFinite(sse.completeFirstAt)
          ? roundMs(sse.completeEventParsedAt - sse.completeFirstAt) : null,
        complete读取等待耗时_ms: roundMs(completeReadWaitMs), complete分块间处理耗时_ms: roundMs(completeBetweenReadsMs) },
      结果传输: { 格式: Number.isFinite(sse.completeEventParsedAt) ? "原始JSON SSE" : null,
        压缩后数据大小_KB: null, Base64数据大小_KB: null, Base64解码耗时_ms: null,
        手动解压耗时_ms: null, 手动解压后数据大小_KB: null,
        JSON解析耗时_ms: roundMs(sse.completeJsonParseMs), 事件JSON解析耗时_ms: roundMs(sse.completeJsonParseMs),
        计时口径: "HTTP压缩由浏览器自动处理；原始complete事件与结果只解析一次，两个JSON耗时相同且不可相加" },
      尾部状态: tailStates[tailStatus] || "未知",
    };
    Object.defineProperty(summary, "页面切换完成耗时_ms", {
      enumerable: true,
      configurable: true,
      get: getPageSwitchDuration,
    });

    const logEntries = [["汇总", summary]];
    if (debugMode) {
      logEntries.push(["读取明细", limitDiagnosticArray(readSamples, 200)]);
    }
    safeLog("[Paipu SSE Browser] 浏览器SSE性能", logEntries);
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
  // HTTP Content-Encoding is decoded by fetch; do not negotiate an embedded encoding.
  delete requestBody.accept_compression;
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
        ...(isPagesProxyMode() && typeof window !== "undefined" && window.__PAIPU_DEBUG_PROXY__ === true
          ? { "X-Paipu-Proxy-Debug": "1" } : {}),
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
      const eventRequestId = ev.performance["请求ID"];
      if (validRequestId(eventRequestId)) {
        serverRequestId = eventRequestId;
        timings.serverRequestId = serverRequestId;
      }
      const rawStatus = ev.performance["状态"];
      if (rawStatus && typeof rawStatus === "object" && !Array.isArray(rawStatus)) {
        serverBusinessStatus = {
          牌谱获取方式: (typeof rawStatus["牌谱获取方式"] === "string" ? rawStatus["牌谱获取方式"] : null)
            ?? (typeof ev.performance["牌谱获取方式"] === "string" ? ev.performance["牌谱获取方式"] : null),
          牌谱获取结果: (typeof rawStatus["牌谱获取结果"] === "string" ? rawStatus["牌谱获取结果"] : null)
            ?? (typeof ev.performance["牌谱获取结果"] === "string" ? ev.performance["牌谱获取结果"] : null),
          是否命中本地记录: (typeof rawStatus["是否命中本地记录"] === "boolean" ? rawStatus["是否命中本地记录"] : null)
            ?? (typeof ev.performance["是否命中本地记录"] === "boolean" ? ev.performance["是否命中本地记录"] : null),
          是否等待文件锁: (typeof rawStatus["是否等待文件锁"] === "boolean" ? rawStatus["是否等待文件锁"] : null)
            ?? (typeof ev.performance["是否等待文件锁"] === "boolean" ? ev.performance["是否等待文件锁"] : null),
          牌谱获取HTTP状态码: (typeof rawStatus["牌谱获取HTTP状态码"] === "number" ? rawStatus["牌谱获取HTTP状态码"] : null)
            ?? (typeof ev.performance["牌谱获取HTTP状态码"] === "number" ? ev.performance["牌谱获取HTTP状态码"] : null),
        };
      }
      if (performanceLogged) return;
      performanceLogged = true;
      try {
        const report = {};
        for (const key of ["事件", "schema版本", "请求ID", "总耗时_ms", "耗时_ms", "数据大小_KB", "计数", "状态", "压缩比例", "计时口径", "当前进行中阶段"]) {
          const value = key === "请求ID" && !validRequestId(eventRequestId) ? null : ev.performance[key];
          report[key] = sanitizeDiagnostic(value, key);
        }
        safeLog("[Paipu Performance] 服务端性能", [["请求关联", {
          客户端请求ID: clientRequestId, 服务端请求ID: serverRequestId,
        }], ["性能统计", report]]);
      } catch (_) {
        // Even a malformed optional diagnostic must not affect the review.
        diagnosticErrorCount++;
        errorCount++;
      }
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
      const sameChunk = progressFrame && completeFrame ? progressFrame.lastChunk === completeFrame.firstChunk : null;
      timings.sse = {
        completeBytes: completeFrame?.bytes ?? null,
        completeFirstAt: first ?? null,
        completeLastAt: timings.completeReceivedAt,
        completeEventParsedAt: parsedAt,
        completeJsonParseMs: parsedAt - eventParseStarted,
        progressLastAt: progressAt ?? null,
        sameChunk,
        chunkCount,
        totalBytes,
      };
      completionEvent = ev;
    } else if (type === "error") {
      throw Object.assign(new Error(`服务器分析失败：${ev.detail || "分析发生错误"}`), { serverReported: true });
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
      const chunk = { 分块序号: chunkCount, 读取序号: readCount, 数据大小_KB: sizeKB(size), 接收时间_ms: roundMs(readFinished) };
      if (!firstChunk) {
        firstChunk = chunk;
        firstChunkAt = readFinished;
      }
      lastChunk = chunk;
      lastChunkAt = readFinished;
    }
    readSamples.push({ 序号: readCount, 开始时间_ms: roundMs(readStarted),
      结束时间_ms: roundMs(readFinished), 等待耗时_ms: roundMs(wait), 数据大小_KB: sizeKB(size),
      状态: failed ? "读取失败" : (packet.done ? "流结束" : "数据分块"), 可见状态: displayVisibility(currentVisibility),
      错误信息: failed ? safeReadError(failure) : null });
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

  delivered = false;
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
    const result = completionEvent.result;
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
