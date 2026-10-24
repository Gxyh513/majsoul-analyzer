/**
 * 雀魂牌谱 AI 检讨系统 — NAGA / Mortal 风格前端控制器
 * v2.1 UI: the table stays; the side sheet says in one sentence where you differed.
 */

const state = {
  reviewData: null,        // Full response from /api/analyze/*
  gameId: "demo",          // Current game_id or paipu UUID
  targetActor: 0,          // Current reviewed player seat (0..3)
  kyokuIdx: 0,             // Current kyoku index
  stepIdx: 0,              // Current replay step index within kyoku
  openHands: true,         // Show opponent hands face-up
  decisionFilter: "all",   // "all" | "issues"
  openCandLabel: null,     // Expanded candidate row (ukeire breakdown)
  serverStatus: null,
};

const STAGE_W = 720;
const STAGE_H = 870;

const WIND_CN = ["东", "南", "西", "北"];
const TILE_CN_MAP = {
  E: "东", S: "南", W: "西", N: "北", P: "白", F: "发", C: "中",
  "5mr": "赤五万", "5pr": "赤五筒", "5sr": "赤五索",
};
const VERDICT_SHORT = { best: "一致", ok: "微差", question: "疑问", blunder: "恶手" };

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function tileCn(pai) {
  if (!pai) return "";
  if (TILE_CN_MAP[pai]) return TILE_CN_MAP[pai];
  const numMap = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"];
  const suitMap = { m: "万", p: "筒", s: "索" };
  if (pai.length === 2 && suitMap[pai[1]]) {
    return `${numMap[parseInt(pai[0], 10)] || pai[0]}${suitMap[pai[1]]}`;
  }
  return pai;
}

function shantenText(s) {
  if (s === undefined || s === null) return "-";
  if (s < 0) return "和牌";
  if (s === 0) return "听牌";
  return `${s} 向听`;
}

const VERDICT_NOTE = { best: "和 AI 一样", ok: "差得不多", question: "值得再看", blunder: "差很多" };

function decisionSentence(dec) {
  const discard = (ev) => ev?.type === "dahai" && ev.pai;
  if (discard(dec.actual) && discard(dec.expected)) {
    const you = tileCn(dec.actual.pai);
    const ai = tileCn(dec.expected.pai);
    if (dec.is_equal) return `这一手和 AI 一样，打了${you}。`;
    return `你打了${you}，AI 更想打${ai}。`;
  }
  const you = String(dec.actual_cn || "").replace(" (Pass)", "");
  const ai = String(dec.expected_cn || "").replace(" (Pass)", "");
  if (dec.is_equal) return `这一手和 AI 一样：${you}。`;
  return `你选了${you}，AI 更建议${ai}。`;
}

function fmtPct(v, digits = 1) {
  return `${(v ?? 0).toFixed(digits)}%`;
}

// Render an SVG mahjong tile HTML string
function renderTileHtml(pai, sizeClass = "t-opp", extraClass = "", title = "") {
  if (!pai || pai === "?") {
    const face = sizeClass === "t-dora" ? "tile-slot" : "tile-back";
    return `<div class="mj-tile ${face} ${sizeClass} ${extraClass}"></div>`;
  }
  const tip = escapeHtml(title || tileCn(pai));
  return `<div class="mj-tile ${sizeClass} ${extraClass}" title="${tip}" data-pai="${escapeHtml(pai)}"><img src="/tiles/${encodeURIComponent(pai)}.svg" alt="${tip}" draggable="false" /></div>`;
}

// Toast helper
function showToast(msg, type = "info") {
  const container = $("toastContainer");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    setTimeout(() => el.remove(), 300);
  }, type === "error" ? 6000 : 3200);
}

function setLoading(visible, title = "正在分析牌谱...", sub = "") {
  $("loadingTitle").textContent = title;
  $("loadingSub").textContent = sub;
  $("loadingOverlay").classList.toggle("hidden", !visible);
}

function currentEngine() {
  return $("engineSelect").value;
}

// ============================================================================
// API Calls
// ============================================================================
async function apiFetch(url, options = {}) {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || `请求失败 (${resp.status})`);
  }
  return data;
}

async function refreshServerStatus() {
  try {
    const st = await apiFetch("/api/status");
    state.serverStatus = st;
    updateTopbarStatus(st);
    renderSavedHistoryList(st.history || []);
  } catch (err) {
    console.error("Failed to fetch status:", err);
    showToast("无法连接本地服务，请确认程序正在运行", "error");
  }
}

function updateTopbarStatus(st) {
  const dot = $("msStatusDot");
  const txt = $("msStatusText");
  const banner = $("modalAccountStatusBanner");
  const ms = st.majsoul || {};

  if (ms.logged_in) {
    dot.className = "status-dot online";
    txt.textContent = ms.nickname || "已登录";
    banner.className = "banner ok";
    banner.textContent = `已登录：${ms.nickname || "未知昵称"}（ID ${ms.account_id || "-"}）`;
  } else if (ms.has_saved_token || ms.saved_account) {
    dot.className = "status-dot saved";
    txt.textContent = ms.nickname || "已保存账号";
    banner.className = "banner warn";
    banner.textContent = `已保存登录凭证（${ms.saved_account || "Token"}），分析在线牌谱时会自动登录。`;
  } else {
    dot.className = "status-dot offline";
    txt.textContent = "登录雀魂";
    banner.className = "banner";
    banner.textContent = "未登录。你仍然可以使用示例牌谱或导入牌谱文件。";
  }

  const eng = st.engine || {};
  $("engineStatusText").textContent = eng.has_mortal_weights ? "神经网络" : "牌效";

  $("engineModalDetail").innerHTML = `
    <div><span class="k">现在用的</span>${escapeHtml(eng.model_tag || "-")}</div>
    <div><span class="k">神经网络</span>${
      eng.has_mortal_weights
        ? `<span class="good">已载入（${escapeHtml(eng.model_path)}）</span>`
        : `<span class="warn">没找到权重文件，所以用牌效和防守</span>`
    }</div>
    <div><span class="k">跑在</span>${escapeHtml(eng.device || "CPU")}</div>
    ${eng.load_error ? `<div><span class="k">没载入的原因</span><span class="bad">${escapeHtml(eng.load_error)}</span></div>` : ""}
  `;
}

