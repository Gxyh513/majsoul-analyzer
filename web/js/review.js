// ============================================================================
// Two-Tier Navigation: Switch between Landing and Review Screen
// ============================================================================
function showTier(tierNum) {
  const t1 = $("tier1Screen");
  const t2 = $("tier2Screen");
  if (tierNum === 1) {
    if (state.isPlaying) togglePlay(false);
    t1.classList.remove("hidden");
    t2.classList.add("hidden");
    const input = $("homePaipuInput");
    if (input) input.value = "";
    window.scrollTo(0, 0);
  } else {
    t1.classList.add("hidden");
    t2.classList.remove("hidden");
    updateEngineStatusDisplay();
    window.scrollTo(0, 0);
  }
}

const REVIEW_SCHEMA = "mortal-onnx-2f7ad48-v1";
const MODEL_REVISION = "2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a";
const MODEL_SHA256 = "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4";

function isTrustedReview(result) {
  const status = state.serverStatus?.engine;
  const engine = result?.engine;
  return Boolean(status?.has_mortal_weights === true && status.active_engine === "mortal" &&
    status.model_revision === MODEL_REVISION && status.weight_sha256 === MODEL_SHA256 &&
    status.review_schema === REVIEW_SCHEMA && engine?.type === "mortal" &&
    engine.id === "mortal-s42-onnx" && engine.weight_file === "model.onnx" &&
    engine.version === 4 && engine.steps === null && engine.revision === MODEL_REVISION &&
    engine.q_source === "neural_network" && engine.review_schema === REVIEW_SCHEMA &&
    engine.weight_sha256 === MODEL_SHA256);
}

async function requireModelStatus() {
  await refreshServerStatus();
  if (state.serverStatus?.engine?.has_mortal_weights !== true ||
      state.serverStatus.engine.active_engine !== "mortal" ||
      state.serverStatus.engine.weight_sha256 !== MODEL_SHA256 ||
      state.serverStatus.engine.model_revision !== MODEL_REVISION ||
      state.serverStatus.engine.review_schema !== REVIEW_SCHEMA) {
    throw new Error("无法验证 Mortal S42 ONNX，请检查模型状态和后端版本。");
  }
}

function requireTrustedReview(result) {
  if (!isTrustedReview(result)) throw new Error("分析结果的模型来源无法验证，请重新分析。");
}

// Only the latest request may update the visible review or its progress.
let analysisViewVersion = 0;
let currentReviewSource = null;
const reviewCache = new Map();
const pendingReviews = new Map();
const REVIEW_CACHE_SOURCE_LIMIT = 2;
const REVIEW_ENGINE = "auto";
const REVIEW_TEMPERATURE = 0.1;

function matchesReview(result, seat) {
  return isTrustedReview(result) && Number.isInteger(result.target_actor) &&
    (seat == null || result.target_actor === seat);
}

