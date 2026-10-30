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

function matchesReview(result, seat) {
  return isTrustedReview(result) && Number.isInteger(result.target_actor) &&
    (seat == null || result.target_actor === seat);
}

async function getReview(source, seat, onProgress) {
  await requireModelStatus();
  const body = source.kind === "json"
    ? { content: source.input }
    : { paipu: source.identity };
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
  try {
    const result = await getReview(source, seat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    });
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
  try {
    const result = await getReview(source, newSeat, (pct, msg) => {
      if (version === analysisViewVersion) updateAnalysisProgress(pct, msg);
    });
    if (version !== analysisViewVersion || currentReviewSource !== source) return;
    applyPerspective(result, true);
    setLoading(false);
  } catch (err) {
    if (version !== analysisViewVersion || currentReviewSource !== source) return;
    setLoading(false);
    showAnalysisError(`切换视角失败：${err.message}`, () => switchSeatPerspective(newSeat));
  }
}