async function runAnalysis(promise, { keepKyoku = 0, successMsg = null } = {}) {
  try {
    const res = await promise;
    applyReviewResult(res, keepKyoku);
    if (successMsg) showToast(successMsg(res), "success");
    return res;
  } catch (err) {
    showToast(err.message, "error");
    return null;
  } finally {
    setLoading(false);
  }
}

async function loadDemoGame(targetActor = 0) {
  setLoading(true, "正在加载示例牌谱...", "首次分析约需数秒");
  const res = await runAnalysis(
    apiFetch("/api/analyze/demo", {
      method: "POST",
      body: JSON.stringify({ target_actor: targetActor, engine: currentEngine(), temperature: 0.1 }),
    })
  );
  if (res) $("paipuInput").value = "";
}

async function analyzePaipuInput(targetActor = null) {
  const rawInput = $("paipuInput").value.trim();
  if (!rawInput) {
    showToast("请先粘贴牌谱链接或牌谱 ID", "error");
    $("paipuInput").focus();
    return;
  }
  setLoading(true, "正在获取并分析牌谱...", rawInput);
  const res = await runAnalysis(
    apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify({ paipu: rawInput, target_actor: targetActor, engine: currentEngine(), temperature: 0.1 }),
    }),
    { successMsg: (r) => `分析完成（视角：${r.player_name}）` }
  );
  if (res) refreshServerStatus();
}

async function reanalyzeCurrent(newSeat, keepKyoku) {
  if (!state.reviewData) return;
  return runAnalysis(
    apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify({ paipu: state.gameId || "demo", target_actor: newSeat, engine: currentEngine(), temperature: 0.1 }),
    }),
    { keepKyoku }
  );
}

async function switchSeatPerspective(newSeat) {
  if (!state.reviewData || newSeat === state.targetActor) return;
  setLoading(true, `切换到「${state.reviewData.names[newSeat]}」的视角...`);
  await reanalyzeCurrent(newSeat, state.kyokuIdx);
}

// ============================================================================
// Apply Review Result & Render Dashboard
// ============================================================================
function applyReviewResult(res, preserveKyokuIdx = 0) {
  state.reviewData = res;
  state.gameId = res.game_id || "demo";
  state.targetActor = res.target_actor;
  state.kyokuIdx = Math.min(preserveKyokuIdx, Math.max(0, (res.kyokus?.length || 1) - 1));

  renderSummaryDashboard();
  selectKyoku(state.kyokuIdx, true);
}

function renderSummaryDashboard() {
  const res = state.reviewData;
  if (!res) return;

  // Game title
  const isDemo = String(state.gameId).startsWith("demo");
  const room = res.meta?.room || "";
  $("gameTitle").innerHTML = `${isDemo ? `<span class="demo-badge">示例</span>` : ""}${escapeHtml(room || state.gameId)}`;
  $("gameTitle").title = room ? `${room} · ${state.gameId}` : state.gameId;

  // 1. Seat switcher
  const seatTabs = $("seatTabs");
  seatTabs.innerHTML = res.names
    .map((name, seat) => `
      <button class="seg ${seat === res.target_actor ? "active" : ""}" data-seat="${seat}" role="tab" title="${escapeHtml(name)}">
        <span class="seg-wind">${WIND_CN[seat]}起</span>
        <span class="seg-name">${escapeHtml(name)}</span>
      </button>`)
    .join("");
  seatTabs.querySelectorAll(".seg").forEach((btn) => {
    btn.addEventListener("click", () => switchSeatPerspective(parseInt(btn.dataset.seat, 10)));
  });

  // 2. Overview
  const sum = res.summary || {};
  $("metricRating").textContent = (sum.rating ?? 0).toFixed(1);
  const grade = sum.grade || "-";
  const gradeEl = $("metricGrade");
  gradeEl.textContent = grade;
  gradeEl.className = `grade-badge grade-${grade[0]}`;
  $("metricMatchRate").textContent = fmtPct(sum.match_rate);
  $("metricMatchCount").textContent = `${sum.total_matches || 0} / ${sum.total_reviewed || 0} 手`;
  $("metricMatchBar").style.width = `${Math.min(100, sum.match_rate || 0)}%`;
  $("metricBlunders").textContent = sum.blunders || 0;
  $("metricInaccuracies").textContent = sum.inaccuracies || 0;
  $("metricEngineTag").textContent = res.engine?.type === "mortal"
    ? "这次用神经网络做的判断"
    : "这次用牌效和防守做的判断";

  // 3. Kyoku pills
  renderKyokuPills();
}

function rateClass(rate) {
  if (rate >= 80) return "rate-good";
  if (rate >= 60) return "rate-mid";
  return "rate-bad";
}