async function getReview(source, seat, onProgress = () => {}, timings = null) {
  const url = buildApiUrl(getBackendUrl(), "/api/analyze/stream");
  const body = source.kind === "json"
    ? { content: source.input, engine: REVIEW_ENGINE, temperature: REVIEW_TEMPERATURE }
    : { paipu: source.identity, engine: REVIEW_ENGINE, temperature: REVIEW_TEMPERATURE };
  const sourceKey = JSON.stringify([url, source.kind, body, "mortal-s42-onnx", MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA]);
  const cached = reviewCache.get(sourceKey);
  const cachedSeat = seat == null ? cached?.defaultSeat : seat;
  if (cachedSeat != null && cached?.seats.has(cachedSeat)) {
    reviewCache.delete(sourceKey);
    reviewCache.set(sourceKey, cached);
    onProgress(100, "已复用本页面的分析结果，正在加载回放...");
    return cached.seats.get(cachedSeat);
  }

  let requests = pendingReviews.get(sourceKey);
  if (!requests) {
    requests = new Map();
    pendingReviews.set(sourceKey, requests);
  }
  let pending = requests.get(seat);
  if (!pending) {
    pending = { listeners: new Set(), progress: null, promise: null, timings: {} };
    requests.set(seat, pending);
    const notify = (percent, message) => {
      pending.progress = [percent, message];
      for (const listener of pending.listeners) listener(percent, message);
    };
    pending.promise = Promise.resolve().then(async () => {
      notify(0, "正在验证 Mortal S42 ONNX 模型...");
      await requireModelStatus();
      if (url !== buildApiUrl(getBackendUrl(), "/api/analyze/stream")) {
        throw new Error("后端地址已发生变化，请重新分析。");
      }
      const requestBody = seat == null ? body : { ...body, target_actor: seat };
      const result = await fetchReviewStream(requestBody, notify, url, pending.timings);
      requireTrustedReview(result);
      if (!matchesReview(result, seat)) throw new Error("分析结果的玩家座位不匹配，请重试。");
      const entry = reviewCache.get(sourceKey) || { defaultSeat: null, seats: new Map() };
      entry.seats.set(result.target_actor, result);
      if (seat == null) entry.defaultSeat = result.target_actor;
      reviewCache.delete(sourceKey);
      reviewCache.set(sourceKey, entry);
      while (reviewCache.size > REVIEW_CACHE_SOURCE_LIMIT) {
        reviewCache.delete(reviewCache.keys().next().value);
      }
      return result;
    }).finally(() => {
      requests.delete(seat);
      if (!requests.size) pendingReviews.delete(sourceKey);
    });
  }
  pending.listeners.add(onProgress);
  if (pending.progress) onProgress(...pending.progress);
  try {
    const result = await pending.promise;
    if (timings) {
      timings.completeReceivedAt = pending.timings.completeReceivedAt;
      timings.progress100ReceivedAt = pending.timings.progress100ReceivedAt;
      timings.completeParsedAt = pending.timings.sse?.completeParsedAt;
      timings.requestId = pending.timings.requestId;
      timings.clientRequestId = pending.timings.clientRequestId;
      timings.serverRequestId = pending.timings.serverRequestId;
      // A trailing performance event can provide the server ID after result delivery.
      timings.streamTimings = pending.timings;
    }
    return result;
  } finally {
    pending.listeners.delete(onProgress);
  }
}

function logReviewViewCompletion(timings, version) {
  if (!Number.isFinite(timings.completeReceivedAt)) return;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (version !== analysisViewVersion) return;
    const now = performance.now();
    const rounded = (value) => Number.isFinite(value) ? Math.round(value * 1000) / 1000 : null;
    const progressAt = timings.progress100ReceivedAt;
    const completeAt = timings.completeReceivedAt;
    const stream = timings.streamTimings || timings;
    const pageSwitchDuration = Number.isFinite(completeAt) ? rounded(now - completeAt) : null;
    timings.viewCompletedAt = rounded(now);
    timings.pageSwitchDurationMs = pageSwitchDuration;
    if (stream && stream !== timings) {
      stream.viewCompletedAt = rounded(now);
      stream.pageSwitchDurationMs = pageSwitchDuration;
    }
    try {
      console.groupCollapsed("[Paipu UX Performance] 页面切换性能");
      try {
        console.log("页面切换统计", {
          事件: "页面切换性能统计", 版本: 1,
          客户端请求ID: stream.clientRequestId || timings.clientRequestId || timings.requestId || null,
          服务端请求ID: stream.serverRequestId || null,
          进度100接收时间_ms: rounded(progressAt), complete接收时间_ms: rounded(completeAt),
          complete解析完成时间_ms: rounded(timings.completeParsedAt), 页面切换完成时间_ms: rounded(now),
          complete事件JSON解析耗时_ms: rounded(stream.sse?.completeJsonParseMs),
          进度100至complete耗时_ms: Number.isFinite(progressAt) ? rounded(completeAt - progressAt) : null,
          complete至页面切换耗时_ms: rounded(now - completeAt),
          进度100至页面切换耗时_ms: Number.isFinite(progressAt) ? rounded(now - progressAt) : null,
          范围: "页面更新后的第二个动画帧", 计时口径: "浏览器 performance.now()",
        });
      } finally {
        console.groupEnd();
      }
    } catch (_) {}
  }));
}

