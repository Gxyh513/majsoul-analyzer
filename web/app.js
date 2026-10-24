/**
 * 牌谱检讨 — 仿 KillerDucky Mortal Web GUI 风格
 * 雀魂 AI 牌谱检讨系统前端控制器
 */

const state = window.state = {
  reviewData: null,
  gameId: "demo",
  targetActor: 0,
  kyokuIdx: 0,
  stepIdx: 0,
  openHands: true,
  selectedModel: "akagi",
  serverStatus: null,
};

const WIND_CN = ["东", "南", "西", "北"];
const TILE_CN_MAP = {
  E: "东", S: "南", W: "西", N: "北", P: "白", F: "发", C: "中",
  "5mr": "赤五万", "5pr": "赤五筒", "5sr": "赤五索",
};

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

function fmtPct(v, digits = 1) {
  if (v === undefined || v === null || isNaN(v)) return "--%";
  return `${Number(v).toFixed(digits)}%`;
}

function showToast(msg, type = "info") {
  const container = $("toastContainer");
  if (!container) return;
  const t = document.createElement("div");
  t.className = `kd-toast ${type}`;
  t.textContent = msg;
  container.appendChild(t);
  setTimeout(() => {
    t.style.opacity = "0";
    t.style.transition = "opacity 0.3s";
    setTimeout(() => t.remove(), 300);
  }, 3500);
}

function setLoading(visible, title = "正在分析牌谱...", sub = "") {
  const overlay = $("loadingOverlay");
  if (!overlay) return;
  if (visible) {
    $("loadingTitle").textContent = title;
    $("loadingSub").textContent = sub || "赤木神经网络正在检讨每一手决策";
    overlay.classList.remove("hidden");
  } else {
    overlay.classList.add("hidden");
  }
}

const DEFAULT_MODELSCOPE_BACKEND = "https://gxyh513-majsoul-analyzer.ms.show?studio_token=39607f51-2d00-4fdd-9ee1-c121d670b498";