function renderKyokuPills() {
  const res = state.reviewData;
  const container = $("kyokuPills");
  if (!res || !res.kyokus) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = res.kyokus
    .map((k, idx) => {
      const rate = k.stats?.match_rate ?? 100;
      const blunders = k.stats?.blunders || 0;
      const endSum = (k.end_summaries || []).join(" · ");
      const tip = `${k.label}${k.honba ? ` ${k.honba}本场` : ""} · 一致率 ${rate}%${blunders ? ` · ${blunders} 处恶手` : ""}${endSum ? `\n结果：${endSum}` : ""}`;
      return `
        <button class="kyoku-pill ${idx === state.kyokuIdx ? "active" : ""}" data-kidx="${idx}" title="${escapeHtml(tip)}">
          <span>${escapeHtml(k.label)}${k.honba ? `<small>·${k.honba}</small>` : ""}</span>
          <span class="kyoku-rate ${rateClass(rate)}">${Math.round(rate)}%</span>
          ${blunders ? `<span class="kyoku-err">${blunders}</span>` : ""}
        </button>`;
    })
    .join("");
  container.querySelectorAll(".kyoku-pill").forEach((btn) => {
    btn.addEventListener("click", () => selectKyoku(parseInt(btn.dataset.kidx, 10), true));
  });
  container.querySelector(".kyoku-pill.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function selectKyoku(kIdx, jumpToFirstDecision = false) {
  const res = state.reviewData;
  if (!res || !res.kyokus[kIdx]) return;
  state.kyokuIdx = kIdx;
  renderKyokuPills();

  const kyoku = res.kyokus[kIdx];
  $("stepSlider").max = Math.max(0, kyoku.steps.length - 1);
  renderTimelineTicks(kyoku);
  renderKyokuDecisionList(kyoku);

  if (jumpToFirstDecision && kyoku.decisions && kyoku.decisions.length > 0) {
    setStep(kyoku.decisions[0].step_idx);
  } else {
    setStep(0);
  }
}

function renderTimelineTicks(kyoku) {
  const ticksEl = $("timelineTicks");
  const totalSteps = Math.max(1, kyoku.steps.length - 1);
  ticksEl.innerHTML = (kyoku.decisions || [])
    .map((d) => {
      const leftPct = ((d.step_idx / totalSteps) * 100).toFixed(2);
      const tip = `第${d.junme}巡 · ${d.verdict_cn}（AI：${d.expected_cn} / 实战：${d.actual_cn}）`;
      return `<div class="tick ${d.verdict}" style="left:${leftPct}%" data-step="${d.step_idx}" title="${escapeHtml(tip)}"></div>`;
    })
    .join("");
  ticksEl.querySelectorAll(".tick").forEach((el) => {
    el.addEventListener("click", () => setStep(parseInt(el.dataset.step, 10)));
  });
}

// ============================================================================
// Step Navigation & 4-Player Board Rendering
// ============================================================================
function setStep(stepIdx) {
  const res = state.reviewData;
  if (!res) return;
  const kyoku = res.kyokus[state.kyokuIdx];
  if (!kyoku || !kyoku.steps.length) return;

  const clamped = Math.max(0, Math.min(kyoku.steps.length - 1, stepIdx));
  const changed = clamped !== state.stepIdx;
  state.stepIdx = clamped;

  $("stepSlider").value = clamped;
  $("stepCounterText").textContent = `${clamped} / ${kyoku.steps.length - 1}`;

  const snap = kyoku.steps[clamped];
  const isExactDecisionStep = snap.decision_idx !== null && snap.decision_idx !== undefined;
  const decision = isExactDecisionStep ? kyoku.decisions[snap.decision_idx] : findLatestDecisionBeforeStep(kyoku, clamped);

  if (changed || !isExactDecisionStep) state.openCandLabel = null;

  renderBoard(kyoku, snap, isExactDecisionStep ? decision : null);
  renderDecisionPanel(kyoku, decision, isExactDecisionStep);
  highlightActiveDecisionInList(decision?.decision_idx);
}

function findLatestDecisionBeforeStep(kyoku, stepIdx) {
  let found = null;
  for (const d of kyoku.decisions || []) {
    if (d.step_idx <= stepIdx) {
      found = d;
    } else {
      break;
    }
  }
  return found;
}

function formatStepEventToast(ev, names) {
  if (!ev) return "";
  const actorName = ev.actor !== undefined ? names[ev.actor] || `玩家${ev.actor}` : "";
  switch (ev.type) {
    case "start_kyoku":
      return `${ev.bakaze === "S" ? "南" : ev.bakaze === "W" ? "西" : "东"}${ev.kyoku}局 开始`;
    case "tsumo":
      return `${actorName} 摸牌`;
    case "dahai":
      return `${actorName} ${ev.tsumogiri ? "摸切" : "手切"} ${tileCn(ev.pai)}`;
    case "chi":
      return `${actorName} 吃 ${tileCn(ev.pai)}`;
    case "pon":
      return `${actorName} 碰 ${tileCn(ev.pai)}`;
    case "daiminkan":
      return `${actorName} 明杠 ${tileCn(ev.pai)}`;
    case "ankan":
      return `${actorName} 暗杠`;
    case "kakan":
      return `${actorName} 加杠 ${tileCn(ev.pai)}`;
    case "reach":
      return `${actorName} 立直！`;
    case "reach_accepted":
      return `${actorName} 立直成立`;
    case "dora":
      return `新宝牌指示牌 ${tileCn(ev.dora_marker)}`;
    case "hora":
      return `${actorName} ${ev.actor === ev.target ? "自摸" : "荣和"}！`;
    case "ryukyoku":
      return "流局";
    case "end_kyoku":
      return "本局结束";
    default:
      return "";
  }
}

const REL_POS = ["Bottom", "Right", "Top", "Left"];

function renderBoard(kyoku, snap, activeDecision) {
  const res = state.reviewData;
  const hero = state.targetActor;
  const names = res.names;

  // Center status box
  $("centerRoundLabel").textContent = kyoku.label;
  $("centerTilesLeft").textContent = `余 ${snap.tiles_left} 张`;
  $("centerKyotaku").textContent = `供托 ${snap.kyotaku}`;
  $("centerHonba").textContent = `${snap.honba} 本场`;
  $("centerJunme").textContent = `第 ${snap.junme} 巡`;

  const markers = snap.dora_markers || [];
  let doraHtml = markers.map((m) => renderTileHtml(m, "t-dora")).join("");
  for (let i = markers.length; i < 5; i++) doraHtml += renderTileHtml("?", "t-dora");
  $("centerDoraMarkers").innerHTML = doraHtml;

  // Event text
  const isLast = state.stepIdx === kyoku.steps.length - 1;
  $("centerEventToast").textContent = kyoku.end_summaries?.length && isLast
    ? `本局结果：${kyoku.end_summaries.join(" · ")}`
    : formatStepEventToast(snap.event, names);

  // Relative seat mapping: Bottom = hero, Right = (hero+1)%4, Top = (hero+2)%4, Left = (hero+3)%4
  REL_POS.forEach((pos, rel) => {
    const seat = (hero + rel) % 4;
    renderCenterSeat(pos, seat, kyoku, snap);
    renderPlayerHeader(pos, seat, kyoku, snap, names);
    renderPlayerRiver(pos, seat, snap);
    renderPlayerMelds(pos, seat, snap);
    if (pos === "Bottom") {
      renderHeroHand(seat, snap, activeDecision);
    } else {
      renderOpponentHand(pos, seat, snap);
    }
  });
}

function renderCenterSeat(pos, seat, kyoku, snap) {
  const el = $(`cseat${pos}`);
  const wind = WIND_CN[(seat - kyoku.oya + 4) % 4];
  el.className = `cseat cseat-${pos.toLowerCase()}`;
  if (seat === kyoku.oya) el.classList.add("is-oya");
  if (snap.actor === seat) el.classList.add("is-turn");
  if (snap.riichi[seat]) el.classList.add("is-riichi");
  el.innerHTML = `<b>${wind}</b><span class="cs-score">${snap.scores[seat]}</span>`;
  el.title = `${state.reviewData.names[seat]} · ${snap.scores[seat]} 点`;
}

function renderPlayerHeader(pos, seat, kyoku, snap, names) {
  const el = $(`header${pos}`);
  const seatWind = WIND_CN[(seat - kyoku.oya + 4) % 4];
  const isOya = seat === kyoku.oya;
  const windHtml = `<span class="wind-pill ${isOya ? "oya" : ""}">${seatWind}${isOya ? "·亲" : ""}</span>`;
  const riichiHtml = snap.riichi[seat] ? `<span class="riichi-tag">立直</span>` : "";
  el.classList.toggle("is-turn", snap.actor === seat);
  el.title = names[seat];
  if (pos === "Bottom") {
    el.innerHTML = `${windHtml}<span class="tag-name">${escapeHtml(names[seat])}</span><span class="tag-score">${snap.scores[seat]}</span>${riichiHtml}`;
  } else {
    el.innerHTML = `
      <div class="tag-line1">${windHtml}<span class="tag-name">${escapeHtml(names[seat])}</span></div>
      <div class="tag-line2"><span class="tag-score">${snap.scores[seat]}</span> 点 ${riichiHtml}</div>`;
  }
}

function renderPlayerRiver(pos, seat, snap) {
  const river = snap.rivers[seat] || [];
  const isLastDiscardActor = snap.event?.type === "dahai" && snap.event?.actor === seat;
  $(`river${pos}`).innerHTML = river
    .map((r, idx) => {
      const classes = [];
      if (r.tsumogiri) classes.push("tile-tsumogiri");
      if (r.riichi) classes.push("tile-side");
      if (r.called) classes.push("tile-called");
      if (isLastDiscardActor && idx === river.length - 1) classes.push("tile-last");
      const tip = `${tileCn(r.pai)}（${r.tsumogiri ? "摸切" : "手切"}${r.riichi ? " · 立直宣言牌" : ""}${r.called ? " · 被鸣" : ""}）`;
      return renderTileHtml(r.pai, "t-river", classes.join(" "), tip);
    })
    .join("");
}

// Render melds with the called tile turned sideways according to the source seat
function renderMeldGroup(m, ownerSeat, sizeCls) {
  const MELD_CN = { chi: "吃", pon: "碰", daiminkan: "明杠", kakan: "加杠", ankan: "暗杠" };
  const tiles = [...(m.tiles || [])];
  if (!tiles.length) return "";
  let sideIdx = -1;
  let order = tiles;
  if (m.type === "ankan") {
    return `<div class="meld-group" title="暗杠">${tiles.map((t, i) => renderTileHtml(i === 0 || i === tiles.length - 1 ? "?" : t, sizeCls)).join("")}</div>`;
  }
  if (m.from !== undefined && m.from !== null) {
    const called = tiles[0];
    const rest = tiles.slice(1);
    const rel = (m.from - ownerSeat + 4) % 4; // 3 = kamicha (left), 2 = toimen, 1 = shimocha (right)
    if (rel === 3) { order = [called, ...rest]; sideIdx = 0; }
    else if (rel === 2) { order = [rest[0], called, ...rest.slice(1)]; sideIdx = 1; }
    else { order = [...rest, called]; sideIdx = order.length - 1; }
  }
  const html = order.map((t, i) => renderTileHtml(t, sizeCls, i === sideIdx ? "tile-side" : "")).join("");
  return `<div class="meld-group" title="${MELD_CN[m.type] || m.type}">${html}</div>`;
}

function renderPlayerMelds(pos, seat, snap) {
  const melds = snap.melds[seat] || [];
  const sizeCls = pos === "Bottom" ? "t-hmeld" : "t-meld";
  $(`melds${pos}`).innerHTML = melds.map((m) => renderMeldGroup(m, seat, sizeCls)).join("");
}

function renderOpponentHand(pos, seat, snap) {
  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  let html = hand.map((t) => renderTileHtml(state.openHands ? t : "?", "t-opp")).join("");
  if (drawn) {
    html += `<span style="width:8px"></span>` + renderTileHtml(state.openHands ? drawn : "?", "t-opp");
  }
  $(`hand${pos}`).innerHTML = html;
}

// Render Hero Hand with NAGA Probability Bars overlaid directly on top of each tile!
function renderHeroHand(seat, snap, activeDecision) {
  const wrap = $("handBottom");
  const banner = $("heroActionBanner");
  const verdictPill = $("heroVerdictPill");

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  const allTiles = hand.map((pai) => ({ pai, isDrawn: false }));
  if (drawn) allTiles.push({ pai: drawn, isDrawn: true });

  const tileBars = activeDecision?.tile_bars || {};

  if (activeDecision) {
    verdictPill.className = `verdict-pill ${activeDecision.verdict}`;
    verdictPill.textContent = activeDecision.is_equal
      ? `${activeDecision.verdict_cn} · 与 AI 一致`
      : `${activeDecision.verdict_cn} · AI：${activeDecision.expected_cn}`;
  } else {
    verdictPill.className = "verdict-pill hidden";
  }

  // Non-discard action distribution (Riichi / Call / Pass / Kan / Agari)
  const hasNonDiscard = (activeDecision?.candidates || []).some((c) => c.label >= 37);
  if (activeDecision && (hasNonDiscard || activeDecision.decision_type === "call_response")) {
    const topCands = activeDecision.candidates.slice(0, 5);
    banner.innerHTML = `<span class="ab-label">动作选择</span>` + topCands
      .map((c) => {
        const isBest = c.rank === 1;
        const isAct = c.action_cn === activeDecision.actual_cn;
        return `
          <span class="ab-chip ${isBest ? "is-best" : ""} ${isAct ? "is-actual" : ""}">
            ${c.pai && c.label <= 36 ? renderTileHtml(c.pai, "t-mini") : ""}
            <span>${escapeHtml(c.action_cn)}</span>
            <span class="ab-prob">${fmtPct(c.prob)}</span>
            ${isBest ? `<span class="mini-badge ai">AI</span>` : ""}
            ${isAct ? `<span class="mini-badge ${isBest ? "act-ok" : "act-bad"}">实战</span>` : ""}
          </span>`;
      })
      .join("");
    banner.classList.remove("is-empty");
  } else {
    banner.innerHTML = "";
    banner.classList.add("is-empty");
  }

  wrap.innerHTML = allTiles
    .map(({ pai, isDrawn }) => {
      const barInfo = tileBars[pai];
      const prob = barInfo ? barInfo.prob : 0;
      const barHeight = prob > 0 ? Math.max(2, Math.round((prob / 100) * 30)) : 0;

      const cls = ["hero-slot"];
      if (isDrawn) cls.push("drawn");
      if (barInfo?.is_best) cls.push("best");
      else if (barInfo?.is_actual) cls.push("mismatch");

      let badges = "";
      if (barInfo?.is_best) badges += `<span class="mini-badge ai">AI</span>`;
      if (barInfo?.is_actual) badges += `<span class="mini-badge ${barInfo.is_best ? "act-ok" : "act-bad"}">实战</span>`;

      const probText = barInfo && prob >= 0.5 ? `${prob.toFixed(prob >= 10 ? 0 : 1)}%` : "";
      const tip = barInfo
        ? `${tileCn(pai)}：AI 推荐度 ${prob.toFixed(1)}% · 进张 ${barInfo.ukeire} 张${barInfo.danger ? ` · 放铳率 ${barInfo.danger}%` : ""}`
        : tileCn(pai);

      return `
        <div class="${cls.join(" ")}" data-pai="${escapeHtml(pai)}">
          <div class="bar-area">
            <span class="bar-text">${probText}</span>
            <div class="bar-track">${barHeight ? `<div class="bar-fill" style="height:${barHeight}px"></div>` : ""}</div>
          </div>
          ${renderTileHtml(pai, "t-hero", "", tip)}
          <div class="slot-badges">${badges}</div>
        </div>`;
    })
    .join("");

  // Clicking a tile in hero hand expands its candidate row
  wrap.querySelectorAll(".hero-slot").forEach((slot) => {
    slot.addEventListener("click", () => {
      if (!activeDecision) return;
      const cand = activeDecision.candidates.find((c) => c.pai === slot.dataset.pai && c.label <= 36);
      if (cand) {
        switchTab("tabDecision");
        toggleCandidate(cand.label, true);
      }
    });
  });
}

// ============================================================================
// Right Column: Decision panel, candidates, decision list
// ============================================================================
function renderActionWithTile(actObj, actionCn) {
  if (!actObj) return `<span>${escapeHtml(actionCn || "--")}</span>`;
  const tile = actObj.pai && actObj.type === "dahai" ? renderTileHtml(actObj.pai, "t-inline") : "";
  return `${tile}<span>${escapeHtml(actionCn)}</span>`;
}

function tagClass(t) {
  if (t.includes("危险") || t.includes("退向") || t.includes("无役")) return "danger";
  if (t.includes("最") || t.includes("听牌") || t.includes("现物")) return "good";
  return "";
}

function renderDecisionPanel(kyoku, dec, isExactStep) {
  const badge = $("decVerdictBadge");
  const aiBox = $("compareAiContent");
  const actBox = $("compareActualContent");
  const list = $("candidatesList");
  const actualSide = actBox.parentElement;

  const headline = $("decHeadline");
  if (!dec) {
    badge.className = "verdict-pill neutral";
    badge.textContent = "还没有";
    $("decTurnChip").textContent = "第 - 巡";
    $("decShantenChip").textContent = "-";
    $("decQLossChip").textContent = "还没比较";
    $("decQLossChip").title = "和 AI 最想下的那一手比，差多少。";
    $("decFuritenChip").classList.add("hidden");
    if (headline) headline.textContent = "还没轮到这个玩家做决定。按 ↓ 跳到第一手。";
    aiBox.innerHTML = "--";
    actBox.innerHTML = "--";
    actualSide.classList.remove("is-diff");
    list.innerHTML = `<div class="empty">这一局还没轮到这个玩家做决定。<br />按 <kbd>↓</kbd>，或点「下一手」。</div>`;
    return;
  }

  badge.className = `verdict-pill ${dec.verdict}`;
  badge.textContent = isExactStep ? dec.verdict_cn : `${dec.verdict_cn}，上一手`;
  if (headline) headline.textContent = isExactStep ? decisionSentence(dec) : `上一手。${decisionSentence(dec)}`;
  $("decTurnChip").textContent = `第 ${dec.junme} 巡 · 余 ${dec.tiles_left} 张`;
  $("decShantenChip").textContent = shantenText(dec.shanten);
  $("decQLossChip").textContent = VERDICT_NOTE[dec.verdict] || "已比较";
  $("decQLossChip").title = `和 AI 最想下的那一手比，差距是 ${dec.q_loss.toFixed(3)}。数字越大，差得越远。`;
  $("decFuritenChip").classList.toggle("hidden", !dec.at_furiten);

  aiBox.innerHTML = `${renderActionWithTile(dec.expected, dec.expected_cn)}<span class="cc-sub good">${fmtPct(dec.best_prob)}</span>`;
  actBox.innerHTML = `${renderActionWithTile(dec.actual, dec.actual_cn)}<span class="cc-sub ${dec.is_equal ? "same" : "bad"}">${fmtPct(dec.actual_prob)} · 第 ${dec.actual_rank} 选择</span>`;
  actualSide.classList.toggle("is-diff", !dec.is_equal);

  list.innerHTML = dec.candidates
    .map((c) => {
      const isBest = c.rank === 1;
      const isActual = c.action_cn === dec.actual_cn && c.rank === dec.actual_rank;
      const cls = ["cand"];
      if (isBest) cls.push("is-best");
      else if (isActual && !dec.is_equal) cls.push("is-actual-bad");

      const badges = [];
      if (isBest) badges.push(`<span class="mini-badge ai">AI</span>`);
      if (isActual) badges.push(`<span class="mini-badge ${isBest ? "act-ok" : "act-bad"}">实战</span>`);

      const stats = [];
      if (c.win_prob > 0) stats.push(`<span class="stat-text">和率 ${c.win_prob}%</span>`);
      if (c.tenpai_prob > 0) stats.push(`<span class="stat-text">听牌率 ${c.tenpai_prob}%</span>`);
      if (c.ev > 0) stats.push(`<span class="stat-text">打点期望 ${c.ev}</span>`);
      if (c.danger > 0) stats.push(`<span class="stat-text ${c.danger >= 12 ? "danger" : ""}">放铳率 ${c.danger}%</span>`);
      const tags = (c.tags || []).map((t) => `<span class="tag-chip ${tagClass(t)}">${escapeHtml(t)}</span>`).join("");

      const ukTiles = c.ukeire_tiles || [];
      const ukHtml = ukTiles.length
        ? `<div class="uk-title">进张 ${c.ukeire} 张（${ukTiles.length} 种）</div>
           <div class="uk-tiles">${ukTiles.map((u) => `<span class="uk-item">${renderTileHtml(u.pai, "t-inline")}×${u.count}</span>`).join("")}</div>`
        : `<div class="uk-title">${shantenText(c.shanten_after)}，无进张明细</div>`;

      const isDiscard = c.label <= 36 && c.pai;
      return `
        <div class="${cls.join(" ")}" data-label="${c.label}">
          <div class="cand-main">
            <span class="cand-rank">#${c.rank}</span>
            <div class="cand-action">
              ${isDiscard ? renderTileHtml(c.pai, "t-inline") : ""}
              <span>${escapeHtml(c.action_cn)}</span>
              <span class="cand-badges">${badges.join("")}</span>
            </div>
            <div>
              <div class="cand-prob-top"><b>${fmtPct(c.prob)}</b></div>
              <div class="cand-track"><div class="cand-fill" style="width:${Math.min(100, c.prob)}%"></div></div>
            </div>
            <div class="cand-eff">
              <div>${shantenText(c.shanten_after)}</div>
              <div class="sub">${c.ukeire > 0 ? `进张 ${c.ukeire}` : "&nbsp;"}</div>
            </div>
          </div>
          <div class="cand-extra">${stats.join("")}${tags}</div>
          <div class="cand-ukeire">${ukHtml}</div>
        </div>`;
    })
    .join("");

  list.querySelectorAll(".cand").forEach((row) => {
    row.addEventListener("click", () => toggleCandidate(parseInt(row.dataset.label, 10)));
  });

  // Re-open previously expanded row (same step), otherwise expand the AI top choice
  const openLabel = state.openCandLabel ?? dec.candidates[0]?.label;
  if (openLabel !== undefined) toggleCandidate(openLabel, true, false);
}

function toggleCandidate(label, forceOpen = false, scroll = true) {
  const list = $("candidatesList");
  const row = list.querySelector(`.cand[data-label="${label}"]`);
  if (!row) return;
  const willOpen = forceOpen || !row.classList.contains("open");
  list.querySelectorAll(".cand.open").forEach((r) => r.classList.remove("open"));
  if (willOpen) {
    row.classList.add("open");
    state.openCandLabel = label;
    if (scroll) scrollIntoContainer($("tabDecision"), row);
  } else {
    state.openCandLabel = null;
  }
}

// Scroll a child into view inside its scroll container only (never scrolls the page)
function scrollIntoContainer(container, el) {
  if (!container || !el) return;
  if (container.scrollHeight <= container.clientHeight) return;
  const cRect = container.getBoundingClientRect();
  const eRect = el.getBoundingClientRect();
  if (eRect.top < cRect.top) {
    container.scrollTop += eRect.top - cRect.top - 8;
  } else if (eRect.bottom > cRect.bottom) {
    container.scrollTop += eRect.bottom - cRect.bottom + 8;
  }
}

function renderDecActCell(label, actObj, actionCn, pct) {
  const tile = actObj?.pai && actObj.type === "dahai" ? renderTileHtml(actObj.pai, "t-mini") : "";
  return `<span class="dr-act"><span class="lbl">${label}</span>${tile}<span class="txt">${escapeHtml(actionCn)}</span><span class="pct">${pct.toFixed(0)}%</span></span>`;
}

function renderKyokuDecisionList(kyoku) {
  const container = $("decisionListContainer");
  const all = kyoku.decisions || [];
  const issues = all.filter((d) => d.verdict === "blunder" || d.verdict === "question");
  $("decCountBadge").textContent = issues.length ? `${issues.length} 失误` : all.length;

  const decs = state.decisionFilter === "issues" ? issues : all;
  if (!decs.length) {
    container.innerHTML = `<div class="empty">${state.decisionFilter === "issues" ? "这一局没有需要回头看的手。" : "这一局没有这个玩家做决定的地方。"}</div>`;
    return;
  }

  container.innerHTML = decs
    .map((d) => `
      <div class="dec-row v-${d.verdict}" data-decidx="${d.decision_idx}" data-step="${d.step_idx}">
        <span class="dr-turn">第 ${d.junme} 巡</span>
        ${renderDecActCell("AI", d.expected, d.expected_cn, d.best_prob)}
        ${renderDecActCell("实战", d.actual, d.actual_cn, d.actual_prob)}
        <span class="verdict-pill ${d.verdict}">${VERDICT_SHORT[d.verdict] || d.verdict_cn}</span>
      </div>`)
    .join("");

  container.querySelectorAll(".dec-row").forEach((row) => {
    row.addEventListener("click", () => setStep(parseInt(row.dataset.step, 10)));
  });
  highlightActiveDecisionInList(currentDecisionIdx());
}

function currentDecisionIdx() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return undefined;
  return findLatestDecisionBeforeStep(kyoku, state.stepIdx)?.decision_idx;
}

