/**
 * 雀魂牌谱 AI 检讨系统 — NAGA / Mortal 风格前端控制器
 */

const state = {
  reviewData: null,        // Full response from /api/analyze/*
  gameId: "demo",          // Current game_id or paipu UUID
  targetActor: 0,          // Current reviewed player seat (0..3)
  kyokuIdx: 0,             // Current kyoku index
  stepIdx: 0,              // Current replay step index within kyoku
  openHands: true,         // Show opponent hands face-up
  decisionFilter: "all",   // "all" | "issues"
  selectedCandLabel: null, // Selected candidate action label for ukeire breakdown
  serverStatus: null,
};

const WIND_CN = ["东", "南", "西", "北"];
const TILE_CN_MAP = {
  E: "东", S: "南", W: "西", N: "北", P: "白", F: "发", C: "中",
  "5mr": "赤五万", "5pr": "赤五筒", "5sr": "赤五索",
};

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
  return `${s}向听`;
}

// Render an SVG mahjong tile HTML string
function renderTileHtml(pai, sizeClass = "tile-opp", extraClass = "", title = "") {
  if (!pai || pai === "?") {
    return `<div class="mj-tile tile-back ${sizeClass} ${extraClass}"></div>`;
  }
  const isRed = pai.endsWith("r");
  const redCls = isRed ? "tile-red" : "";
  const tip = title || tileCn(pai);
  return `
    <div class="mj-tile ${sizeClass} ${redCls} ${extraClass}" title="${tip}" data-pai="${pai}">
      <img src="/tiles/${pai}.svg" alt="${pai}" loading="lazy" />
    </div>
  `;
}

// Toast helper
function showToast(msg, type = "info") {
  const container = document.getElementById("toastContainer");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, 3600);
}

function setLoading(visible, title = "正在进行 AI 牌谱检讨...", sub = "计算每一巡向听、进张、局收支 EV、放铳率与最优动作分布") {
  const overlay = document.getElementById("loadingOverlay");
  document.getElementById("loadingTitle").textContent = title;
  document.getElementById("loadingSub").textContent = sub;
  overlay.classList.toggle("hidden", !visible);
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
  }
}

function updateTopbarStatus(st) {
  const dot = document.getElementById("msStatusDot");
  const txt = document.getElementById("msStatusText");
  const banner = document.getElementById("modalAccountStatusBanner");

  if (st.majsoul?.logged_in) {
    dot.className = "status-dot online";
    txt.textContent = `已连接: ${st.majsoul.nickname || st.majsoul.account_id || "国服账号"}`;
    banner.textContent = `✅ 已登录雀魂国服网关 — 昵称: ${st.majsoul.nickname || "未知"} (ID: ${st.majsoul.account_id || "-"})`;
  } else if (st.majsoul?.has_saved_token || st.majsoul?.saved_account) {
    dot.className = "status-dot saved";
    txt.textContent = `国服账号 (已存凭证)`;
    banner.textContent = `🟡 已保存本地登录凭证 (${st.majsoul.saved_account || "Token"})，拉取在线牌谱时将自动连接国服网关`;
  } else {
    dot.className = "status-dot offline";
    txt.textContent = "雀魂账号 / 最近对局";
    banner.textContent = "⚪ 当前状态：未登录雀魂国服网关（可使用内置演示牌谱、本地 JSON，或在此登录国服拉取在线牌谱）";
  }

  const engTxt = document.getElementById("engineStatusText");
  const eng = st.engine || {};
  if (eng.has_mortal_weights) {
    engTxt.textContent = `Mortal 神经网络 (${eng.device})`;
  } else {
    engTxt.textContent = `内置 SP+防守引擎 (Rust)`;
  }

  const engDetail = document.getElementById("engineModalDetail");
  if (engDetail) {
    engDetail.innerHTML = `
      <div><strong>当前激活引擎：</strong> <span style="color:#38bdf8">${eng.model_tag || "-"}</span></div>
      <div><strong>Mortal 权重状态：</strong> ${
        eng.has_mortal_weights
          ? `<span style="color:#34d399">✅ 已加载 (${eng.model_path})</span>`
          : `<span style="color:#fbbf24">⚡ 未检测到 mortal.pth，当前使用开箱即用的 libriichi Rust SP+防守精确检讨引擎</span>`
      }</div>
      <div><strong>推理计算设备：</strong> <code>${eng.device || "CPU"}</code></div>
      ${eng.load_error ? `<div style="color:#fda4af"><strong>权重加载提示：</strong> ${eng.load_error}</div>` : ""}
    `;
  }
}

async function loadDemoGame(targetActor = 0) {
  const engine = document.getElementById("engineSelect").value;
  setLoading(true, "正在加载并分析内置四麻演示牌谱...", "调用 libriichi / Mortal 引擎计算全书手牌 EV 与 NAGA 分布");
  try {
    const res = await apiFetch("/api/analyze/demo", {
      method: "POST",
      body: JSON.stringify({ target_actor: targetActor, engine, temperature: 0.1 }),
    });
    applyReviewResult(res);
    showToast("已加载内置四麻多局演示牌谱", "success");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setLoading(false);
  }
}