function setPerspectiveLoading(title) {
  setLoading(true, title);
  const overlay = $("loadingOverlay");
  if (overlay) {
    overlay.style.pointerEvents = "none";
    overlay.style.background = "transparent";
    overlay.style.backdropFilter = "none";
    overlay.style.webkitBackdropFilter = "none";
    overlay.style.alignItems = "flex-end";
    overlay.style.justifyContent = "flex-start";
  }
}

function applyPerspective(result, preservePosition) {
  const round = preservePosition ? state.kyokuIdx : 0;
  const step = preservePosition ? state.stepIdx : 0;
  if (state.isPlaying) togglePlay(false);
  cancelScheduledStep();
  state.reviewData = result;
  const isDemo = currentReviewSource.kind === "paipu" &&
    (currentReviewSource.input === "demo" || currentReviewSource.input.startsWith("demo-"));
  $("demoReviewLabel")?.classList.toggle("hidden", !isDemo);
  state.gameId = result.game_id || currentReviewSource.identity;
  state.targetActor = result.target_actor;
  const index = Math.min(round, Math.max(0, (result.kyokus?.length || 1) - 1));
  renderSummaryDashboard();
  selectKyoku(index, !preservePosition);
  if (preservePosition) setStep(Math.min(step, Math.max(0, (result.kyokus?.[index]?.steps?.length || 1) - 1)));
  if ($("roundModal") && !$("roundModal").classList.contains("hidden")) renderKyokuTable();
}

async function startAnalysisFromInput(rawInput, seat = null) {
  const originalInput = rawInput || "";
  const input = originalInput.trim();
  if (!input) {
    $("homeInputError")?.classList.remove("hidden");
    $("homePaipuInput")?.focus();
    return;
  }
  $("homeInputError")?.classList.add("hidden");
  const isJson = input.startsWith("{") || input.startsWith("[");
  const source = { kind: isJson ? "json" : "paipu", input: isJson ? originalInput : input,
    identity: isJson ? input : parsePaipuUuid(input) };
  const version = ++analysisViewVersion;
  updateEngineStatusDisplay();
  setLoading(true, "正在分析牌谱...", "正在使用 Mortal S42 ONNX 神经网络检讨每一手决策");
  const timings = {};
  try {
    const result = await getReview(source, seat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    }, timings);
    if (version !== analysisViewVersion) return;
    currentReviewSource = source;
    applyPerspective(result, false);
    showTier(2);
    const newUrl = new URL(window.location.href);
    if (isJson) newUrl.searchParams.delete("paipu");
    else newUrl.searchParams.set("paipu", source.identity);
    window.history.pushState(null, "", newUrl.toString());
    showToast(`分析完成：共检讨 ${result.kyokus?.length || 0} 局`, "success");
    setLoading(false);
    logReviewViewCompletion(timings, version);
  } catch (err) {
    if (version !== analysisViewVersion) return;
    showAnalysisError(`分析失败：${err.message}`, () => startAnalysisFromInput(rawInput, seat));
  }
}

async function switchSeatPerspective(newSeat) {
  if (!state.reviewData || !currentReviewSource) return;
  const version = ++analysisViewVersion;
  const source = currentReviewSource;
  if (newSeat === state.targetActor) {
    setLoading(false);
    return;
  }
  const seatName = getPlayerDisplayName(newSeat);
  setPerspectiveLoading(`正在分析 ${seatName} 的 AI 检讨`);
  const timings = {};
  try {
    const result = await getReview(source, newSeat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    }, timings);
    if (version !== analysisViewVersion || currentReviewSource !== source) return;
    applyPerspective(result, true);
    setLoading(false);
    logReviewViewCompletion(timings, version);
  } catch (err) {
    if (version !== analysisViewVersion || currentReviewSource !== source) return;
    setLoading(false);
    showAnalysisError(`切换视角失败：${err.message}`, () => switchSeatPerspective(newSeat));
  }
}
