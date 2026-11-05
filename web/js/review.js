// ============================================================================
// Two-Tier Navigation: Switch between Landing and Review Screen
// ============================================================================
function showTier(tierNum) {
  const t1 = $("tier1Screen");
  const t2 = $("tier2Screen");
  if (tierNum === 1) {
    if (typeof cancelScheduledStep === "function") cancelScheduledStep();
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

const MODEL_STATUS_MAX_AGE_MS = 30000;

function isTrustedModelStatus(status) {
  const engine = status?.engine;
  return Boolean(engine?.has_mortal_weights === true && engine.active_engine === "mortal" &&
    engine.model_revision === MODEL_REVISION && engine.weight_sha256 === MODEL_SHA256 &&
    engine.review_schema === REVIEW_SCHEMA);
}

function isTrustedReview(result) {
  const engine = result?.engine;
  return Boolean(isTrustedModelStatus(state.serverStatus) && engine?.type === "mortal" &&
    engine.id === "mortal-s42-onnx" && engine.weight_file === "model.onnx" &&
    engine.version === 4 && engine.steps === null && engine.revision === MODEL_REVISION &&
    engine.q_source === "neural_network" && engine.review_schema === REVIEW_SCHEMA &&
    engine.weight_sha256 === MODEL_SHA256);
}

async function requireModelStatus() {
  const status = await refreshServerStatus({
    maxAgeMs: isTrustedModelStatus(state.serverStatus) ? MODEL_STATUS_MAX_AGE_MS : 0,
    revalidate: true,
  });
  if (!isTrustedModelStatus(status)) {
    throw new Error(status?.timedOut ? "模型状态检查超时，请稍后重试。" : "无法验证 Mortal S42 ONNX，请检查模型状态和后端版本。");
  }
}

function requireTrustedReview(result) {
  if (!isTrustedReview(result)) throw new Error("分析结果的模型来源无法验证，请重新分析。");
}

// Only the latest request may update the visible review or its progress.
let analysisViewVersion = 0;
let currentReviewSource = null;
let activeViewController = null;

function beginViewRequest() {
  if (activeViewController) activeViewController.abort();
  activeViewController = new AbortController();
  return activeViewController;
}

function finishViewRequest(controller) {
  if (activeViewController === controller) activeViewController = null;
  // Also detach the subscriber if replay asset loading failed first.
  controller.abort();
}

function cancelAnalysisWait(notify = true) {
  const controller = activeViewController;
  ++analysisViewVersion;
  activeViewController = null;
  controller?.abort();
  setLoading(false);
  if (controller && notify) {
    showToast("已取消等待。服务器已开始的推理可能仍会完成，不会自动重试。", "info");
  }
}

const reviewCache = new Map();
const pendingReviews = new Map();
const REVIEW_CACHE_SOURCE_LIMIT = 2;
const REVIEW_CACHE_ESTIMATED_BUDGET_BYTES = 32 * 1024 * 1024;

// Approximate expanded JS object cost without stringifying several megabytes of
// data just to measure it. The active result is never evicted by itself.
function estimateReviewFootprint(result) {
  let bytes = 64 * 1024;
  for (const round of result?.kyokus || []) {
    bytes += (round.steps?.length || 0) * 5000;
    bytes += (round.decisions?.length || 0) * 2800;
  }
  return bytes;
}

function trimReviewCache() {
  const totalBytes = () => [...reviewCache.values()].reduce((sum, entry) =>
    sum + [...entry.sizes.values()].reduce((a, b) => a + b, 0), 0);
  while (reviewCache.size > REVIEW_CACHE_SOURCE_LIMIT || totalBytes() > REVIEW_CACHE_ESTIMATED_BUDGET_BYTES) {
    const sourceKey = reviewCache.keys().next().value;
    if (sourceKey === undefined) break;
    const entry = reviewCache.get(sourceKey);
    if (reviewCache.size === 1 && entry.seats.size <= 1) break;
    const oldestSeat = entry.seats.keys().next().value;
    entry.seats.delete(oldestSeat);
    entry.sizes.delete(oldestSeat);
    if (entry.defaultSeat === oldestSeat) entry.defaultSeat = null;
    if (!entry.seats.size) reviewCache.delete(sourceKey);
  }
}
const REVIEW_ENGINE = "auto";
const REVIEW_TEMPERATURE = 0.1;

function matchesReview(result, seat) {
  return isTrustedReview(result) && Number.isInteger(result.target_actor) &&
    (seat == null || result.target_actor === seat);
}

async function getReview(source, seat, onProgress = () => {}, timings = null, signal = null) {
  if (signal?.aborted) throw new DOMException("分析请求已取消", "AbortError");
  const url = buildApiUrl(getBackendUrl(), "/api/analyze/stream");
  const body = { paipu: source.identity, engine: REVIEW_ENGINE, temperature: REVIEW_TEMPERATURE, transport_version: 2 };
  const sourceKey = JSON.stringify([url, source.kind, body, "mortal-s42-onnx", MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA]);
  const cached = reviewCache.get(sourceKey);
  const cachedSeat = seat == null ? cached?.defaultSeat : seat;
  if (cachedSeat != null && cached?.seats.has(cachedSeat)) {
    const reused = cached.seats.get(cachedSeat);
    cached.seats.delete(cachedSeat);
    cached.seats.set(cachedSeat, reused);
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
    pending = { listeners: new Set(), progress: null, promise: null, timings: {}, controller: new AbortController(), settled: false };
    requests.set(seat, pending);
    const notify = (percent, message) => {
      pending.progress = [percent, message];
      for (const listener of pending.listeners) listener(percent, message);
    };
    pending.promise = Promise.resolve().then(async () => {
      notify(0, "正在验证 Mortal S42 ONNX 模型...");
      // Loading the stream reader does not need to wait for model attestation.
      // Both must still succeed before the analysis request is sent.
      await Promise.all([requireModelStatus(), loadReviewStreamClient()]);
      if (url !== buildApiUrl(getBackendUrl(), "/api/analyze/stream")) {
        throw new Error("后端地址已发生变化，请重新分析。");
      }
      if (pending.controller.signal.aborted) throw new DOMException("分析请求已取消", "AbortError");
      const requestBody = seat == null ? body : { ...body, target_actor: seat };
      const result = await fetchReviewStream(requestBody, notify, url, pending.timings, pending.controller.signal);
      if (pending.controller.signal.aborted) throw new DOMException("分析请求已取消", "AbortError");
      requireTrustedReview(result);
      if (!matchesReview(result, seat)) throw new Error("分析结果的玩家座位不匹配，请重试。");
      const entry = reviewCache.get(sourceKey) || { defaultSeat: null, seats: new Map(), sizes: new Map() };
      entry.seats.delete(result.target_actor);
      entry.seats.set(result.target_actor, result);
      entry.sizes.set(result.target_actor, estimateReviewFootprint(result));
      if (seat == null) entry.defaultSeat = result.target_actor;
      reviewCache.delete(sourceKey);
      reviewCache.set(sourceKey, entry);
      trimReviewCache();
      return result;
    }).finally(() => {
      pending.settled = true;
      if (requests.get(seat) === pending) requests.delete(seat);
      if (!requests.size && pendingReviews.get(sourceKey) === requests) pendingReviews.delete(sourceKey);
    });
    pending.promise.catch(() => {});
  }
  pending.listeners.add(onProgress);
  const detachSubscriber = () => {
    pending.listeners.delete(onProgress);
    if (!pending.listeners.size && !pending.settled) {
      // Detach immediately, before another view can subscribe to an aborted job.
      // An already-running inference still owns its server-side permit.
      pending.controller.abort();
      if (requests.get(seat) === pending) requests.delete(seat);
      if (!requests.size && pendingReviews.get(sourceKey) === requests) pendingReviews.delete(sourceKey);
    }
  };
  let onAbort = null;
  const aborted = signal ? new Promise((_, reject) => {
    onAbort = () => {
      detachSubscriber();
      reject(new DOMException("分析请求已取消", "AbortError"));
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }) : null;
  if (pending.progress) onProgress(...pending.progress);
  try {
    const result = await (aborted ? Promise.race([pending.promise, aborted]) : pending.promise);
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
    if (signal && onAbort) signal.removeEventListener("abort", onAbort);
    detachSubscriber();
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

// Different seats can insert different riichi-decision snapshots.
// Preserve the underlying MJAI event rather than a seat-specific array index.
function findPerspectiveStep(steps, eventIndex, fallbackStep) {
  const fallback = Math.min(fallbackStep, Math.max(0, (steps?.length || 1) - 1));
  if (!Number.isInteger(eventIndex) || !Array.isArray(steps) || !steps.length) return fallback;
  let nearest = 0;
  let found = false;
  for (let i = 0; i < steps.length; i++) {
    const idx = steps[i]?.event_idx;
    if (!Number.isInteger(idx)) continue;
    if (idx > eventIndex) break;
    nearest = i;
    found = true;
  }
  return found ? nearest : fallback;
}

function applyPerspective(result, preservePosition) {
  const round = preservePosition ? state.kyokuIdx : 0;
  const step = preservePosition ? state.stepIdx : 0;
  const sourceEventIndex = preservePosition
    ? state.reviewData?.kyokus?.[round]?.steps?.[step]?.event_idx
    : null;
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
  if (preservePosition) setStep(findPerspectiveStep(result.kyokus?.[index]?.steps, sourceEventIndex, step));
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
  if (input.startsWith("{") || input.startsWith("[")) {
    showToast("已停止支持 JSON 上传，请粘贴雀魂牌谱链接或牌谱号。", "error");
    return;
  }
  const source = { kind: "paipu", input, identity: parsePaipuUuid(input) };
  const viewController = beginViewRequest();
  const version = ++analysisViewVersion;
  updateEngineStatusDisplay();
  setLoading(true, "正在分析牌谱...", "正在使用 Mortal S42 ONNX 神经网络检讨每一手决策");
  const timings = {};
  try {
    // Start replay downloads alongside status/stream preparation and analysis.
    // Promise.all observes both failures, even if the other branch rejects first.
    // Assets must be ready before any result is applied to the visible replay.
    const [result] = await Promise.all([
      getReview(source, seat, (pct, msg) => {
        if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
      }, timings, viewController.signal),
      loadReplayAssets(),
    ]);
    if (version !== analysisViewVersion || viewController.signal.aborted) return;
    currentReviewSource = source;
    applyPerspective(result, false);
    showTier(2);
    // The initial analysis completed while the replay was hidden, so recompute
    // recommendation bar geometry after the board has entered layout.
    requestAnimationFrame(() => {
      if (version === analysisViewVersion && state.reviewData === result) setStep(state.stepIdx, { renderOnly: true });
    });
    // Review navigation stays in memory; refreshing must not reopen a paipu.
    showToast(`分析完成：共检讨 ${result.kyokus?.length || 0} 局`, "success");
    setLoading(false);
    logReviewViewCompletion(timings, version);
  } catch (err) {
    if (version !== analysisViewVersion || viewController.signal.aborted || err?.name === "AbortError") return;
    showAnalysisError(`分析失败：${err.message}`, () => {
      if (version === analysisViewVersion) startAnalysisFromInput(rawInput, seat);
    });
  } finally {
    finishViewRequest(viewController);
  }
}

async function switchSeatPerspective(newSeat) {
  if (!state.reviewData || !currentReviewSource) return;
  if (newSeat === state.targetActor) {
    cancelAnalysisWait(false);
    return;
  }
  const source = currentReviewSource;
  const viewController = beginViewRequest();
  const version = ++analysisViewVersion;
  const seatName = getPlayerDisplayName(newSeat);
  setPerspectiveLoading(`正在分析 ${seatName} 的 AI 检讨`);
  const timings = {};
  try {
    const result = await getReview(source, newSeat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    }, timings, viewController.signal);
    if (version !== analysisViewVersion || currentReviewSource !== source || viewController.signal.aborted) return;
    applyPerspective(result, true);
    setLoading(false);
    logReviewViewCompletion(timings, version);
  } catch (err) {
    if (version !== analysisViewVersion || currentReviewSource !== source || viewController.signal.aborted || err?.name === "AbortError") return;
    setLoading(false);
    showAnalysisError(`切换视角失败：${err.message}`, () => {
      if (version === analysisViewVersion && currentReviewSource === source) switchSeatPerspective(newSeat);
    });
  } finally {
    finishViewRequest(viewController);
  }
}