function getBackendUrl() {
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
    const full = new URL(path.replace(/^\//, ""), base.href.endsWith("/") ? base.href : base.href + "/");
    for (const [k, v] of base.searchParams.entries()) {
      if (!full.searchParams.has(k)) full.searchParams.set(k, v);
    }
    if (studioToken && !full.searchParams.has("studio_token")) {
      full.searchParams.set("studio_token", studioToken);
    }
    return full.toString();
  } catch (e) {
    return backend ? `${backend.replace(/\/+$/, "")}/${path.replace(/^\//, "")}` : path;
  }
}

async function apiFetch(path, options = {}) {
  const isAbs = /^https?:\/\//i.test(path);
  const backend = getBackendUrl();
  const url = isAbs ? path : buildApiUrl(backend, path);

  const resp = await fetch(url, {
    mode: "cors",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

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
  }
}

function updateTopbarStatus(st) {
  const eng = st.engine || {};
  const btn = $("engineStatusText");
  if (btn) {
    if (eng.active_engine === "mortal" || eng.has_mortal_weights) {
      btn.textContent = `${eng.model_name || "赤木 Akagi"} · ${eng.device || "CPU"}`;
    } else {
      btn.textContent = "无神经网络权重";
    }
  }

  const sel = $("engineSelect");
  if (sel && eng.available_models) {
    sel.innerHTML = eng.available_models
      .map((m) => `<option value="${m.id}">${escapeHtml(m.name)}</option>`)
      .join("");
    if (eng.selected_model) sel.value = eng.selected_model;
  }

  const detail = $("engineModalDetail");
  if (detail) {
    detail.innerHTML = `
      <div><b>当前模型：</b>${escapeHtml(eng.model_name || "-")} (${escapeHtml(eng.model_tag || "-")})</div>
      <div><b>权重路径：</b>${escapeHtml(eng.model_path || "-")}</div>
      <div><b>计算硬件：</b>${escapeHtml(eng.device || "CPU")}</div>
    `;
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

// ============================================================================
// Two-Tier Navigation: Switch between Landing and Review Screen
// ============================================================================
function showTier(tierNum) {
  const t1 = $("tier1Screen");
  const t2 = $("tier2Screen");
  if (tierNum === 1) {
    t1.classList.remove("hidden");
    t2.classList.add("hidden");
    window.scrollTo(0, 0);
  } else {
    t1.classList.add("hidden");
    t2.classList.remove("hidden");
    window.scrollTo(0, 0);
  }
}

async function startAnalysisFromInput(rawInput, model = "akagi", seat = 0) {
  if (!rawInput) {
    showToast("请先输入雀魂牌谱链接或牌谱号", "error");
    return;
  }
  const paipuId = parsePaipuUuid(rawInput);
  setLoading(true, `正在分析牌谱 ${paipuId}...`, "正在使用深度神经网络检讨每一手决策");

  try {
    const res = await apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify({ paipu: paipuId, target_actor: seat, engine: model }),
    });

    state.reviewData = res;
    state.gameId = res.game_id || paipuId;
    state.targetActor = res.target_actor;
    state.kyokuIdx = 0;

    showTier(2);

    const newUrl = new URL(window.location.href);
    newUrl.searchParams.set("paipu", paipuId);
    window.history.pushState(null, "", newUrl.toString());

    renderSummaryDashboard();
    selectKyoku(0, true);
    showToast(`分析完成：共检讨 ${res.kyokus?.length || 0} 局`, "success");
  } catch (err) {
    showToast(`分析失败：${err.message}`, "error");
  } finally {
    setLoading(false);
  }
}

async function switchSeatPerspective(newSeat) {
  if (!state.reviewData || newSeat === state.targetActor) return;
  const paipuId = state.gameId;
  if (!paipuId) return;
  setLoading(true, `切换到「${state.reviewData.names[newSeat]}」视角...`);
  try {
    const res = await apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify({ paipu: paipuId, target_actor: newSeat, engine: state.selectedModel }),
    });
    state.reviewData = res;
    state.targetActor = res.target_actor;
    renderSummaryDashboard();
    selectKyoku(state.kyokuIdx, true);
  } catch (err) {
    showToast(`切换视角失败：${err.message}`, "error");
  } finally {
    setLoading(false);
  }
}

function handWind(seat, kyoku) {
  return WIND_CN[(seat - (kyoku.oya ?? 0) + 4) % 4];
}

function renderSummaryDashboard() {
  const res = state.reviewData;
  if (!res) return;

  renderSeatTabs();

  const sum = res.summary || {};
  const pill = $("gameSummaryPill");
  if (pill) {
    pill.textContent = `AI 一致率: ${fmtPct(sum.match_rate)} | 恶手: ${sum.blunders || 0} | Rating: ${(sum.rating ?? 0).toFixed(1)}`;
  }
}

function renderSeatTabs() {
  const res = state.reviewData;
  if (!res) return;
  const oya = res.kyokus?.[state.kyokuIdx]?.oya ?? 0;
  const seatTabs = $("seatTabs");
  if (!seatTabs) return;
  seatTabs.innerHTML = res.names
    .map((name, seat) => {
      const wind = WIND_CN[(seat - oya + 4) % 4];
      const active = seat === res.target_actor ? "active" : "";
      return `<button class="kd-tab ${active}" data-seat="${seat}" role="tab" title="${escapeHtml(name)}">
        <b>${wind}家</b> ${escapeHtml(name)}
      </button>`;
    })
    .join("");
  seatTabs.querySelectorAll(".kd-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchSeatPerspective(parseInt(btn.dataset.seat, 10)));
  });
}

function selectKyoku(kIdx, jumpToFirstDecision = false) {
  const res = state.reviewData;
  if (!res || !res.kyokus[kIdx]) return;
  state.kyokuIdx = kIdx;
  renderSeatTabs();

  const kyoku = res.kyokus[kIdx];
  const slider = $("stepSlider");
  if (slider) slider.max = Math.max(0, kyoku.steps.length - 1);

  if (jumpToFirstDecision && kyoku.decisions && kyoku.decisions.length > 0) {
    setStep(kyoku.decisions[0].step_idx);
  } else {
    setStep(0);
  }
}