function highlightActiveDecisionInList(decIdx) {
  const container = $("decisionListContainer");
  if (!container) return;
  container.querySelectorAll(".dec-row").forEach((row) => {
    const isMatch = parseInt(row.dataset.decidx, 10) === decIdx;
    row.classList.toggle("active", isMatch);
    if (isMatch && !$("tabList").classList.contains("hidden")) {
      scrollIntoContainer($("tabList"), row);
    }
  });
}

// ============================================================================
// Navigation Helpers (Prev/Next Decision, Next Blunder)
// ============================================================================
function jumpPrevDecision() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return;
  const prev = [...(kyoku.decisions || [])].reverse().find((d) => d.step_idx < state.stepIdx);
  if (prev) {
    setStep(prev.step_idx);
  } else if (state.kyokuIdx > 0) {
    selectKyoku(state.kyokuIdx - 1, false);
    const prevKyoku = state.reviewData.kyokus[state.kyokuIdx];
    if (prevKyoku.decisions?.length) {
      setStep(prevKyoku.decisions[prevKyoku.decisions.length - 1].step_idx);
    }
  }
}

function jumpNextDecision() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return;
  const next = (kyoku.decisions || []).find((d) => d.step_idx > state.stepIdx);
  if (next) {
    setStep(next.step_idx);
  } else if (state.kyokuIdx + 1 < (state.reviewData?.kyokus?.length || 0)) {
    selectKyoku(state.kyokuIdx + 1, true);
  } else {
    showToast("已经是最后一手了", "info");
  }
}

