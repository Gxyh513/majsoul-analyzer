// ============================================================================
// Two-Tier Navigation: Switch between Landing and Review Screen
// ============================================================================
function showTier(tierNum) {
  const t1 = $("tier1Screen");
  const t2 = $("tier2Screen");
  if (tierNum === 1) {
    if (state.isPlaying) togglePlay(false);
    state.selectedModel = "akagi";
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

const REVIEW_SCHEMA = "mortal-582500-only-v4";

function isTrustedReview(result) {
  const status = state.serverStatus?.engine;
  const engine = result?.engine;
  return Boolean(status?.has_mortal_weights && status.active_engine === "mortal" &&
    status.weight_sha256 && engine?.type === "mortal" && engine.id === "akagi" &&
    engine.weight_file === "mortal_582500.pth" && engine.version === 4 &&
    engine.steps === 582500 && engine.q_source === "neural_network" &&
    engine.review_schema === REVIEW_SCHEMA && engine.weight_sha256 === status.weight_sha256);
}

async function requireModelStatus() {
  await refreshServerStatus();
  if (!state.serverStatus?.engine?.has_mortal_weights ||
      state.serverStatus.engine.active_engine !== "mortal" ||
      !state.serverStatus.engine.weight_sha256 ||
      state.serverStatus.engine.review_schema !== REVIEW_SCHEMA) {
    throw new Error("无法验证 mortal_582500.pth，请启动已更新的后端并检查模型状态。");
  }
}

function requireTrustedReview(result) {
  if (!isTrustedReview(result)) throw new Error("分析结果的模型来源无法验证，请重新分析。");
}

// Only the latest request may update the visible review or its progress.
let analysisViewVersion = 0;
let currentReviewSource = null;

function matchesReview(result, seat) {
  return isTrustedReview(result) && Number.isInteger(result.target_actor) &&
    (seat == null || result.target_actor === seat);
}

async function getReview(source, model, seat, onProgress) {
  await requireModelStatus();
  const body = source.kind === "json"
    ? { content: source.input, engine: model }
    : { paipu: source.identity, engine: model };
  if (seat != null) body.target_actor = seat;
  let result;
  try {
    result = await fetchReviewStream(body, onProgress);
  } catch (streamErr) {
    if (streamErr.serverReported) throw streamErr;
    console.warn("SSE stream failed, falling back to analysis API:", streamErr);
    const endpoint = source.kind === "json" ? "/api/analyze/json" : "/api/analyze/paipu";
    result = await apiFetch(endpoint, { method: "POST", body: JSON.stringify(body) });
  }
  requireTrustedReview(result);
  if (!matchesReview(result, seat)) throw new Error("分析结果的玩家座位不匹配，请重试。");
  return result;
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

async function startAnalysisFromInput(rawInput, model = "akagi", seat = null) {
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
  state.selectedModel = model;
  updateEngineStatusDisplay(model);
  setLoading(true, "正在分析牌谱...", "正在使用 Mortal-V4-582500 神经网络检讨每一手决策");
  try {
    const result = await getReview(source, model, seat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    });
    if (version !== analysisViewVersion || state.selectedModel !== model) return;
    currentReviewSource = source;
    applyPerspective(result, false);
    showTier(2);
    const newUrl = new URL(window.location.href);
    if (isJson) newUrl.searchParams.delete("paipu");
    else newUrl.searchParams.set("paipu", source.identity);
    window.history.pushState(null, "", newUrl.toString());
    showToast(`分析完成：共检讨 ${result.kyokus?.length || 0} 局`, "success");
    setLoading(false);
  } catch (err) {
    if (version !== analysisViewVersion || state.selectedModel !== model) return;
    showAnalysisError(`分析失败：${err.message}`, () => startAnalysisFromInput(rawInput, model, seat));
  }
}

async function switchSeatPerspective(newSeat) {
  if (!state.reviewData || !currentReviewSource) return;
  const version = ++analysisViewVersion;
  const source = currentReviewSource;
  const model = state.selectedModel;
  if (newSeat === state.targetActor) {
    setLoading(false);
    return;
  }
  const seatName = getPlayerDisplayName(newSeat);
  setPerspectiveLoading(`正在分析 ${seatName} 的 AI 检讨`);
  try {
    const result = await getReview(source, model, newSeat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    });
    if (version !== analysisViewVersion || currentReviewSource !== source || state.selectedModel !== model) return;
    applyPerspective(result, true);
    setLoading(false);
  } catch (err) {
    if (version !== analysisViewVersion || currentReviewSource !== source || state.selectedModel !== model) return;
    setLoading(false);
    showAnalysisError(`切换视角失败：${err.message}`, () => switchSeatPerspective(newSeat));
  }
}