function stepDelta(delta) {
  const kyoku = state.reviewData?.kyokus[state.kyokuIdx];
  if (!kyoku) return;
  const target = state.stepIdx + delta;
  if (target < 0) {
    if (state.kyokuIdx > 0) {
      selectKyoku(state.kyokuIdx - 1);
      const prevKyoku = state.reviewData.kyokus[state.kyokuIdx];
      setStep(prevKyoku.steps.length - 1);
    }
  } else if (target >= kyoku.steps.length) {
    if (state.kyokuIdx < state.reviewData.kyokus.length - 1) {
      selectKyoku(state.kyokuIdx + 1, true);
    }
  } else {
    setStep(target);
  }
}

function setStep(stepIdx) {
  const res = state.reviewData;
  if (!res) return;
  const kyoku = res.kyokus[state.kyokuIdx];
  if (!kyoku || !kyoku.steps.length) return;

  const clamped = Math.max(0, Math.min(kyoku.steps.length - 1, stepIdx));
  state.stepIdx = clamped;

  if ($("stepSlider")) $("stepSlider").value = clamped;
  if ($("stepCounterText")) $("stepCounterText").textContent = `${clamped} / ${kyoku.steps.length - 1}`;

  const snap = kyoku.steps[clamped];
  const isExactDecisionStep = snap.decision_idx !== null && snap.decision_idx !== undefined;
  const decision = isExactDecisionStep ? kyoku.decisions[snap.decision_idx] : findLatestDecisionBeforeStep(kyoku, clamped);

  renderBoard(kyoku, snap, isExactDecisionStep ? decision : null);
  renderDecisionCard(decision, isExactDecisionStep);
}

function findLatestDecisionBeforeStep(kyoku, stepIdx) {
  let found = null;
  for (const d of kyoku.decisions || []) {
    if (d.step_idx <= stepIdx) found = d;
    else break;
  }
  return found;
}

// ============================================================================
// Board Rendering (KillerDucky 4-Player Layout with Green Discard Bars)
// ============================================================================
function renderBoard(kyoku, snap, activeDecision) {
  const hero = state.targetActor;

  // Center Info Box
  const roundName = (kyoku.label || "").split(" ")[0] || "东1";
  const btnRound = $("btnRoundScore");
  if (btnRound) btnRound.textContent = roundName;

  const tilesLeftEl = $("centerTilesLeft");
  if (tilesLeftEl) tilesLeftEl.textContent = `x${snap.tiles_left ?? 70}`;

  const honbaEl = $("centerHonba");
  if (honbaEl) honbaEl.textContent = snap.honba > 0 ? `${snap.honba}本场` : "";

  // 5 Dora slots
  const dorasEl = $("centerDoras");
  if (dorasEl) {
    const markers = snap.dora_markers || [];
    let doraHtml = "";
    for (let i = 0; i < 5; i++) {
      const m = markers[i];
      const img = m ? `/tiles/${encodeURIComponent(m)}.svg` : `/tiles/back.svg`;
      doraHtml += `<div class="tileDiv"><img src="${img}" class="tileImg"></div>`;
    }
    dorasEl.innerHTML = doraHtml;
  }

  // 4 Seat positions relative to hero
  const p1 = (hero + 1) % 4; // Right
  const p2 = (hero + 2) % 4; // Top
  const p3 = (hero + 3) % 4; // Left

  const giP0 = $("giP0");
  if (giP0) giP0.textContent = `${handWind(hero, kyoku)} ${snap.scores[hero] ?? 25000}`;
  const giP1 = $("giP1");
  if (giP1) giP1.textContent = `${handWind(p1, kyoku)} ${snap.scores[p1] ?? 25000}`;
  const giP2 = $("giP2");
  if (giP2) giP2.textContent = `${handWind(p2, kyoku)} ${snap.scores[p2] ?? 25000}`;
  const giP3 = $("giP3");
  if (giP3) giP3.textContent = `${handWind(p3, kyoku)} ${snap.scores[p3] ?? 25000}`;

  // Rivers
  renderPlayerRiver("Bottom", hero, snap);
  renderPlayerRiver("Right", p1, snap);
  renderPlayerRiver("Top", p2, snap);
  renderPlayerRiver("Left", p3, snap);

  // Hands
  renderHeroHand(hero, snap, activeDecision);
  renderOpponentHand("Right", p1, snap);
  renderOpponentHand("Top", p2, snap);
  renderOpponentHand("Left", p3, snap);
}