function jumpNextBlunder() {
  const res = state.reviewData;
  if (!res || !res.kyokus) return;

  // Search current kyoku after stepIdx, then subsequent kyokus, then wrap around
  const totalKyokus = res.kyokus.length;
  for (let offset = 0; offset <= totalKyokus; offset++) {
    const kIdx = (state.kyokuIdx + offset) % totalKyokus;
    const decs = res.kyokus[kIdx].decisions || [];
    for (const d of decs) {
      if (d.verdict !== "blunder" && d.verdict !== "question") continue;
      if (offset === 0 && d.step_idx <= state.stepIdx) continue;
      if (kIdx !== state.kyokuIdx) selectKyoku(kIdx, false);
      setStep(d.step_idx);
      switchTab("tabDecision");
      showToast(`${res.kyokus[kIdx].label} 第 ${d.junme} 巡：${d.verdict_cn}`, "info");
      return;
    }
  }
  showToast("这一局里没有需要回头看的手", "success");
}

// ============================================================================
// Tabs, Stage scaling, Modals
// ============================================================================
function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tabId));
  document.querySelectorAll(".tab-body").forEach((b) => b.classList.toggle("hidden", b.id !== tabId));
  if (tabId === "tabList") highlightActiveDecisionInList(currentDecisionIdx());
}