async function analyzePaipuInput(targetActor = null) {
  const rawInput = document.getElementById("paipuInput").value.trim() || state.gameId || "demo";
  const engine = document.getElementById("engineSelect").value;
  setLoading(true, "正在抓取并检讨牌谱...", `目标: ${rawInput}`);
  try {
    const res = await apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify({
        paipu: rawInput,
        target_actor: targetActor,
        engine,
        temperature: 0.1,
      }),
    });
    applyReviewResult(res);
    refreshServerStatus();
    showToast(`牌谱分析完成（视角：${res.player_name}）`, "success");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setLoading(false);
  }
}

async function switchSeatPerspective(newSeat) {
  if (!state.reviewData || newSeat === state.targetActor) return;
  const keepKyoku = state.kyokuIdx;
  const engine = document.getElementById("engineSelect").value;
  setLoading(true, `正在切换至「${state.reviewData.names[newSeat]}」视角重新检讨...`);
  try {
    const res = await apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify({
        paipu: state.gameId || "demo",
        target_actor: newSeat,
        engine,
        temperature: 0.1,
      }),
    });
    applyReviewResult(res, keepKyoku);
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setLoading(false);
  }
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
  requestAnimationFrame(() => updateAdaptiveSizing());
}

function renderSummaryDashboard() {
  const res = state.reviewData;
  if (!res) return;

  // 1. Seat switcher tabs
  const seatTabs = document.getElementById("seatTabs");
  seatTabs.innerHTML = res.names
    .map((name, seat) => {
      const active = seat === res.target_actor ? "active" : "";
      return `
        <button class="seat-tab ${active}" data-seat="${seat}">
          <span class="seat-tab-wind">${WIND_CN[seat]}家 · 座位 ${seat + 1}</span>
          <span class="seat-tab-name" title="${name}">${name}</span>
        </button>
      `;
    })
    .join("");

  seatTabs.querySelectorAll(".seat-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchSeatPerspective(parseInt(btn.dataset.seat, 10)));
  });

  // 2. Metrics cards
  const sum = res.summary || {};
  document.getElementById("metricRating").textContent = (sum.rating ?? 0).toFixed(1);
  document.getElementById("metricEngineTag").textContent = `引擎: ${res.engine?.tag || "-"}`;

  const gradeEl = document.getElementById("metricGrade");
  const grade = sum.grade || "A";
  gradeEl.textContent = grade;
  const gLetter = grade[0];
  gradeEl.className = `grade-badge grade-${gLetter}`;

  document.getElementById("metricMatchRate").textContent = `${(sum.match_rate ?? 0).toFixed(1)}%`;
  document.getElementById("metricMatchCount").textContent = `(${sum.total_matches || 0} / ${sum.total_reviewed || 0} 手)`;
  document.getElementById("metricMatchBar").style.width = `${Math.min(100, sum.match_rate || 0)}%`;

  document.getElementById("metricBlunders").textContent = sum.blunders || 0;
  document.getElementById("metricBlunderRate").textContent = `(${(sum.blunder_rate ?? 0).toFixed(1)}%)`;
  document.getElementById("metricInaccuracies").textContent = `微差/疑问: ${sum.inaccuracies || 0} 处`;

  // 3. Kyoku pills
  renderKyokuPills();
}

function renderKyokuPills() {
  const res = state.reviewData;
  const container = document.getElementById("kyokuPills");
  if (!res || !res.kyokus) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = res.kyokus
    .map((k, idx) => {
      const active = idx === state.kyokuIdx ? "active" : "";
      const blunders = k.stats?.blunders || 0;
      const blunderHtml = blunders > 0 ? `<span class="kyoku-blunder-dot" title="本局有 ${blunders} 处恶手">${blunders}恶</span>` : "";
      const endSum = k.end_summaries?.length ? k.end_summaries[0] : "";
      return `
        <button class="kyoku-pill ${active}" data-kidx="${idx}" title="${endSum}">
          <span>${k.label}</span>
          <span class="kyoku-pill-rate">${k.stats?.match_rate ?? 100}%</span>
          ${blunderHtml}
        </button>
      `;
    })
    .join("");

  container.querySelectorAll(".kyoku-pill").forEach((btn) => {
    btn.addEventListener("click", () => selectKyoku(parseInt(btn.dataset.kidx, 10), true));
  });
}

function selectKyoku(kIdx, jumpToFirstDecision = false) {
  const res = state.reviewData;
  if (!res || !res.kyokus[kIdx]) return;
  state.kyokuIdx = kIdx;
  renderKyokuPills();

  const kyoku = res.kyokus[kIdx];
  const slider = document.getElementById("stepSlider");
  slider.max = Math.max(0, kyoku.steps.length - 1);

  renderTimelineTicks(kyoku);
  renderKyokuDecisionList(kyoku);

  if (jumpToFirstDecision && kyoku.decisions && kyoku.decisions.length > 0) {
    setStep(kyoku.decisions[0].step_idx);
  } else {
    setStep(0);
  }
}