function renderPlayerRiver(pos, seat, snap) {
  const container = $(`river${pos}`);
  if (!container) return;
  const river = snap.rivers[seat] || [];
  const isLastDiscardActor = snap.event?.type === "dahai" && snap.event?.actor === seat;

  let html = "";
  let slotCount = 0;

  river.forEach((r, idx) => {
    // KillerDucky 6-tile row wrap: insert 4 invisible placeholder tiles after tile 6 and tile 12
    if (slotCount === 6 || slotCount === 16) {
      for (let j = 0; j < 4; j++) {
        html += `<div class="tileDiv" style="opacity:0; pointer-events:none;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
        slotCount++;
      }
    }

    const isLast = isLastDiscardActor && idx === river.length - 1;
    const cls = ["tileImg"];
    if (r.tsumogiri) cls.push("tsumogiri");
    if (r.riichi) cls.push("rotate");
    if (r.called) cls.push("called");
    if (isLast) cls.push("last-discard");

    const tip = `${tileCn(r.pai)}（${r.tsumogiri ? "摸切" : "手切"}${r.riichi ? " · 立直宣言" : ""}${r.called ? " · 被鸣" : ""}）`;
    const divCls = ["tileDiv"];
    if (r.riichi) divCls.push("has-rotate");
    if (isLast) divCls.push("has-last-discard");

    html += `<div class="${divCls.join(" ")}" title="${escapeHtml(tip)}"><img src="/tiles/${encodeURIComponent(r.pai)}.svg" class="${cls.join(" ")}"></div>`;
    slotCount++;
  });

  container.innerHTML = html;
}

function renderHeroHand(seat, snap, activeDecision) {
  const closedWrap = $("handBottomClosed");
  const callsWrap = $("handBottomCalls");
  const svgEl = $("discardBarsSvg");
  if (!closedWrap) return;

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  const allTiles = hand.map((pai) => ({ pai, isDrawn: false }));
  if (drawn) allTiles.push({ pai: drawn, isDrawn: true });

  const tileBars = activeDecision?.tile_bars || {};
  let actualPai = activeDecision?.actual?.pai;
  if (!actualPai && snap.event?.type === "dahai" && snap.event?.actor === seat) {
    actualPai = snap.event.pai;
  }

  // Render Green Discard Recommendation Bars (SVG)
  let svgHtml = "";
  let highlightedIdx = -1;

  allTiles.forEach(({ pai, isDrawn }, idx) => {
    const barInfo = tileBars[pai];
    const prob = barInfo ? barInfo.prob : 0;
    const slot = isDrawn ? idx + 0.3 : idx;
    const xloc = 15 + 17 + slot * 34; // 15px hand padding + 17px half-tile + idx*34px
    if (prob > 0) {
      const height = Math.max(3, Math.round((prob / 100) * 45));
      const y = 50 - height;
      svgHtml += `<rect x="${xloc - 5}" y="${y}" width="10" height="${height}" fill="hsl(120, 70%, 34%)" rx="1"></rect>`;
    }
    if (actualPai === pai && highlightedIdx === -1) {
      highlightedIdx = idx;
    }
  });

  if (svgEl) svgEl.innerHTML = svgHtml;

  // Render Closed Hand Tiles
  let handHtml = "";
  hand.forEach((pai, idx) => {
    const isAct = highlightedIdx === idx;
    handHtml += `<div class="tileDiv" data-pai="${escapeHtml(pai)}"><img src="/tiles/${encodeURIComponent(pai)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""}"></div>`;
  });

  if (drawn) {
    const isAct = highlightedIdx === hand.length;
    handHtml += `<div class="tileDiv narrow" style="opacity:0;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
    handHtml += `<div class="tileDiv" data-pai="${escapeHtml(drawn)}"><img src="/tiles/${encodeURIComponent(drawn)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""}"></div>`;
  }

  closedWrap.innerHTML = handHtml;

  // Render Calls
  if (callsWrap) {
    const melds = snap.melds[seat] || [];
    callsWrap.innerHTML = melds.map((m) => renderMeldHtml(m, seat, "p0")).join("");
  }
}

function renderOpponentHand(pos, seat, snap) {
  const closedWrap = $(`hand${pos}Closed`);
  const callsWrap = $(`hand${pos}Calls`);
  if (!closedWrap) return;

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  let html = "";

  hand.forEach((pai) => {
    const src = state.openHands ? `/tiles/${encodeURIComponent(pai)}.svg` : `/tiles/back.svg`;
    html += `<div class="tileDiv"><img src="${src}" class="tileImg"></div>`;
  });

  if (drawn) {
    const src = state.openHands ? `/tiles/${encodeURIComponent(drawn)}.svg` : `/tiles/back.svg`;
    html += `<div class="tileDiv narrow" style="opacity:0;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
    html += `<div class="tileDiv"><img src="${src}" class="tileImg"></div>`;
  }

  closedWrap.innerHTML = html;

  if (callsWrap) {
    const melds = snap.melds[seat] || [];
    callsWrap.innerHTML = melds.map((m) => renderMeldHtml(m, seat, pos.toLowerCase())).join("");
  }
}

function renderMeldHtml(m, ownerSeat, pov) {
  const tiles = [...(m.tiles || [])];
  if (!tiles.length) return "";
  let sideIdx = -1;
  let order = tiles;
  if (m.type === "ankan") {
    return `<div style="display:flex;">${tiles
      .map((t, i) => {
        const src = i === 0 || i === tiles.length - 1 ? "/tiles/back.svg" : `/tiles/${encodeURIComponent(t)}.svg`;
        return `<div class="tileDiv"><img src="${src}" class="tileImg"></div>`;
      })
      .join("")}</div>`;
  }
  if (m.from !== undefined && m.from !== null) {
    const called = tiles[0];
    const rest = tiles.slice(1);
    const rel = (m.from - ownerSeat + 4) % 4;
    if (rel === 3) { order = [called, ...rest]; sideIdx = 0; }
    else if (rel === 2) { order = [rest[0], called, ...rest.slice(1)]; sideIdx = 1; }
    else { order = [...rest, called]; sideIdx = order.length - 1; }
  }
  return `<div style="display:flex;">${order
    .map((t, i) => {
      const isRotated = i === sideIdx;
      return `<div class="tileDiv"><img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg ${isRotated ? "rotate" : ""}"></div>`;
    })
    .join("")}</div>`;
}

// ============================================================================
// Active Decision Details Card (In Right Sidebar)
// ============================================================================
function renderDecisionCard(dec, isExactStep) {
  const card = $("decisionCard");
  if (!card) return;

  const turnEl = $("decTurnText");
  const badgeEl = $("decVerdictBadge");
  const aiEl = $("decAiChoice");
  const actEl = $("decActualChoice");
  const tableBody = $("decCandTableBody");

  if (!dec) {
    turnEl.textContent = "当前巡目";
    badgeEl.textContent = "巡航中";
    badgeEl.className = "kd-dec-verdict best";
    aiEl.textContent = "--";
    actEl.textContent = "--";
    tableBody.innerHTML = `<tr><td colspan="5" style="padding:10px; opacity:0.6;">暂无 AI 决策打法建议</td></tr>`;
    return;
  }

  const isMatch = dec.is_equal;
  const currentKyoku = state.reviewData?.kyokus[state.kyokuIdx];
  turnEl.textContent = `${currentKyoku ? handWind(dec.actor ?? 0, currentKyoku) : ""}家切牌`;

  if (isMatch) {
    badgeEl.textContent = "与 AI 一致";
    badgeEl.className = "kd-dec-verdict best";
  } else if (dec.verdict === "blunder") {
    badgeEl.textContent = "恶手";
    badgeEl.className = "kd-dec-verdict blunder";
  } else {
    badgeEl.textContent = dec.verdict_cn || "微差";
    badgeEl.className = "kd-dec-verdict question";
  }

  const expPai = dec.expected?.pai;
  const actPai = dec.actual?.pai;
  aiEl.innerHTML = expPai
    ? `<img src="/tiles/${encodeURIComponent(expPai)}.svg" class="tileImg" style="width:16px;height:22px;display:inline-block;vertical-align:middle;"> ${tileCn(expPai)} (${fmtPct(dec.expected?.prob)})`
    : escapeHtml(dec.expected_cn || "-");

  actEl.innerHTML = actPai
    ? `<img src="/tiles/${encodeURIComponent(actPai)}.svg" class="tileImg" style="width:16px;height:22px;display:inline-block;vertical-align:middle;"> ${tileCn(actPai)}`
    : escapeHtml(dec.actual_cn || "-");

  const cands = (dec.candidates || []).slice(0, 5);
  tableBody.innerHTML = cands
    .map((c, i) => {
      const pai = c.pai || c.action?.pai;
      const prob = fmtPct(c.prob);
      const isAct = c.action_cn === dec.actual_cn || (pai && pai === dec.actual?.pai);
      const isAi = c.rank === 1;
      const tileDisplay = pai
        ? `<img src="/tiles/${encodeURIComponent(pai)}.svg" class="tileImg" style="width:16px;height:22px;display:inline-block;vertical-align:middle;">`
        : escapeHtml(c.action_cn || "-");
      const style = isAct ? "background:rgba(255, 68, 68, 0.25);" : isAi ? "background:rgba(34, 197, 94, 0.15);" : "";
      return `
        <tr style="${style}">
          <td>#${i + 1}</td>
          <td>${tileDisplay}</td>
          <td style="font-weight:bold; color:${isAi ? "hsl(120,70%,45%)" : "inherit"}">${prob}</td>
          <td>${shantenText(c.shanten)}</td>
          <td>${c.ukeire ?? "-"}</td>
        </tr>
      `;
    })
    .join("");
}

// ============================================================================
// Navigation Jumps
// ============================================================================
function jumpPrevKyoku() {
  if (state.kyokuIdx > 0) selectKyoku(state.kyokuIdx - 1, true);
}

function jumpNextKyoku() {
  const maxIdx = (state.reviewData?.kyokus?.length || 1) - 1;
  if (state.kyokuIdx < maxIdx) selectKyoku(state.kyokuIdx + 1, true);
}

function jumpPrevDecision() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.decisions?.length) return;
  const decs = kyoku.decisions;
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < state.stepIdx) {
      setStep(decs[i].step_idx);
      return;
    }
  }
  if (state.kyokuIdx > 0) {
    jumpPrevKyoku();
    const prevKyoku = state.reviewData.kyokus[state.kyokuIdx];
    if (prevKyoku?.decisions?.length) {
      setStep(prevKyoku.decisions[prevKyoku.decisions.length - 1].step_idx);
    }
  }
}

function jumpNextDecision() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.decisions?.length) return;
  for (const d of kyoku.decisions) {
    if (d.step_idx > state.stepIdx) {
      setStep(d.step_idx);
      return;
    }
  }
  if (state.kyokuIdx < state.reviewData.kyokus.length - 1) {
    jumpNextKyoku();
  }
}

function jumpPrevBlunder() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.decisions?.length) return;
  const decs = kyoku.decisions;
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < state.stepIdx && !decs[i].is_equal) {
      setStep(decs[i].step_idx);
      return;
    }
  }
  for (let k = state.kyokuIdx - 1; k >= 0; k--) {
    const prevK = state.reviewData.kyokus[k];
    const prevDecs = prevK?.decisions || [];
    for (let i = prevDecs.length - 1; i >= 0; i--) {
      if (!prevDecs[i].is_equal) {
        selectKyoku(k);
        setStep(prevDecs[i].step_idx);
        return;
      }
    }
  }
  showToast("前面没有更多错误决策了", "info");
}

function jumpNextBlunder() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.decisions?.length) return;
  for (const d of kyoku.decisions) {
    if (d.step_idx > state.stepIdx && !d.is_equal) {
      setStep(d.step_idx);
      return;
    }
  }
  for (let k = state.kyokuIdx + 1; k < state.reviewData.kyokus.length; k++) {
    const nextK = state.reviewData.kyokus[k];
    for (const d of nextK?.decisions || []) {
      if (!d.is_equal) {
        selectKyoku(k);
        setStep(d.step_idx);
        return;
      }
    }
  }
  showToast("本局及后续没有更多错误手了", "info");
}

// ============================================================================
// Modals: Round Scores & Engine Switcher
// ============================================================================
function openModal(id) {
  const el = $(id);
  if (!el) return;
  el.classList.remove("hidden");
  const bd = $(id + "Backdrop");
  if (bd) bd.classList.remove("hidden");
  el.querySelector("input, textarea, select, button")?.focus();
}

function closeModal(id) {
  const el = $(id);
  if (el) el.classList.add("hidden");
  const bd = $(id + "Backdrop");
  if (bd) bd.classList.add("hidden");
}

function openRoundScoresModal() {
  const res = state.reviewData;
  if (!res || !res.kyokus) return;
  const tbody = $("roundTableBody");
  if (!tbody) return;

  tbody.innerHTML = res.kyokus
    .map((k, idx) => {
      const oyaWind = handWind(k.oya, k);
      const decCount = k.decision_count || (k.decisions ? k.decisions.length : 0);
      const matchCount = k.match_count || (k.decisions ? k.decisions.filter((d) => d.is_equal).length : 0);
      const matchPct = decCount > 0 ? ((matchCount / decCount) * 100).toFixed(1) + "%" : "--";
      const resultText = k.end_summaries && k.end_summaries.length ? k.end_summaries.join("<br>") : k.result || "流局";
      const blunders = k.blunder_count || 0;
      return `
        <tr data-kidx="${idx}">
          <td style="font-weight:bold; color:var(--color-text-bright);">${escapeHtml(k.label || `东${idx + 1}`)}</td>
          <td>${oyaWind}家</td>
          <td>${resultText}</td>
          <td>${k.points_delta !== undefined ? k.points_delta : "--"}</td>
          <td style="color:hsl(120,70%,45%); font-weight:bold;">${matchPct}</td>
          <td style="color:${blunders > 0 ? "hsl(0,70%,60%)" : "inherit"}; font-weight:${blunders > 0 ? "bold" : "normal"};">${blunders}</td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      const kidx = parseInt(tr.dataset.kidx, 10);
      selectKyoku(kidx, true);
      closeModal("roundModal");
    });
  });

  openModal("roundModal");
}