const stackedMq = window.matchMedia("(max-width: 1099px)");

function fitStage() {
  const wrap = $("stageWrap");
  const stage = $("stage");
  const w = wrap.clientWidth;
  let scale;
  if (stackedMq.matches) {
    scale = Math.min(1, w / STAGE_W);
    wrap.style.height = `${Math.ceil(STAGE_H * scale)}px`;
  } else {
    wrap.style.height = "";
    scale = Math.min(w / STAGE_W, wrap.clientHeight / STAGE_H);
  }
  stage.style.setProperty("--stage-scale", Math.max(0.3, scale).toFixed(4));
}

function openModal(id) {
  $("moreMenu")?.removeAttribute("open");
  $(id).classList.remove("hidden");
  $(id).querySelector("input, textarea, select, button.btn")?.focus();
}

function closeModal(id) {
  $(id).classList.add("hidden");
}

function renderSavedHistoryList(items) {
  const listEl = $("recentGamesList");
  if (!items || !items.length) return;
  listEl.innerHTML = items
    .map((item) => {
      const namesStr = (item.names || []).join(" / ") || "四人麻将";
      const time = item.start_time ? new Date(item.start_time * 1000).toLocaleString("zh-CN", { hour12: false }) : "";
      return `
        <div class="recent-item">
          <div class="ri-main">
            <div class="ri-id" title="${escapeHtml(item.uuid)}">${escapeHtml(time || item.uuid)}</div>
            <div class="ri-names" title="${escapeHtml(namesStr)}">${escapeHtml(namesStr)}</div>
          </div>
          <button class="btn btn-sm btn-primary" data-uuid="${escapeHtml(item.uuid)}">分析</button>
        </div>`;
    })
    .join("");

  listEl.querySelectorAll("button[data-uuid]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("paipuInput").value = btn.dataset.uuid;
      closeModal("accountModal");
      analyzePaipuInput();
    });
  });
}