function renderTimelineTicks(kyoku) {
  const ticksEl = document.getElementById("timelineTicks");
  const totalSteps = Math.max(1, kyoku.steps.length - 1);
  ticksEl.innerHTML = (kyoku.decisions || [])
    .map((d) => {
      const leftPct = ((d.step_idx / totalSteps) * 100).toFixed(2);
      return `<div class="timeline-tick ${d.verdict}" style="left:${leftPct}%" data-step="${d.step_idx}" title="第${d.junme}巡: ${d.verdict_cn} (AI:${d.expected_cn} / 实战:${d.actual_cn})"></div>`;
    })
    .join("");

  ticksEl.querySelectorAll(".timeline-tick").forEach((el) => {
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
  state.stepIdx = clamped;
  state.selectedCandLabel = null;

  document.getElementById("stepSlider").value = clamped;
  document.getElementById("stepCounterText").textContent = `步骤 ${clamped} / ${kyoku.steps.length - 1}`;

  const snap = kyoku.steps[clamped];
  const decision = snap.decision_idx !== null && snap.decision_idx !== undefined
    ? kyoku.decisions[snap.decision_idx]
    : findLatestDecisionBeforeStep(kyoku, clamped);

  const isExactDecisionStep = snap.decision_idx !== null && snap.decision_idx !== undefined;

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
      return `🀄 ${ev.bakaze === "S" ? "南" : "东"}${ev.kyoku}局 开始`;
    case "tsumo":
      return `${actorName} 摸 ${tileCn(ev.pai)}`;
    case "dahai":
      return `${actorName} ${ev.tsumogiri ? "摸切" : "手切"} ${tileCn(ev.pai)}`;
    case "chi":
      return `${actorName} 吃 ${tileCn(ev.pai)}`;
    case "pon":
      return `${actorName} 碰 ${tileCn(ev.pai)}`;
    case "daiminkan":
    case "ankan":
    case "kakan":
      return `${actorName} 杠`;
    case "hora":
      return `🎉 ${actorName} ${ev.actor === ev.target ? "自摸" : "荣和"}!`;
    case "ryukyoku":
      return `🌊 流局`;
    default:
      return ev.type;
  }
}

function renderBoard(kyoku, snap, activeDecision) {
  const res = state.reviewData;
  const hero = state.targetActor;
  const names = res.names;

  // Center status box
  document.getElementById("centerRoundLabel").textContent = kyoku.label;
  document.getElementById("centerTilesLeft").textContent = `余 ${snap.tiles_left} 枚`;
  document.getElementById("centerKyotaku").textContent = `供托 ${snap.kyotaku}`;
  document.getElementById("centerHonba").textContent = `本场 ${snap.honba}`;
  document.getElementById("centerJunme").textContent = `第 ${snap.junme} 巡`;

  // Dora markers
  const doraWrap = document.getElementById("centerDoraMarkers");
  const markers = snap.dora_markers || [];
  let doraHtml = markers.map((m) => renderTileHtml(m, "tile-dora")).join("");
  for (let i = markers.length; i < 5; i++) {
    doraHtml += renderTileHtml("?", "tile-dora");
  }
  doraWrap.innerHTML = doraHtml;

  // 4-way compass scores
  const compassEl = document.getElementById("compassScores");
  compassEl.innerHTML = [0, 1, 2, 3]
    .map((seat) => {
      const seatWind = WIND_CN[(seat - kyoku.oya + 4) % 4];
      const isTurn = snap.actor === seat ? "active-turn" : "";
      const rBadge = snap.riichi[seat] ? `<span class="riichi-tag">立直</span>` : "";
      return `
        <div class="compass-cell ${isTurn}">
          <span><strong>${seatWind}</strong> ${names[seat].slice(0, 5)} ${rBadge}</span>
          <span class="compass-score">${snap.scores[seat]}</span>
        </div>
      `;
    })
    .join("");

  // Event toast
  const toastText = kyoku.end_summaries?.length && state.stepIdx === kyoku.steps.length - 1
    ? `🏁 ${kyoku.end_summaries.join(" · ")}`
    : formatStepEventToast(snap.event, names);
  document.getElementById("centerEventToast").textContent = toastText;

  // Relative seat mapping: Bottom = hero, Right = (hero+1)%4, Top = (hero+2)%4, Left = (hero+3)%4
  const relMap = [
    { pos: "Bottom", seat: hero },
    { pos: "Right", seat: (hero + 1) % 4 },
    { pos: "Top", seat: (hero + 2) % 4 },
    { pos: "Left", seat: (hero + 3) % 4 },
  ];

  for (const { pos, seat } of relMap) {
    renderPlayerHeader(pos, seat, kyoku, snap, names);
    renderPlayerRiver(pos, seat, snap);
    renderPlayerMelds(pos, seat, snap);
    if (pos === "Bottom") {
      renderHeroHand(seat, snap, activeDecision);
    } else {
      renderOpponentHand(pos, seat, snap);
    }
  }
}

function renderPlayerHeader(pos, seat, kyoku, snap, names) {
  const el = document.getElementById(`header${pos}`);
  const seatWind = WIND_CN[(seat - kyoku.oya + 4) % 4];
  const isOya = seat === kyoku.oya;
  const rHtml = snap.riichi[seat] ? `<span class="riichi-tag">立直</span>` : "";
  el.innerHTML = `
    <span class="wind-pill ${isOya ? "oya" : ""}">${seatWind}家${isOya ? "·亲" : ""}</span>
    <strong>${names[seat]}</strong>
    <span style="color:#38bdf8;font-weight:700">${snap.scores[seat]}点</span>
    ${rHtml}
  `;
}

function renderPlayerRiver(pos, seat, snap) {
  const el = document.getElementById(`river${pos}`);
  const river = snap.rivers[seat] || [];
  const isLastDiscardActor = snap.event?.type === "dahai" && snap.event?.actor === seat;

  el.innerHTML = river
    .map((r, idx) => {
      const classes = [];
      if (r.tsumogiri) classes.push("tile-tsumogiri");
      if (r.riichi) classes.push("tile-riichi");
      if (r.called) classes.push("tile-called");
      if (isLastDiscardActor && idx === river.length - 1) classes.push("tile-last-discard");
      const tip = `${tileCn(r.pai)} (${r.tsumogiri ? "摸切" : "手切"}${r.riichi ? "·立直宣言牌" : ""})`;
      return renderTileHtml(r.pai, "tile-river", classes.join(" "), tip);
    })
    .join("");
}

function renderPlayerMelds(pos, seat, snap) {
  const el = document.getElementById(`melds${pos}`);
  const melds = snap.melds[seat] || [];
  const sizeCls = pos === "Bottom" ? "tile-opp" : "tile-dora";
  el.innerHTML = melds
    .map((m) => {
      const tilesHtml = (m.tiles || []).map((t) => renderTileHtml(t, sizeCls)).join("");
      return `<div class="meld-group" title="${m.type}">${tilesHtml}</div>`;
    })
    .join("");
}

function renderOpponentHand(pos, seat, snap) {
  const el = document.getElementById(`hand${pos}`);
  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  let html = hand
    .map((t) => renderTileHtml(state.openHands ? t : "?", "tile-opp"))
    .join("");
  if (drawn) {
    html += renderTileHtml(state.openHands ? drawn : "?", "tile-opp", "drawn-gap");
  }
  el.innerHTML = html;
}

// Render Hero Hand with NAGA Probability Bars overlaid directly on top of each tile!
function renderHeroHand(seat, snap, activeDecision) {
  const wrap = document.getElementById("handBottom");
  const banner = document.getElementById("heroActionBanner");
  const verdictPill = document.getElementById("heroVerdictPill");

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  const allTiles = hand.map((pai) => ({ pai, isDrawn: false }));
  if (drawn) {
    allTiles.push({ pai: drawn, isDrawn: true });
  }

  const tileBars = activeDecision?.tile_bars || {};

  // Show verdict pill next to hero header if activeDecision exists
  if (activeDecision) {
    verdictPill.className = `hero-verdict-pill verdict-badge ${activeDecision.verdict}`;
    verdictPill.textContent = `${activeDecision.verdict_cn} · AI推荐: ${activeDecision.expected_cn} (${activeDecision.best_prob.toFixed(1)}%)`;
    verdictPill.classList.remove("hidden");
  } else {
    verdictPill.classList.add("hidden");
  }

  // Render non-discard action comparison banner (Riichi / Call / Pass / Kan / Agari)
  const nonDiscardCands = (activeDecision?.candidates || []).filter((c) => c.label >= 37);
  if (activeDecision && (nonDiscardCands.length > 0 || activeDecision.decision_type === "call_response")) {
    const topCands = activeDecision.candidates.slice(0, 5);
    banner.innerHTML = `
      <span style="font-size:11.5px;color:#94a3b8;font-weight:700">🔔 副露 / 立直决策分布：</span>
      ${topCands
        .map((c) => {
          const isBest = c.rank === 1 ? "is-best" : "";
          const isAct = c.action_cn === activeDecision.actual_cn ? "is-actual" : "";
          return `
            <div class="action-banner-chip ${isBest} ${isAct}" data-label="${c.label}">
              <span>${c.action_cn}</span>
              <span style="color:#38bdf8">${c.prob.toFixed(1)}%</span>
              ${c.rank === 1 ? `<span class="mini-badge ai">AI</span>` : ""}
              ${c.action_cn === activeDecision.actual_cn ? `<span class="mini-badge actual-ok">实</span>` : ""}
            </div>
          `;
        })
        .join("")}
    `;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }

  // Render each tile slot with vertical NAGA probability bar above it
  wrap.innerHTML = allTiles
    .map(({ pai, isDrawn }) => {
      const barInfo = tileBars[pai];
      const prob = barInfo ? barInfo.prob : 0;
      const barHeight = Math.max(prob > 0 ? 3 : 0, Math.round((prob / 100) * 30));

      let slotCls = isDrawn ? "drawn-slot" : "";
      if (barInfo?.is_best) slotCls += " best-tile";
      else if (barInfo?.is_actual) slotCls += " actual-mismatch";

      let badgesHtml = "";
      if (barInfo?.is_best) {
        badgesHtml += `<span class="mini-badge ai" title="AI 最优推荐">AI</span>`;
      }
      if (barInfo?.is_actual) {
        const bCls = barInfo.is_best ? "actual-ok" : "actual-bad";
        badgesHtml += `<span class="mini-badge ${bCls}" title="实战打出的牌">实</span>`;
      }

      const probText = barInfo && prob >= 0.5 ? `${prob.toFixed(prob >= 10 ? 0 : 1)}%` : "";
      const tip = barInfo
        ? `${tileCn(pai)} — AI推荐度: ${prob.toFixed(1)}% | Q:${barInfo.q_value} | 进张:${barInfo.ukeire}枚 | EV:${barInfo.ev} | 放铳率:${barInfo.danger}%`
        : tileCn(pai);

      return `
        <div class="hero-tile-slot ${slotCls}" data-pai="${pai}">
          <div class="naga-bar-area">
            <span class="naga-prob-text">${probText}</span>
            <div class="naga-bar-track">
              <div class="naga-bar-fill" style="height: ${barHeight}px"></div>
            </div>
          </div>
          ${renderTileHtml(pai, "tile-hero", "", tip)}
          <div class="tile-badges-row">${badgesHtml}</div>
        </div>
      `;
    })
    .join("");

  // Clicking a tile in hero hand highlights its candidate row & ukeire breakdown
  wrap.querySelectorAll(".hero-tile-slot").forEach((slot) => {
    slot.addEventListener("click", () => {
      const pai = slot.dataset.pai;
      if (!activeDecision) return;
      const cand = activeDecision.candidates.find((c) => c.pai === pai && c.label <= 36);
      if (cand) {
        showUkeireDetail(cand);
      }
    });
  });
}

// ============================================================================
// Right Column: NAGA Candidate Table & Decision List
// ============================================================================
function renderActionWithTile(actObj, actionCn) {
  if (!actObj) return actionCn || "--";
  if (actObj.pai) {
    return `${renderTileHtml(actObj.pai, "tile-inline")} <span>${actionCn}</span>`;
  }
  return `<span>${actionCn}</span>`;
}

function renderDecisionPanel(kyoku, dec, isExactStep) {
  const badge = document.getElementById("decVerdictBadge");
  const turnChip = document.getElementById("decTurnChip");
  const shantenChip = document.getElementById("decShantenChip");
  const qLossChip = document.getElementById("decQLossChip");
  const furitenChip = document.getElementById("decFuritenChip");
  const aiBox = document.getElementById("compareAiContent");
  const actBox = document.getElementById("compareActualContent");
  const tbody = document.getElementById("candidatesTableBody");
  const ukeireBox = document.getElementById("ukeireDetailBox");

  ukeireBox.classList.add("hidden");

  if (!dec) {
    badge.className = "verdict-badge neutral";
    badge.textContent = "等待决策步骤";
    turnChip.textContent = "第 - 巡";
    shantenChip.textContent = "向听: -";
    qLossChip.textContent = "ΔQ: 0.000";
    furitenChip.classList.add("hidden");
    aiBox.innerHTML = "--";
    actBox.innerHTML = "--";
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">当前步骤无我方决策，点击下方「下一决策 ⏬」跳至我方行动手</td></tr>`;
    return;
  }

  badge.className = `verdict-badge ${dec.verdict}`;
  badge.textContent = `${dec.verdict_cn}${isExactStep ? "" : " (最近一手)"}`;

  turnChip.textContent = `第 ${dec.junme} 巡 · 余 ${dec.tiles_left} 枚`;
  shantenChip.textContent = `当前: ${shantenText(dec.shanten)}`;
  qLossChip.textContent = `权重损失 ΔQ: ${dec.q_loss.toFixed(3)}`;
  furitenChip.classList.toggle("hidden", !dec.at_furiten);

  aiBox.innerHTML = `
    ${renderActionWithTile(dec.expected, dec.expected_cn)}
    <span style="color:#34d399;font-size:13px">(${dec.best_prob.toFixed(1)}%)</span>
  `;

  const actColor = dec.is_equal ? "#38bdf8" : "#fda4af";
  actBox.innerHTML = `
    ${renderActionWithTile(dec.actual, dec.actual_cn)}
    <span style="color:${actColor};font-size:13px">(${dec.actual_prob.toFixed(1)}% · #${dec.actual_rank})</span>
  `;

  // Render candidates table
  tbody.innerHTML = dec.candidates
    .map((c) => {
      const isBest = c.rank === 1;
      const isActual = c.action_cn === dec.actual_cn && c.rank === dec.actual_rank;
      let rowCls = "";
      if (isBest) rowCls = "row-best";
      else if (isActual && !dec.is_equal) rowCls = "row-actual-mismatch";

      const rankBadges = [];
      if (isBest) rankBadges.push(`<span class="mini-badge ai">首选</span>`);
      if (isActual) {
        rankBadges.push(`<span class="mini-badge ${isBest ? "actual-ok" : "actual-bad"}">实战</span>`);
      }

      const shantenStr = shantenText(c.shanten_after);
      const ukeireStr = c.ukeire > 0 ? `${c.ukeire}枚` : "-";

      const evStr = c.ev > 0 ? `EV ${c.ev}` : "-";
      const wpStr = c.win_prob > 0 ? `和率 ${c.win_prob}%` : `听牌 ${c.tenpai_prob}%`;

      const dangerStr = c.danger > 0 ? `<span style="color:${c.danger >= 12 ? "#fda4af" : "#94a3b8"}">铳率 ${c.danger}%</span>` : "";
      const tagsHtml = (c.tags || [])
        .map((t) => {
          const tCls = t.includes("危险") || t.includes("退向") || t.includes("无役")
            ? "danger"
            : t.includes("最") || t.includes("听牌") || t.includes("现物")
            ? "good"
            : "";
          return `<span class="tag-chip ${tCls}">${t}</span>`;
        })
        .join("");

      return `
        <tr class="${rowCls}" data-label="${c.label}">
          <td>
            <div style="font-weight:800">#${c.rank}</div>
            <div>${rankBadges.join(" ")}</div>
          </td>
          <td>
            <div class="cand-action-cell">
              ${c.pai ? renderTileHtml(c.pai, "tile-inline") : ""}
              <span>${c.action_cn}</span>
            </div>
          </td>
          <td>
            <div class="prob-bar-wrap">
              <div class="prob-bar-top">
                <span class="prob-pct">${c.prob.toFixed(1)}%</span>
                <span class="prob-q">Q: ${c.q_value.toFixed(2)}</span>
              </div>
              <div class="prob-track">
                <div class="prob-fill" style="width: ${Math.min(100, c.prob)}%"></div>
              </div>
            </div>
          </td>
          <td>
            <div style="font-weight:700">${shantenStr}</div>
            <div style="font-size:11px;color:#38bdf8">${ukeireStr}</div>
          </td>
          <td>
            <div style="font-weight:700;color:#fbbf24">${evStr}</div>
            <div style="font-size:11px;color:#94a3b8">${wpStr}</div>
          </td>
          <td>
            <div>${dangerStr}</div>
            <div>${tagsHtml}</div>
          </td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll("tr[data-label]").forEach((row) => {
    row.addEventListener("click", () => {
      const label = parseInt(row.dataset.label, 10);
      const cand = dec.candidates.find((c) => c.label === label);
      if (cand) showUkeireDetail(cand);
    });
  });

  // Automatically show top candidate's ukeire if available
  if (dec.candidates[0]?.ukeire_tiles?.length) {
    showUkeireDetail(dec.candidates[0]);
  }
}

function showUkeireDetail(cand) {
  const box = document.getElementById("ukeireDetailBox");
  const tiles = cand.ukeire_tiles || [];
  if (!tiles.length) {
    box.innerHTML = `<span><strong>${cand.action_cn}</strong>：${shantenText(cand.shanten_after)}（无具体进张列表）</span>`;
    box.classList.remove("hidden");
    return;
  }

  const itemsHtml = tiles
    .map(
      (u) => `
      <span class="ukeire-tile-item">
        ${renderTileHtml(u.pai, "tile-dora")}
        <span>×${u.count}</span>
      </span>
    `
    )
    .join("");

  box.innerHTML = `
    <div><strong>${cand.action_cn}</strong> 进张详情 (共 <strong>${cand.ukeire}</strong> 枚)：</div>
    <div class="ukeire-tiles-list">${itemsHtml}</div>
  `;
  box.classList.remove("hidden");
}

function renderKyokuDecisionList(kyoku) {
  const container = document.getElementById("decisionListContainer");
  let decs = kyoku.decisions || [];
  if (state.decisionFilter === "issues") {
    decs = decs.filter((d) => d.verdict === "blunder" || d.verdict === "question");
  }

  if (!decs.length) {
    container.innerHTML = `<div class="empty-cell">本局无符合筛选条件的决策记录</div>`;
    return;
  }

  container.innerHTML = decs
    .map(
      (d) => `
      <div class="decision-row" data-decidx="${d.decision_idx}" data-step="${d.step_idx}">
        <span style="font-weight:700;color:#94a3b8">第 ${d.junme} 巡</span>
        <span>🤖 AI: <strong>${d.expected_cn}</strong> (${d.best_prob.toFixed(0)}%)</span>
        <span>🖐️ 实: <strong>${d.actual_cn}</strong> (${d.actual_prob.toFixed(0)}%)</span>
        <span class="verdict-badge ${d.verdict}">${d.verdict_cn}</span>
      </div>
    `
    )
    .join("");

  container.querySelectorAll(".decision-row").forEach((row) => {
    row.addEventListener("click", () => {
      setStep(parseInt(row.dataset.step, 10));
    });
  });
}

function highlightActiveDecisionInList(decIdx) {
  const container = document.getElementById("decisionListContainer");
  if (!container) return;
  container.querySelectorAll(".decision-row").forEach((row) => {
    const isMatch = parseInt(row.dataset.decidx, 10) === decIdx;
    row.classList.toggle("active", isMatch);
    if (isMatch) {
      const rowTop = row.offsetTop - container.offsetTop;
      const targetScroll = rowTop - container.clientHeight / 2 + row.clientHeight / 2;
      container.scrollTo({ top: Math.max(0, targetScroll), behavior: "smooth" });
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
      if (kIdx !== state.kyokuIdx) {
        selectKyoku(kIdx, false);
      }
      setStep(d.step_idx);
      showToast(`已定位至 ${res.kyokus[kIdx].label} 第${d.junme}巡 (${d.verdict_cn})`, "info");
      return;
    }
  }
  showToast("全庄未发现更多恶手或疑问手 🎉", "success");
}

// ============================================================================
// Modals & Account / JSON / Engine Handlers
// ============================================================================
function renderSavedHistoryList(items) {
  const listEl = document.getElementById("recentGamesList");
  if (!items || !items.length) return;
  listEl.innerHTML = items
    .map((item) => {
      const namesStr = (item.names || []).join(" / ") || "四人麻将";
      return `
        <div class="recent-game-item">
          <div>
            <div style="font-weight:700;color:var(--text-main)">${item.uuid}</div>
            <div style="font-size:11px;color:var(--text-secondary)">${namesStr}</div>
          </div>
          <button class="btn btn-sm btn-primary btn-analyze-uuid" data-uuid="${item.uuid}">分析此局</button>
        </div>
      `;
    })
    .join("");

  listEl.querySelectorAll(".btn-analyze-uuid").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("paipuInput").value = btn.dataset.uuid;
      document.getElementById("accountModal").classList.add("hidden");
      analyzePaipuInput();
    });
  });
}

// ============================================================================
// Adaptive Theme & Responsive Sizing Controller
// ============================================================================
const appearance = {
  theme: localStorage.getItem("mjs_theme") || "majsoul",
  scale: localStorage.getItem("mjs_scale") || "auto",
  compact: localStorage.getItem("mjs_compact") || "auto",
};

function applyTheme(themePref) {
  appearance.theme = themePref;
  localStorage.setItem("mjs_theme", themePref);
  let resolved = themePref;
  if (themePref === "auto") {
    const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    resolved = prefersLight ? "light" : "majsoul";
  }
  document.documentElement.dataset.theme = resolved;
  const sel = document.getElementById("themeSelect");
  if (sel && sel.value !== themePref) sel.value = themePref;
}

function applyCompactMode(compactPref) {
  appearance.compact = compactPref;
  localStorage.setItem("mjs_compact", compactPref);
  const isCompact =
    compactPref === "true" ||
    (compactPref === "auto" && window.innerHeight < 860 && window.innerWidth >= 960);
  document.body.classList.toggle("compact-mode", isCompact);
  const btn = document.getElementById("btnToggleCompact");
  const txt = document.getElementById("compactToggleText");
  if (btn) btn.classList.toggle("active-pill", isCompact);
  if (txt) txt.textContent = isCompact ? "展开概览" : "紧凑一屏";
}

function updateAdaptiveSizing() {
  if (appearance.compact === "auto") {
    applyCompactMode("auto");
  }

  const scalePref = appearance.scale;
  const sel = document.getElementById("scaleSelect");
  if (sel && sel.value !== scalePref) sel.value = scalePref;

  const boardCol = document.querySelector(".board-column");
  const boardW = boardCol ? boardCol.clientWidth : window.innerWidth * 0.52;
  const vh = window.innerHeight;
  const topbarH = document.querySelector(".topbar")?.offsetHeight || 48;
  const summaryH = document.getElementById("summarySection")?.offsetHeight || 86;
  const availH = Math.max(420, vh - topbarH - summaryH - 28);

  if (scalePref !== "auto") {
    const manualScale = parseFloat(scalePref) || 1;
    document.documentElement.style.setProperty("--ui-scale", manualScale.toFixed(3));
    const fitW = Math.min(1, Math.max(0.68, boardW / (650 * manualScale)));
    document.documentElement.style.setProperty("--board-scale", fitW.toFixed(3));
  } else {
    document.documentElement.style.setProperty("--ui-scale", "1");
    const widthScale = Math.min(1.06, Math.max(0.64, boardW / 650));
    const heightScale =
      window.innerWidth >= 1080
        ? Math.min(1.05, Math.max(0.64, (availH - 96) / 655))
        : 1.0;
    const autoBoardScale = Math.min(widthScale, heightScale);
    document.documentElement.style.setProperty("--board-scale", autoBoardScale.toFixed(3));
  }
}

function initAppearanceAndSizing() {
  applyTheme(appearance.theme);
  applyCompactMode(appearance.compact);
  updateAdaptiveSizing();

  // Listen for system color-scheme changes
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      if (appearance.theme === "auto") applyTheme("auto");
    });
  }

  // Observe board column & window resize
  window.addEventListener("resize", updateAdaptiveSizing);
  const boardCol = document.querySelector(".board-column");
  if (boardCol && window.ResizeObserver) {
    const ro = new ResizeObserver(() => updateAdaptiveSizing());
    ro.observe(boardCol);
  }

  document.getElementById("themeSelect")?.addEventListener("change", (e) => {
    applyTheme(e.target.value);
  });

  document.getElementById("scaleSelect")?.addEventListener("change", (e) => {
    appearance.scale = e.target.value;
    localStorage.setItem("mjs_scale", appearance.scale);
    updateAdaptiveSizing();
  });

  document.getElementById("btnToggleCompact")?.addEventListener("click", () => {
    const currentlyCompact = document.body.classList.contains("compact-mode");
    applyCompactMode(currentlyCompact ? "false" : "true");
    updateAdaptiveSizing();
  });
}

function bindEvents() {
  // Topbar actions
  document.getElementById("btnAnalyzePaipu").addEventListener("click", () => analyzePaipuInput());
  document.getElementById("paipuInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") analyzePaipuInput();
  });
  document.getElementById("btnLoadDemo").addEventListener("click", () => loadDemoGame(state.targetActor));

  // Replay controls
  document.getElementById("stepSlider").addEventListener("input", (e) => setStep(parseInt(e.target.value, 10)));
  document.getElementById("btnStepStart").addEventListener("click", () => setStep(0));
  document.getElementById("btnPrevStep").addEventListener("click", () => setStep(state.stepIdx - 1));
  document.getElementById("btnNextStep").addEventListener("click", () => setStep(state.stepIdx + 1));
  document.getElementById("btnPrevDec").addEventListener("click", jumpPrevDecision);
  document.getElementById("btnNextDec").addEventListener("click", jumpNextDecision);
  document.getElementById("btnNextBlunder").addEventListener("click", jumpNextBlunder);
  document.getElementById("btnQuickNextBlunder").addEventListener("click", jumpNextBlunder);

  document.getElementById("btnToggleOpenHands").addEventListener("click", (e) => {
    state.openHands = !state.openHands;
    e.currentTarget.textContent = `👁️ 三家明牌: ${state.openHands ? "开" : "关"}`;
    setStep(state.stepIdx);
  });

  // Decision filters
  document.getElementById("filterAllDecs").addEventListener("click", (e) => {
    state.decisionFilter = "all";
    e.currentTarget.classList.add("active");
    document.getElementById("filterIssueDecs").classList.remove("active");
    if (state.reviewData) renderKyokuDecisionList(state.reviewData.kyokus[state.kyokuIdx]);
  });

  document.getElementById("filterIssueDecs").addEventListener("click", (e) => {
    state.decisionFilter = "issues";
    e.currentTarget.classList.add("active");
    document.getElementById("filterAllDecs").classList.remove("active");
    if (state.reviewData) renderKyokuDecisionList(state.reviewData.kyokus[state.kyokuIdx]);
  });

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      setStep(state.stepIdx - 1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      setStep(state.stepIdx + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      jumpPrevDecision();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      jumpNextDecision();
    } else if (e.key === "b" || e.key === "B") {
      e.preventDefault();
      jumpNextBlunder();
    } else if (e.key === "h" || e.key === "H") {
      e.preventDefault();
      document.getElementById("btnToggleOpenHands").click();
    }
  });

  // Modals open/close
  document.getElementById("btnOpenAccountModal").addEventListener("click", () => {
    document.getElementById("accountModal").classList.remove("hidden");
  });
  document.getElementById("btnOpenJsonModal").addEventListener("click", () => {
    document.getElementById("jsonModal").classList.remove("hidden");
  });
  document.getElementById("btnOpenEngineModal").addEventListener("click", () => {
    document.getElementById("engineModal").classList.remove("hidden");
  });

  document.querySelectorAll(".modal-close").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.close).classList.add("hidden");
    });
  });

  // Account Login / Logout / Recent games
  document.getElementById("btnSubmitLogin").addEventListener("click", async () => {
    const account = document.getElementById("loginAccount").value.trim();
    const password = document.getElementById("loginPassword").value;
    const access_token = document.getElementById("loginToken").value.trim();
    setLoading(true, "正在连接雀魂国服网关并验证登录...");
    try {
      const st = await apiFetch("/api/login", {
        method: "POST",
        body: JSON.stringify({ account, password, access_token, remember: true }),
      });
      updateTopbarStatus(st);
      showToast("雀魂国服登录成功！", "success");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setLoading(false);
    }
  });

  document.getElementById("btnLogout").addEventListener("click", async () => {
    const st = await apiFetch("/api/logout", { method: "POST" });
    updateTopbarStatus(st);
    showToast("已清除本地雀魂凭证", "info");
  });

  document.getElementById("btnFetchRecentGames").addEventListener("click", async () => {
    setLoading(true, "正在从雀魂国服拉取最近对局列表...");
    try {
      const data = await apiFetch("/api/recent_games");
      renderSavedHistoryList(data.games || []);
      showToast(`已获取 ${data.games?.length || 0} 场最近对局`, "success");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setLoading(false);
    }
  });

  // Local JSON Upload / Paste
  document.getElementById("jsonFileInput").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    document.getElementById("jsonPasteArea").value = text;
    showToast(`已读取文件: ${file.name}`, "info");
  });

  document.getElementById("btnAnalyzeJson").addEventListener("click", async () => {
    const raw = document.getElementById("jsonPasteArea").value.trim();
    if (!raw) {
      showToast("请先选择牌谱文件或粘贴 JSON 内容", "error");
      return;
    }
    document.getElementById("jsonModal").classList.add("hidden");
    setLoading(true, "正在解析并检讨本地 JSON 牌谱...");
    try {
      const res = await apiFetch("/api/analyze/json", {
        method: "POST",
        body: JSON.stringify({
          content: raw,
          target_actor: state.targetActor,
          engine: document.getElementById("engineSelect").value,
        }),
      });
      applyReviewResult(res);
      showToast("本地 JSON 牌谱分析完成！", "success");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setLoading(false);
    }
  });

  // Engine Config Save / Reset
  document.getElementById("btnSaveEngineConfig").addEventListener("click", async () => {
    const model_path = document.getElementById("modelPathInput").value.trim();
    try {
      await apiFetch("/api/engine/config", {
        method: "POST",
        body: JSON.stringify({ model_path: model_path || null }),
      });
      await refreshServerStatus();
      showToast("引擎配置已更新", "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  document.getElementById("btnResetEngineConfig").addEventListener("click", async () => {
    document.getElementById("modelPathInput").value = "";
    await apiFetch("/api/engine/config", {
      method: "POST",
      body: JSON.stringify({ model_path: null }),
    });
    await refreshServerStatus();
    showToast("已恢复默认权重检测路径 (data/models/mortal.pth)", "info");
  });
}

// ============================================================================
// Initialize App on DOMContentLoaded
// ============================================================================
window.addEventListener("DOMContentLoaded", async () => {
  initAppearanceAndSizing();
  bindEvents();
  await refreshServerStatus();
  await loadDemoGame(0);
});