// ============================================================================
// Event Bindings
// ============================================================================
function bindEvents() {
  // Tier 1 Model Selector Buttons
  const btnAkagi = $("btnModelAkagi");
  const btnMortal = $("btnModelMortal");
  btnAkagi?.addEventListener("click", () => {
    state.selectedModel = "akagi";
    btnAkagi.classList.add("active");
    btnMortal.classList.remove("active");
  });
  btnMortal?.addEventListener("click", () => {
    state.selectedModel = "mortal";
    btnMortal.classList.add("active");
    btnAkagi.classList.remove("active");
  });

  // Tier 1 Submit
  $("btnHomeAnalyze")?.addEventListener("click", () => {
    const val = $("homePaipuInput")?.value.trim();
    startAnalysisFromInput(val, state.selectedModel);
  });
  $("homePaipuInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const val = $("homePaipuInput")?.value.trim();
      startAnalysisFromInput(val, state.selectedModel);
    }
  });

  // Tier 2 Back to Home
  $("btnBackToHome")?.addEventListener("click", () => {
    showTier(1);
    const newUrl = new URL(window.location.href);
    newUrl.searchParams.delete("paipu");
    window.history.pushState(null, "", newUrl.toString());
    $("homePaipuInput")?.focus();
  });

  // Step timeline slider
  $("stepSlider")?.addEventListener("input", (e) => setStep(parseInt(e.target.value, 10)));

  // Navigation controls
  $("btnPrevKyoku")?.addEventListener("click", jumpPrevKyoku);
  $("btnNextKyoku")?.addEventListener("click", jumpNextKyoku);
  $("btnPrevBlunder")?.addEventListener("click", jumpPrevBlunder);
  $("btnNextBlunder")?.addEventListener("click", jumpNextBlunder);
  $("btnPrevDec")?.addEventListener("click", jumpPrevDecision);
  $("btnNextDec")?.addEventListener("click", jumpNextDecision);
  $("btnPrevStep")?.addEventListener("click", () => stepDelta(-1));
  $("btnNextStep")?.addEventListener("click", () => stepDelta(1));

  // Round modal
  $("btnRoundScore")?.addEventListener("click", openRoundScoresModal);
  $("roundModalClose")?.addEventListener("click", () => closeModal("roundModal"));

  // Engine modal
  $("btnOpenEngineModal")?.addEventListener("click", () => openModal("engineModal"));
  $("engineModalClose")?.addEventListener("click", () => closeModal("engineModal"));
  $("btnSaveEngineConfig")?.addEventListener("click", async () => {
    const model = $("engineSelect")?.value || "akagi";
    state.selectedModel = model;
    closeModal("engineModal");
    if (state.reviewData && state.gameId) {
      await startAnalysisFromInput(state.gameId, model, state.targetActor);
    }
  });

  // Backdrop click closes any modal
  document.querySelectorAll(".modal-backdrop").forEach((bd) => {
    bd.addEventListener("click", () => {
      document.querySelectorAll(".modal:not(.hidden)").forEach((m) => m.classList.add("hidden"));
      bd.classList.add("hidden");
    });
  });

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal:not(.hidden)").forEach((m) => m.classList.add("hidden"));
      document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((b) => b.classList.add("hidden"));
      return;
    }
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    if (document.querySelector(".modal:not(.hidden)")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    const actions = {
      ArrowLeft: () => stepDelta(-1),
      ArrowRight: () => stepDelta(1),
      ArrowUp: jumpPrevDecision,
      ArrowDown: jumpNextDecision,
      PageUp: jumpPrevBlunder,
      PageDown: jumpNextBlunder,
      ",": jumpPrevBlunder,
      ".": jumpNextBlunder,
      Home: () => setStep(0),
      End: () => {
        const k = state.reviewData?.kyokus?.[state.kyokuIdx];
        if (k) setStep(k.steps.length - 1);
      },
      "[": jumpPrevKyoku,
      "]": jumpNextKyoku,
      h: () => {
        state.openHands = !state.openHands;
        if (state.reviewData) setStep(state.stepIdx);
      },
      H: () => {
        state.openHands = !state.openHands;
        if (state.reviewData) setStep(state.stepIdx);
      },
    };

    if (actions[e.key]) {
      e.preventDefault();
      actions[e.key]();
    }
  });

  // Mouse wheel over table steps forward/backward
  $("gridMain")?.addEventListener("wheel", (e) => {
    if (!state.reviewData) return;
    e.preventDefault();
    if (e.deltaY > 0) stepDelta(1);
    else if (e.deltaY < 0) stepDelta(-1);
  }, { passive: false });
}

// ============================================================================
// Initialization
// ============================================================================
window.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await refreshServerStatus();

  // Check URL query for paipu parameter
  const urlParams = new URLSearchParams(window.location.search);
  const paipuParam = urlParams.get("paipu");
  if (paipuParam) {
    $("homePaipuInput").value = paipuParam;
    await startAnalysisFromInput(paipuParam, "akagi", 0);
  } else {
    showTier(1);
  }
});