function toggleOpenHands() {
  state.openHands = !state.openHands;
  const btn = $("btnToggleOpenHands");
  btn.textContent = `对手手牌: ${state.openHands ? "显示" : "隐藏"}`;
  btn.classList.toggle("is-on", state.openHands);
  if (state.reviewData) setStep(state.stepIdx);
}

function setDecisionFilter(filter) {
  state.decisionFilter = filter;
  $("filterAllDecs").classList.toggle("active", filter === "all");
  $("filterIssueDecs").classList.toggle("active", filter === "issues");
  if (state.reviewData) renderKyokuDecisionList(state.reviewData.kyokus[state.kyokuIdx]);
}

async function analyzeJsonContent(raw) {
  closeModal("jsonModal");
  setLoading(true, "正在解析并分析牌谱文件...");
  await runAnalysis(
    apiFetch("/api/analyze/json", {
      method: "POST",
      body: JSON.stringify({ content: raw, target_actor: state.targetActor, engine: currentEngine() }),
    }),
    { successMsg: () => "牌谱文件分析完成" }
  );
}

async function readFileToPasteArea(file) {
  if (!file) return;
  const text = await file.text();
  $("jsonPasteArea").value = text;
  $("dropText").textContent = `已选择：${file.name}`;
}

function bindEvents() {
  // Header actions
  $("btnAnalyzePaipu").addEventListener("click", () => analyzePaipuInput());
  $("paipuInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") analyzePaipuInput();
  });
  $("btnLoadDemo").addEventListener("click", () => loadDemoGame(0));

  // Replay controls
  $("stepSlider").addEventListener("input", (e) => setStep(parseInt(e.target.value, 10)));
  $("btnStepStart").addEventListener("click", () => setStep(0));
  $("btnPrevStep").addEventListener("click", () => setStep(state.stepIdx - 1));
  $("btnNextStep").addEventListener("click", () => setStep(state.stepIdx + 1));
  $("btnPrevDec").addEventListener("click", jumpPrevDecision);
  $("btnNextDec").addEventListener("click", jumpNextDecision);
  $("btnNextBlunder").addEventListener("click", jumpNextBlunder);
  $("btnQuickNextBlunder").addEventListener("click", jumpNextBlunder);
  $("btnToggleOpenHands").addEventListener("click", toggleOpenHands);

  // Tabs & filters
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  $("filterAllDecs").addEventListener("click", () => setDecisionFilter("all"));
  $("filterIssueDecs").addEventListener("click", () => setDecisionFilter("issues"));

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) => m.classList.add("hidden"));
      return;
    }
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    if (document.querySelector(".modal-backdrop:not(.hidden)")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const actions = {
      ArrowLeft: () => setStep(state.stepIdx - 1),
      ArrowRight: () => setStep(state.stepIdx + 1),
      ArrowUp: jumpPrevDecision,
      ArrowDown: jumpNextDecision,
      Home: () => setStep(0),
      b: jumpNextBlunder,
      B: jumpNextBlunder,
      h: toggleOpenHands,
      H: toggleOpenHands,
    };
    if (actions[e.key]) {
      e.preventDefault();
      actions[e.key]();
    }
  });

  // Modals open/close
  $("btnOpenAccountModal").addEventListener("click", () => openModal("accountModal"));
  $("btnOpenJsonModal").addEventListener("click", () => openModal("jsonModal"));
  $("btnOpenEngineModal").addEventListener("click", () => openModal("engineModal"));
  $("btnOpenHelp").addEventListener("click", () => openModal("helpModal"));
  document.querySelectorAll(".modal-close").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });
  document.querySelectorAll(".modal-backdrop").forEach((bd) => {
    bd.addEventListener("mousedown", (e) => {
      if (e.target === bd) bd.classList.add("hidden");
    });
  });
  document.addEventListener("mousedown", (e) => {
    const more = $("moreMenu");
    if (more?.open && !more.contains(e.target)) more.removeAttribute("open");
  });

  // Account Login / Logout / Recent games
  $("btnSubmitLogin").addEventListener("click", async () => {
    const account = $("loginAccount").value.trim();
    const password = $("loginPassword").value;
    const access_token = $("loginToken").value.trim();
    if (!access_token && !(account && password)) {
      showToast("请填写账号和密码，或填写 Access Token", "error");
      return;
    }
    setLoading(true, "正在登录雀魂国服...");
    try {
      const st = await apiFetch("/api/login", {
        method: "POST",
        body: JSON.stringify({ account, password, access_token, remember: true }),
      });
      updateTopbarStatus(st);
      $("loginPassword").value = "";
      showToast("登录成功", "success");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setLoading(false);
    }
  });

  $("btnLogout").addEventListener("click", async () => {
    try {
      const st = await apiFetch("/api/logout", { method: "POST" });
      updateTopbarStatus(st);
      showToast("已清除本地凭证", "info");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  $("btnFetchRecentGames").addEventListener("click", async () => {
    setLoading(true, "正在拉取最近对局...");
    try {
      const data = await apiFetch("/api/recent_games");
      const games = data.games || [];
      if (games.length) renderSavedHistoryList(games);
      else $("recentGamesList").innerHTML = `<div class="empty">没有找到最近对局</div>`;
      showToast(`获取到 ${games.length} 场对局`, "success");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setLoading(false);
    }
  });

  // Local JSON Upload / Paste / Drag & drop
  $("jsonFileInput").addEventListener("change", (e) => readFileToPasteArea(e.target.files?.[0]));
  const dz = $("fileDropZone");
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("drag")));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    readFileToPasteArea(e.dataTransfer?.files?.[0]);
  });

  $("btnAnalyzeJson").addEventListener("click", () => {
    const raw = $("jsonPasteArea").value.trim();
    if (!raw) {
      showToast("请先选择牌谱文件或粘贴内容", "error");
      return;
    }
    analyzeJsonContent(raw);
  });

  // Engine Config Save / Reset / Re-analyze
  $("btnSaveEngineConfig").addEventListener("click", async () => {
    const model_path = $("modelPathInput").value.trim();
    try {
      await apiFetch("/api/engine/config", {
        method: "POST",
        body: JSON.stringify({ model_path: model_path || null }),
      });
      await refreshServerStatus();
      showToast("引擎设置已保存", "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  $("btnResetEngineConfig").addEventListener("click", async () => {
    $("modelPathInput").value = "";
    try {
      await apiFetch("/api/engine/config", { method: "POST", body: JSON.stringify({ model_path: null }) });
      await refreshServerStatus();
      showToast("已恢复默认权重路径", "info");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  $("btnReanalyze").addEventListener("click", async () => {
    if (!state.reviewData) {
      showToast("请先加载一个牌谱", "error");
      return;
    }
    closeModal("engineModal");
    setLoading(true, "正在用新设置重新分析...");
    await reanalyzeCurrent(state.targetActor, state.kyokuIdx);
  });

  // Stage scaling
  new ResizeObserver(() => fitStage()).observe($("stageWrap"));
  window.addEventListener("resize", fitStage);
  stackedMq.addEventListener("change", fitStage);
}

// ============================================================================
// Initialize App on DOMContentLoaded
// ============================================================================
window.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  fitStage();
  await refreshServerStatus();
  await loadDemoGame(0);
});
