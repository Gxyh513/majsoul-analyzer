/**
 * 牌谱检讨 — 仿 KillerDucky Mortal Web GUI 风格
 * 雀魂 AI 牌谱检讨系统前端控制器
 */

const state = window.state = {
  reviewData: null,
  perspectiveCache: {},
  gameId: "demo",
  targetActor: 0,
  kyokuIdx: 0,
  stepIdx: 0,
  openHands: true,
  selectedModel: "akagi",
  serverStatus: null,
  isPlaying: false,
  playTimer: null,
  currentTheme: localStorage.getItem("majsoul_theme_pref") || "apple-dark",
  customBack: localStorage.getItem("majsoul_back_pref") || "auto",
  hidePlayerId: localStorage.getItem("majsoul_hide_id") === "true",
};

const WIND_CN = ["东", "南", "西", "北"];
const TILE_CN_MAP = {
  E: "东", S: "南", W: "西", N: "北", P: "白", F: "发", C: "中",
  "5mr": "赤五万", "5pr": "赤五筒", "5sr": "赤五索",
};

const $ = (id) => document.getElementById(id);

function getPlayerDisplayName(seat) {
  if (state.hidePlayerId) {
    return `雀氏${(seat ?? 0) + 1}`;
  }
  return state.reviewData?.names?.[seat] || `雀氏${(seat ?? 0) + 1}`;
}

function sanitizeSummaryText(text) {
  if (!text) return "";
  if (!state.hidePlayerId || !state.reviewData?.names) return text;
  let s = String(text);
  state.reviewData.names.forEach((origName, seat) => {
    if (origName) {
      s = s.split(origName).join(`雀氏${seat + 1}`);
    }
  });
  return s;
}

function toggleHidePlayerId() {
  state.hidePlayerId = !state.hidePlayerId;
  localStorage.setItem("majsoul_hide_id", state.hidePlayerId ? "true" : "false");
  applyHidePlayerId();
}

function applyHidePlayerId() {
  const btn = $("btnToggleHideId");
  if (btn) {
    btn.textContent = state.hidePlayerId ? "显示ID" : "隐藏ID";
    if (state.hidePlayerId) btn.classList.add("active");
    else btn.classList.remove("active");
  }
  document.body.classList.toggle("hide-player-ids", state.hidePlayerId);
  renderSeatTabs();
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (kyoku) {
    const snap = kyoku?.steps?.[state.stepIdx];
    const dec = kyoku?.decisions?.find((d) => d.step_idx === state.stepIdx);
    renderDecisionCard(dec, true, snap);
  }
}

function getTileBackSrc() {
  const custom = state.customBack || "auto";
  if (custom !== "auto") return `/tiles/${custom}.svg`;
  const theme = state.currentTheme || "apple-dark";
  if (theme === "majsoul-green") return "/tiles/back-green.svg";
  if (theme === "midnight-navy") return "/tiles/back-navy.svg";
  if (theme === "true-black") return "/tiles/back-dark.svg";
  return "/tiles/back-red.svg";
}

function applyTheme(themeName, save = true) {
  state.currentTheme = themeName;
  if (save) localStorage.setItem("majsoul_theme_pref", themeName);
  document.documentElement.setAttribute("data-theme", themeName);
  const sel1 = $("themeSelect");
  if (sel1 && sel1.value !== themeName) sel1.value = themeName;
  const sel2 = $("themeSelectCard");
  if (sel2 && sel2.value !== themeName) sel2.value = themeName;
  if (state.reviewData) {
    setStep(state.stepIdx);
  }
}

function applyBack(backName, save = true) {
  state.customBack = backName;
  if (save) localStorage.setItem("majsoul_back_pref", backName);
  const sel = $("backSelectCard");
  if (sel && sel.value !== backName) sel.value = backName;
  if (state.reviewData) {
    setStep(state.stepIdx);
  }
}

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
    if (state.isPlaying) togglePlay(false);
    t1.classList.remove("hidden");
    t2.classList.add("hidden");
    const input = $("homePaipuInput");
    if (input) input.value = "";
    window.scrollTo(0, 0);
  } else {
    t1.classList.add("hidden");
    t2.classList.remove("hidden");
    window.scrollTo(0, 0);
  }
}

const inFlightRequests = {};

function preloadOtherPerspectives(gameId, model, currentSeat) {
  if (!gameId) return;
  const runner = async () => {
    const totalSeats = state.reviewData?.names?.length || 4;
    for (let seat = 0; seat < totalSeats; seat++) {
      if (seat === currentSeat) continue;
      // 若用户在后台预加载期间已切换牌谱或模型，终止当前牌谱的后台预加载
      if (state.gameId !== gameId || state.selectedModel !== model) break;

      const cacheKey = `${gameId}_${model}_${seat}`;
      if ((state.perspectiveCache && state.perspectiveCache[cacheKey]) || inFlightRequests[cacheKey]) {
        continue;
      }

      try {
        const p = apiFetch("/api/analyze/paipu", {
          method: "POST",
          body: JSON.stringify({ paipu: gameId, target_actor: seat, engine: model }),
        });
        inFlightRequests[cacheKey] = p;
        const res = await p;
        if (res) {
          if (!state.perspectiveCache) state.perspectiveCache = {};
          state.perspectiveCache[cacheKey] = res;
        }
      } catch (e) {
        console.warn(`[Preload] 静默预加载视角 ${seat} 失败:`, e);
      } finally {
        delete inFlightRequests[cacheKey];
      }
    }
  };

  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(() => { runner(); }, { timeout: 2000 });
  } else {
    setTimeout(runner, 400);
  }
}

async function startAnalysisFromInput(rawInput, model = "akagi", seat = null) {
  if (!rawInput) {
    showToast("请先输入雀魂牌谱链接或牌谱号", "error");
    return;
  }
  const paipuId = parsePaipuUuid(rawInput);
  state.selectedModel = model;

  // 检查是否已有缓存
  const cacheKey = seat !== null && seat !== undefined ? `${paipuId}_${model}_${seat}` : null;
  if (cacheKey && state.perspectiveCache && state.perspectiveCache[cacheKey]) {
    const cachedRes = state.perspectiveCache[cacheKey];
    state.reviewData = cachedRes;
    state.gameId = cachedRes.game_id || paipuId;
    state.targetActor = cachedRes.target_actor !== undefined ? cachedRes.target_actor : seat;
    state.kyokuIdx = 0;

    showTier(2);

    const newUrl = new URL(window.location.href);
    newUrl.searchParams.set("paipu", paipuId);
    window.history.pushState(null, "", newUrl.toString());

    renderSummaryDashboard();
    selectKyoku(0, true);
    showToast(`分析完成：共检讨 ${cachedRes.kyokus?.length || 0} 局`, "success");
    preloadOtherPerspectives(state.gameId, state.selectedModel, state.targetActor);
    return;
  }

  setLoading(true, `正在分析牌谱 ${paipuId}...`, "正在使用深度神经网络检讨每一手决策");

  try {
    const postBody = { paipu: rawInput.trim(), engine: model };
    if (seat !== null && seat !== undefined) {
      postBody.target_actor = seat;
    }
    const res = await apiFetch("/api/analyze/paipu", {
      method: "POST",
      body: JSON.stringify(postBody),
    });

    state.reviewData = res;
    state.gameId = res.game_id || paipuId;
    state.targetActor = res.target_actor !== undefined ? res.target_actor : (seat ?? 0);
    state.kyokuIdx = 0;

    if (!state.perspectiveCache) state.perspectiveCache = {};
    state.perspectiveCache[`${state.gameId}_${state.selectedModel}_${state.targetActor}`] = res;

    showTier(2);

    const newUrl = new URL(window.location.href);
    newUrl.searchParams.set("paipu", paipuId);
    window.history.pushState(null, "", newUrl.toString());

    renderSummaryDashboard();
    selectKyoku(0, true);
    showToast(`分析完成：共检讨 ${res.kyokus?.length || 0} 局`, "success");

    preloadOtherPerspectives(state.gameId, state.selectedModel, state.targetActor);
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

  const cacheKey = `${paipuId}_${state.selectedModel}_${newSeat}`;

  // 1. 若已命中缓存：直接同步切换，0 毫秒瞬间秒切！
  if (state.perspectiveCache && state.perspectiveCache[cacheKey]) {
    const cachedRes = state.perspectiveCache[cacheKey];
    state.reviewData = cachedRes;
    state.targetActor = newSeat;
    renderSummaryDashboard();
    selectKyoku(state.kyokuIdx);
    if ($("roundModal") && !$("roundModal").classList.contains("hidden")) {
      renderKyokuTable();
    }
    return;
  }

  // 2. 若未命中缓存：仅在首次请求该视角时向后端请求
  const seatName = state.reviewData?.names?.[newSeat] ?? `${newSeat}`;
  setLoading(true, `切换到「${seatName}」视角...`);
  try {
    let res;
    if (inFlightRequests[cacheKey]) {
      res = await inFlightRequests[cacheKey];
    } else {
      const p = apiFetch("/api/analyze/paipu", {
        method: "POST",
        body: JSON.stringify({ paipu: paipuId, target_actor: newSeat, engine: state.selectedModel }),
      });
      inFlightRequests[cacheKey] = p;
      res = await p;
    }
    if (!state.perspectiveCache) state.perspectiveCache = {};
    state.perspectiveCache[cacheKey] = res;
    state.reviewData = res;
    state.targetActor = res.target_actor !== undefined ? res.target_actor : newSeat;
    renderSummaryDashboard();
    selectKyoku(state.kyokuIdx);
    if ($("roundModal") && !$("roundModal").classList.contains("hidden")) {
      renderKyokuTable();
    }
  } catch (err) {
    showToast(`切换视角失败：${err.message}`, "error");
  } finally {
    delete inFlightRequests[cacheKey];
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

  // Populate Kyoku Select Dropdown (东场/南场局数直接选择)
  const kyokuSelect = $("kyokuSelect");
  if (kyokuSelect && res.kyokus) {
    kyokuSelect.innerHTML = res.kyokus
      .map((k, i) => `<option value="${i}">${escapeHtml(k.label || `第 ${i + 1} 局`)}</option>`)
      .join("");
    kyokuSelect.value = state.kyokuIdx;
  }

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
  const names = res.names || ["雀氏1", "雀氏2", "雀氏3", "雀氏4"];
  seatTabs.innerHTML = names
    .map((name, seat) => {
      const wind = WIND_CN[(seat - oya + 4) % 4];
      const active = seat === res.target_actor ? "active" : "";
      const displayName = getPlayerDisplayName(seat);
      return `<button class="kd-tab ${active}" data-seat="${seat}" role="tab" title="${escapeHtml(displayName)}">
        <b>${wind}家</b> ${escapeHtml(displayName)}
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

  if ($("kyokuSelect")) $("kyokuSelect").value = kIdx;

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
    } else {
      if (state.isPlaying) togglePlay(false);
    }
  } else {
    setStep(target);
  }
}

function togglePlay(forcePlay) {
  const btn = $("btnPlayPause");
  const shouldPlay = typeof forcePlay === "boolean" ? forcePlay : !state.isPlaying;

  if (shouldPlay) {
    if (!state.reviewData) {
      showToast("请先加载或分析牌谱", "info");
      return;
    }
    state.isPlaying = true;
    if (btn) {
      btn.textContent = "暂停";
      btn.classList.add("playing");
    }
    if (state.playTimer) {
      clearInterval(state.playTimer);
      state.playTimer = null;
    }
    state.playTimer = setInterval(() => {
      const kyoku = state.reviewData?.kyokus[state.kyokuIdx];
      if (!kyoku) {
        togglePlay(false);
        return;
      }
      const isLastKyoku = state.kyokuIdx >= (state.reviewData.kyokus.length - 1);
      const isLastStep = state.stepIdx >= (kyoku.steps.length - 1);
      if (isLastKyoku && isLastStep) {
        togglePlay(false);
        return;
      }
      stepDelta(1);
    }, 850);
  } else {
    state.isPlaying = false;
    if (state.playTimer) {
      clearInterval(state.playTimer);
      state.playTimer = null;
    }
    if (btn) {
      btn.textContent = "播放";
      btn.classList.remove("playing");
    }
  }
}

function toggleHandsVisibility() {
  state.openHands = !state.openHands;
  const textEl = $("toggleHandsText");
  if (textEl) {
    textEl.textContent = state.openHands ? "明牌" : "暗牌";
  }
  // 静默切换，不再弹出任何打扰的 Toast 提示
  if (state.reviewData) {
    setStep(state.stepIdx);
  }
}

function getStepActionDesc(snap, kyoku) {
  if (!snap || !snap.event) return "";
  const ev = snap.event;
  const currentKyoku = kyoku || state.reviewData?.kyokus?.[state.kyokuIdx];
  const names = state.reviewData?.names || [];
  const getPlayerLabel = (seat) => {
    if (seat === undefined || seat === null) return "";
    const w = currentKyoku ? handWind(seat, currentKyoku) : "";
    const isHero = seat === state.targetActor;
    const pName = names[seat] ? ` ${names[seat]}` : "";
    return `${w}家${isHero ? "(自家)" : pName}`;
  };

  const actorLabel = getPlayerLabel(ev.actor);
  const targetLabel = getPlayerLabel(ev.target);

  switch (ev.type) {
    case "tsumo":
      return `${actorLabel} 摸牌 ${tileCn(ev.pai)}`;
    case "dahai":
      return `${actorLabel} ${ev.tsumogiri ? "摸切" : "手切"} ${tileCn(ev.pai)}`;
    case "chi":
      return `${actorLabel} 吃 ${tileCn(ev.pai)} (来自 ${targetLabel})`;
    case "pon":
      return `${actorLabel} 碰 ${tileCn(ev.pai)} (来自 ${targetLabel})`;
    case "ankan":
      return `${actorLabel} 暗杠 ${tileCn(ev.consumed?.[0] || ev.pai)}`;
    case "kakan":
      return `${actorLabel} 加杠 ${tileCn(ev.pai)}`;
    case "daiminkan":
      return `${actorLabel} 大明杠 ${tileCn(ev.pai)} (来自 ${targetLabel})`;
    case "reach":
      return `${actorLabel} 宣告立直`;
    case "hora":
      if (ev.actor !== ev.target && ev.target !== undefined && ev.target !== null) {
        const deltaStr = ev.deltas?.[ev.actor] ? ` (+${ev.deltas[ev.actor]}点)` : "";
        return `${actorLabel} 荣和 ${targetLabel}${deltaStr}`;
      } else {
        const deltaStr = ev.deltas?.[ev.actor] ? ` (+${ev.deltas[ev.actor]}点)` : "";
        return `${actorLabel} 自摸和牌${deltaStr}`;
      }
    case "ryuukyoku":
      return `荒牌流局结算`;
    default:
      return `${actorLabel} 行动`;
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
  renderDecisionCard(decision, isExactDecisionStep, snap);
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
      const img = m ? `/tiles/${encodeURIComponent(m)}.svg` : getTileBackSrc();
      doraHtml += `<div class="tileDiv"><img src="${img}" class="tileImg"></div>`;
    }
    dorasEl.innerHTML = doraHtml;
  }

  // Check Hora agari tile & actors
  const isHora = snap.event?.type === "hora";
  const winActor = isHora ? snap.event.actor : null;
  const targetActor = isHora ? snap.event.target : null;
  const isRon = isHora && winActor !== targetActor && targetActor !== undefined && targetActor !== null;

  let agariTile = null;
  if (isHora) {
    if (isRon && targetActor !== null && targetActor !== undefined) {
      const river = snap.rivers[targetActor] || [];
      if (river.length > 0) agariTile = river[river.length - 1].pai;
    } else if (winActor !== null) {
      agariTile = snap.drawn[winActor];
    }
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
  renderPlayerRiver("Bottom", hero, snap, isRon, targetActor);
  renderPlayerRiver("Right", p1, snap, isRon, targetActor);
  renderPlayerRiver("Top", p2, snap, isRon, targetActor);
  renderPlayerRiver("Left", p3, snap, isRon, targetActor);

  // Hands
  renderHeroHand(hero, snap, activeDecision, agariTile, winActor, isRon);
  renderOpponentHand("Right", p1, snap, agariTile, winActor, isRon);
  renderOpponentHand("Top", p2, snap, agariTile, winActor, isRon);
  renderOpponentHand("Left", p3, snap, agariTile, winActor, isRon);
}

function renderPlayerRiver(pos, seat, snap, isRon = false, targetActor = null) {
  const container = $(`river${pos}`);
  if (!container) return;
  const river = snap.rivers[seat] || [];
  const isLastDiscardActor = snap.event?.type === "dahai" && snap.event?.actor === seat;
  const isRonTarget = isRon && seat === targetActor;

  // Exactly 6 tiles per row horizontal, exactly 3 rows vertical
  const rows = [[], [], []];
  river.forEach((r, idx) => {
    const rowIdx = idx < 6 ? 0 : idx < 12 ? 1 : 2;
    rows[rowIdx].push({ ...r, origIdx: idx });
  });

  let html = "";
  rows.forEach((rowTiles) => {
    if (!rowTiles.length) return;
    let rowHtml = `<div class="river-row">`;
    rowTiles.forEach((r) => {
      const isLast = isLastDiscardActor && r.origIdx === river.length - 1;
      const isRonTile = isRonTarget && r.origIdx === river.length - 1;
      const cls = ["tileImg"];
      if (r.tsumogiri) cls.push("tsumogiri");
      if (r.riichi) cls.push("rotate");
      if (r.called) cls.push("called");
      if (isLast) cls.push("last-discard");
      if (isRonTile) cls.push("ron-claimed");

      const tip = `${tileCn(r.pai)}（${r.tsumogiri ? "摸切" : "手切"}${r.riichi ? " · 立直宣言" : ""}${r.called ? " · 被鸣" : ""}${isRonTile ? " · 放铳 (被荣和)" : ""}）`;
      const divCls = ["tileDiv"];
      if (r.riichi) divCls.push("has-rotate");
      if (isLast) divCls.push("has-last-discard");

      rowHtml += `<div class="${divCls.join(" ")}" title="${escapeHtml(tip)}"><img src="/tiles/${encodeURIComponent(r.pai)}.svg" class="${cls.join(" ")}"></div>`;
    });
    rowHtml += `</div>`;
    html += rowHtml;
  });

  container.innerHTML = html;
}

function renderHeroHand(seat, snap, activeDecision, agariTile = null, winActor = null, isRon = false) {
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

  let highlightedIdx = -1;
  allTiles.forEach(({ pai }, idx) => {
    if (actualPai === pai && highlightedIdx === -1) {
      highlightedIdx = idx;
    }
  });

  // 1. Render Closed Hand Tiles into DOM first
  let handHtml = "";
  hand.forEach((pai, idx) => {
    const isAct = highlightedIdx === idx;
    handHtml += `<div class="tileDiv" data-pai="${escapeHtml(pai)}"><img src="/tiles/${encodeURIComponent(pai)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""}"></div>`;
  });

  if (drawn) {
    const isAct = highlightedIdx === hand.length;
    const isWinTile = seat === winActor;
    handHtml += `<div class="tileDiv narrow" style="opacity:0;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
    handHtml += `<div class="tileDiv ${isWinTile ? "agari-tile" : ""}" data-pai="${escapeHtml(drawn)}" title="${isWinTile ? `自摸和牌: ${tileCn(drawn)}` : tileCn(drawn)}"><img src="/tiles/${encodeURIComponent(drawn)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""} ${isWinTile ? "agari-tile" : ""}"></div>`;
  } else if (seat === winActor && isRon && agariTile) {
    handHtml += `<div class="tileDiv narrow" style="opacity:0;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
    handHtml += `<div class="tileDiv agari-tile" data-pai="${escapeHtml(agariTile)}" title="荣和牌: ${tileCn(agariTile)}"><img src="/tiles/${encodeURIComponent(agariTile)}.svg" class="tileImg agari-tile"></div>`;
  }

  closedWrap.innerHTML = handHtml;

  // 2. Render Green Discard Recommendation Bars (SVG) — Absolutely Centered on Actual DOM Tiles
  let svgHtml = "";
  const renderedTiles = closedWrap.querySelectorAll(".tileDiv:not(.narrow)");
  renderedTiles.forEach((tileEl, idx) => {
    if (idx < allTiles.length) {
      const pai = allTiles[idx].pai;
      const barInfo = tileBars[pai];
      const prob = barInfo ? barInfo.prob : 0;
      if (prob > 0) {
        const centerX = tileEl.offsetLeft + tileEl.offsetWidth / 2;
        const height = Math.max(3, Math.round((prob / 100) * 45));
        const y = 50 - height;
        svgHtml += `<rect x="${centerX - 5}" y="${y}" width="10" height="${height}" fill="#30d158" rx="3"></rect>`;
      }
    }
  });

  if (svgEl) svgEl.innerHTML = svgHtml;

  // 3. Render Calls
  if (callsWrap) {
    const melds = snap.melds[seat] || [];
    callsWrap.innerHTML = melds.map((m) => renderMeldHtml(m, seat, "bottom")).join("");
  }
}

function renderOpponentHand(pos, seat, snap, agariTile = null, winActor = null, isRon = false) {
  const closedWrap = $(`hand${pos}Closed`);
  const callsWrap = $(`hand${pos}Calls`);
  if (!closedWrap) return;

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  let html = "";
  const backSrc = getTileBackSrc();
  const isWinner = seat === winActor;
  const showTiles = state.openHands || isWinner;

  hand.forEach((pai) => {
    const src = showTiles ? `/tiles/${encodeURIComponent(pai)}.svg` : backSrc;
    html += `<div class="tileDiv"><img src="${src}" class="tileImg"></div>`;
  });

  if (drawn) {
    const src = showTiles ? `/tiles/${encodeURIComponent(drawn)}.svg` : backSrc;
    html += `<div class="tileDiv narrow" style="opacity:0;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
    html += `<div class="tileDiv ${isWinner ? "agari-tile" : ""}"><img src="${src}" class="tileImg ${isWinner ? "agari-tile" : ""}" title="${isWinner ? `自摸和牌: ${tileCn(drawn)}` : ""}"></div>`;
  } else if (isWinner && isRon && agariTile) {
    html += `<div class="tileDiv narrow" style="opacity:0;"><img src="/tiles/Blank.svg" class="tileImg"></div>`;
    html += `<div class="tileDiv agari-tile"><img src="/tiles/${encodeURIComponent(agariTile)}.svg" class="tileImg agari-tile" title="荣和牌: ${tileCn(agariTile)}"></div>`;
  }

  closedWrap.innerHTML = html;

  if (callsWrap) {
    const melds = snap.melds[seat] || [];
    callsWrap.innerHTML = melds.map((m) => renderMeldHtml(m, seat, pos.toLowerCase())).join("");
  }
}

function renderMeldHtml(m, ownerSeat, pov = "bottom") {
  const tiles = [...(m.tiles || [])];
  if (!tiles.length) return "";
  const backSrc = getTileBackSrc();

  if (m.type === "ankan") {
    return `<div class="meld-block meld-block-${pov}">${tiles
      .map((t, i) => {
        const src = i === 0 || i === tiles.length - 1 ? backSrc : `/tiles/${encodeURIComponent(t)}.svg`;
        return `<div class="tileDiv"><img src="${src}" class="tileImg"></div>`;
      })
      .join("")}</div>`;
  }

  let sideIdx = -1;
  let order = tiles;
  if (m.from !== undefined && m.from !== null) {
    const called = tiles[0];
    const rest = tiles.slice(1);
    const rel = (m.from - ownerSeat + 4) % 4;
    if (rel === 3) { order = [called, ...rest]; sideIdx = 0; }
    else if (rel === 2) { order = [rest[0], called, ...rest.slice(1)]; sideIdx = 1; }
    else { order = [...rest, called]; sideIdx = order.length - 1; }
  }

  return `<div class="meld-block meld-block-${pov}">${order
    .map((t, i) => {
      const isRotated = i === sideIdx;
      const divCls = ["tileDiv"];
      if (isRotated) divCls.push("has-rotate");
      return `<div class="${divCls.join(" ")}"><img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg ${isRotated ? "rotate" : ""}"></div>`;
    })
    .join("")}</div>`;
}

function renderDecisionTileOrBadge(pai, actionObj, actionCn = "") {
  if (pai && pai !== "none" && pai !== "pass" && !pai.startsWith("none")) {
    return `<img src="/tiles/${encodeURIComponent(pai)}.svg" class="tileImg">`;
  }

  const actType = (actionObj?.type || actionObj?.action || actionCn || "pass").toLowerCase();

  if (actType.includes("pass") || actType.includes("none") || actType.includes("跳过") || actType.includes("过")) {
    return `
      <div class="action-badge action-badge-pass" title="跳过 / 不鸣">
        <div class="action-badge-icon">
          <svg viewBox="0 0 24 24"><path d="M5 4l8 8-8 8V4zm9 0l8 8-8 8V4z"/></svg>
        </div>
        <div class="action-badge-text">PASS</div>
      </div>
    `;
  }
  if (actType.includes("chi") || actType.includes("吃")) {
    return `
      <div class="action-badge action-badge-chi" title="吃牌">
        <div class="action-badge-icon">
          <svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
        </div>
        <div class="action-badge-text">CHI</div>
      </div>
    `;
  }
  if (actType.includes("pon") || actType.includes("碰")) {
    return `
      <div class="action-badge action-badge-pon" title="碰牌">
        <div class="action-badge-icon">
          <svg viewBox="0 0 24 24"><circle cx="8" cy="12" r="4" fill="currentColor"/><circle cx="16" cy="12" r="4" fill="currentColor"/></svg>
        </div>
        <div class="action-badge-text">PON</div>
      </div>
    `;
  }
  if (actType.includes("kan") || actType.includes("杠")) {
    return `
      <div class="action-badge action-badge-kan" title="杠牌">
        <div class="action-badge-icon">
          <svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="8" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="16" cy="12" r="1.5" fill="currentColor"/></svg>
        </div>
        <div class="action-badge-text">KAN</div>
      </div>
    `;
  }
  if (actType.includes("riichi") || actType.includes("立直")) {
    return `
      <div class="action-badge action-badge-riichi" title="立直宣言">
        <div class="action-badge-icon">
          <svg viewBox="0 0 24 24"><rect x="2" y="10" width="20" height="4" rx="2" fill="currentColor"/><circle cx="12" cy="12" r="2.2" fill="#ffffff"/></svg>
        </div>
        <div class="action-badge-text">RIICHI</div>
      </div>
    `;
  }
  if (actType.includes("hora") || actType.includes("ron") || actType.includes("tsumo") || actType.includes("和")) {
    return `
      <div class="action-badge action-badge-hora" title="和牌">
        <div class="action-badge-icon">
          <svg viewBox="0 0 24 24"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" fill="currentColor"/></svg>
        </div>
        <div class="action-badge-text">WIN</div>
      </div>
    `;
  }

  return `
    <div class="action-badge action-badge-pass" style="opacity:0.65;">
      <div class="action-badge-icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5" fill="currentColor"/></svg></div>
      <div class="action-badge-text">--</div>
    </div>
  `;
}

// ============================================================================
// Active Decision Details Card (In Right Sidebar)
// ============================================================================
function renderDecisionCard(dec, isExactStep, snap) {
  const card = $("decisionCard");
  if (!card) return;

  const turnEl = $("decTurnText");
  const badgeEl = $("decVerdictBadge");
  const aiTileWrap = $("decAiTileWrap");
  const aiTileName = $("decAiTileName");
  const aiProbBadge = $("decAiProbBadge");
  const aiShantenBadge = $("decAiShantenBadge");
  const aiUkeireBadge = $("decAiUkeireBadge");

  const actualTileWrap = $("decActualTileWrap");
  const actualTileName = $("decActualTileName");
  const actualProbBadge = $("decActualProbBadge");
  const actualVerdictText = $("decActualVerdictText");
  const actualBlock = card.querySelector(".kd-choice-actual");

  const tableBody = $("decCandTableBody");

  const targetActor = state.targetActor ?? 0;
  const currentKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  const wind = currentKyoku ? handWind(targetActor, currentKyoku) : "";
  const names = state.reviewData?.names || [];
  const isHora = snap?.event?.type === "hora";
  const isRyuukyoku = snap?.event?.type === "ryuukyoku";

  // CASE 1: Round End (Hora / 和牌结算)
  if (isHora) {
    const winActor = snap.event.actor;
    const tgtActor = snap.event.target;
    const isRon = winActor !== tgtActor && tgtActor !== undefined && tgtActor !== null;
    const isHeroWin = winActor === targetActor;
    const isHeroLoser = isRon && tgtActor === targetActor;

    const winName = names[winActor] || `玩家 ${winActor + 1}`;
    const winWind = currentKyoku ? handWind(winActor, currentKyoku) : "";
    const tgtName = isRon ? (names[tgtActor] || `玩家 ${tgtActor + 1}`) : "";
    const tgtWind = isRon && currentKyoku ? handWind(tgtActor, currentKyoku) : "";

    const scoreDelta = snap.event.deltas?.[winActor] ?? currentKyoku?.score_deltas?.[winActor] ?? 0;
    const summaryText = currentKyoku?.end_summaries?.length
      ? currentKyoku.end_summaries.join("<br>")
      : (isRon ? `${winWind}家 ${escapeHtml(winName)} 荣和 ${tgtWind}家 ${escapeHtml(tgtName)} +${scoreDelta}` : `${winWind}家 ${escapeHtml(winName)} 自摸 +${scoreDelta}`);

    if (turnEl) turnEl.textContent = `本局终局结算 (${isRon ? "荣和" : "自摸"})`;
    if (badgeEl) {
      if (isHeroWin) {
        badgeEl.textContent = "自家和牌";
        badgeEl.className = "kd-dec-verdict best";
      } else if (isHeroLoser) {
        badgeEl.textContent = "自家放铳";
        badgeEl.className = "kd-dec-verdict blunder";
      } else {
        badgeEl.textContent = `${winWind}家和牌`;
        badgeEl.className = "kd-dec-verdict agari";
      }
    }

    // Identify Winning Tile
    let agariTile = null;
    if (isRon && tgtActor !== null && tgtActor !== undefined) {
      const river = snap.rivers[tgtActor] || [];
      if (river.length > 0) agariTile = river[river.length - 1].pai;
    } else if (winActor !== null) {
      agariTile = snap.drawn[winActor];
    }

    if (actualTileWrap) {
      actualTileWrap.innerHTML = agariTile
        ? `<img src="/tiles/${encodeURIComponent(agariTile)}.svg" class="tileImg agari-tile">`
        : renderDecisionTileOrBadge(null, { type: "hora" }, "和牌");
    }
    if (actualTileName) {
      actualTileName.style.display = "block";
      actualTileName.textContent = isRon ? `荣和 ${agariTile ? tileCn(agariTile) : ""}` : `自摸 ${agariTile ? tileCn(agariTile) : ""}`;
    }
    if (actualProbBadge) actualProbBadge.textContent = `+${scoreDelta} 点`;
    if (actualVerdictText) {
      actualVerdictText.textContent = isHeroWin ? "自家和牌" : isHeroLoser ? "放铳" : "终局";
      actualVerdictText.className = isHeroWin ? "kd-tag-verdict best" : isHeroLoser ? "kd-tag-verdict blunder" : "kd-tag-verdict question";
    }
    if (actualBlock) {
      if (isHeroLoser) actualBlock.classList.add("is-blunder");
      else actualBlock.classList.remove("is-blunder");
    }

    // AI choice block: Settlement Details
    if (aiTileWrap) {
      aiTileWrap.innerHTML = renderDecisionTileOrBadge(null, { type: "hora" }, "WIN");
    }
    if (aiTileName) {
      aiTileName.style.display = "block";
      aiTileName.textContent = isRon ? `放铳者: ${tgtWind}家 ${tgtName}` : "自摸终局";
    }
    if (aiProbBadge) aiProbBadge.textContent = "终局结算";
    if (aiShantenBadge) aiShantenBadge.textContent = "和牌";
    if (aiUkeireBadge) {
      const uras = snap.event.ura_markers || [];
      aiUkeireBadge.textContent = uras.length ? `里宝: ${uras.map(tileCn).join(" ")}` : "本局结束";
    }

    // Settlement Table in Candidates Area
    if (tableBody) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="5" style="padding:8px 2px;">
            <div class="kd-settlement-card">
              <div class="kd-settlement-title-row">
                <span>${isRon ? "荣和 (放铳终局)" : "自摸 (和牌终局)"}</span>
                <span style="font-size:14px; font-weight:800; color:#30d158;">+${scoreDelta} 点</span>
              </div>
              <div class="kd-settlement-summary-text">${summaryText}</div>
              <div class="kd-settlement-delta-grid">
                ${[0, 1, 2, 3].map((seat) => {
                  const delta = snap.event.deltas?.[seat] ?? currentKyoku?.score_deltas?.[seat] ?? 0;
                  const deltaClass = delta > 0 ? "pos" : delta < 0 ? "neg" : "zero";
                  const deltaFormatted = delta > 0 ? `+${delta}` : `${delta}`;
                  const isWin = seat === winActor;
                  const isLos = isRon && seat === tgtActor;
                  const cellClass = isWin ? "winner" : isLos ? "loser" : "";
                  const pName = names[seat] || `P${seat + 1}`;
                  const pWind = currentKyoku ? handWind(seat, currentKyoku) : "";
                  return `
                    <div class="kd-settlement-delta-cell ${cellClass}">
                      <div class="kd-settlement-player-name">${pWind}·${escapeHtml(pName)}</div>
                      <div class="kd-settlement-score-diff ${deltaClass}">${deltaFormatted}</div>
                    </div>
                  `;
                }).join("")}
              </div>
            </div>
          </td>
        </tr>
      `;
    }
    return;
  }

  // CASE 2: Round End (Ryuukyoku / 流局结算)
  if (isRyuukyoku) {
    if (turnEl) turnEl.textContent = "本局终局结算 (荒牌流局)";
    if (badgeEl) {
      badgeEl.textContent = "荒牌流局";
      badgeEl.className = "kd-dec-verdict cruise";
    }
    if (actualTileWrap) actualTileWrap.innerHTML = renderDecisionTileOrBadge(null, { type: "none" }, "流局");
    if (actualTileName) {
      actualTileName.style.display = "block";
      actualTileName.textContent = "荒牌流局";
    }
    if (actualProbBadge) actualProbBadge.textContent = "";
    if (aiTileWrap) aiTileWrap.innerHTML = renderDecisionTileOrBadge(null, { type: "none" }, "流局");
    if (aiTileName) {
      aiTileName.style.display = "block";
      aiTileName.textContent = "荒牌流局";
    }
    if (aiProbBadge) aiProbBadge.textContent = "荒牌流局";
    if (aiShantenBadge) aiShantenBadge.textContent = "流局";
    if (aiUkeireBadge) aiUkeireBadge.textContent = "荒牌";

    const summaryText = currentKyoku?.end_summaries?.length ? currentKyoku.end_summaries.join("<br>") : (currentKyoku?.result || "荒牌流局");

    if (tableBody) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="5" style="padding:8px 2px;">
            <div class="kd-settlement-card">
              <div class="kd-settlement-title-row">
                <span>荒牌流局结算</span>
                <span style="font-size:12px; color:var(--color-text-secondary);">局末听牌与罚符</span>
              </div>
              <div class="kd-settlement-summary-text">${summaryText}</div>
              <div class="kd-settlement-delta-grid">
                ${[0, 1, 2, 3].map((seat) => {
                  const delta = currentKyoku?.score_deltas?.[seat] ?? 0;
                  const deltaClass = delta > 0 ? "pos" : delta < 0 ? "neg" : "zero";
                  const deltaFormatted = delta > 0 ? `+${delta}` : `${delta}`;
                  const pName = names[seat] || `P${seat + 1}`;
                  const pWind = currentKyoku ? handWind(seat, currentKyoku) : "";
                  return `
                    <div class="kd-settlement-delta-cell">
                      <div class="kd-settlement-player-name">${pWind}·${escapeHtml(pName)}</div>
                      <div class="kd-settlement-score-diff ${deltaClass}">${deltaFormatted}</div>
                    </div>
                  `;
                }).join("")}
              </div>
            </div>
          </td>
        </tr>
      `;
    }
    return;
  }

  // CASE 3: Normal or Cruise Steps
  if (!dec) {
    if (turnEl) turnEl.textContent = `${wind}家 (巡航中)`;
    if (badgeEl) {
      badgeEl.textContent = "巡航中";
      badgeEl.className = "kd-dec-verdict cruise";
    }
    if (aiTileWrap) aiTileWrap.innerHTML = renderDecisionTileOrBadge(null, { type: "none" }, "等待决策");
    if (aiTileName) {
      aiTileName.style.display = "block";
      aiTileName.textContent = "等待决策";
    }
    if (aiProbBadge) aiProbBadge.textContent = "";
    if (aiShantenBadge) aiShantenBadge.textContent = "--";
    if (aiUkeireBadge) aiUkeireBadge.textContent = "进张 --";

    if (actualTileWrap) actualTileWrap.innerHTML = renderDecisionTileOrBadge(null, { type: "none" }, "等待切牌");
    if (actualTileName) {
      actualTileName.style.display = "block";
      actualTileName.textContent = "等待切牌";
    }
    if (actualProbBadge) actualProbBadge.textContent = "";
    if (actualVerdictText) {
      actualVerdictText.textContent = "巡航";
      actualVerdictText.className = "kd-tag-verdict cruise";
    }
    if (actualBlock) actualBlock.classList.remove("is-blunder");

    const dynamicDesc = getStepActionDesc(snap, currentKyoku);
    if (tableBody) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="5" style="padding:14px 8px;">
            <div class="kd-step-dynamic-desc">${dynamicDesc ? `当前步: ${escapeHtml(dynamicDesc)}` : "暂无 AI 决策建议 (巡航中)"}</div>
          </td>
        </tr>
      `;
    }
    return;
  }

  const isMatch = dec.is_equal;
  const isCallDec = dec.decision_type === "call_response";
  const isReachDec = dec.decision_type === "reach";

  if (turnEl) {
    if (!isExactStep) {
      turnEl.textContent = `${wind}家 (巡航中 · 别家行动)`;
    } else if (isCallDec) {
      turnEl.textContent = `${wind}家 鸣牌响应决策`;
    } else if (isReachDec) {
      turnEl.textContent = `${wind}家 立直宣言决策`;
    } else {
      turnEl.textContent = `${wind}家 切牌决策`;
    }
  }

  if (badgeEl) {
    if (!isExactStep) {
      badgeEl.textContent = "巡航中";
      badgeEl.className = "kd-dec-verdict cruise";
    } else if (isMatch) {
      badgeEl.textContent = "AI 一致";
      badgeEl.className = "kd-dec-verdict best";
    } else if (dec.verdict === "blunder") {
      badgeEl.textContent = "恶手";
      badgeEl.className = "kd-dec-verdict blunder";
    } else {
      badgeEl.textContent = dec.verdict_cn || "微差";
      badgeEl.className = "kd-dec-verdict question";
    }
  }

  // 1. Actual Choice (On Top)
  const actPai = dec.actual?.pai;
  const isActTile = actPai && actPai !== "none" && actPai !== "pass";
  if (actualTileWrap) {
    actualTileWrap.innerHTML = renderDecisionTileOrBadge(actPai, dec.actual, dec.actual_cn);
  }
  if (actualTileName) {
    if (isActTile) {
      actualTileName.textContent = "";
      actualTileName.style.display = "none";
    } else {
      actualTileName.style.display = "block";
      actualTileName.textContent = dec.actual_cn || "跳过 (Pass)";
    }
  }
  if (actualProbBadge) {
    actualProbBadge.textContent = "";
  }
  if (actualVerdictText) {
    if (!isExactStep) {
      actualVerdictText.textContent = "上一决策";
      actualVerdictText.className = "kd-tag-verdict cruise";
    } else if (isMatch) {
      actualVerdictText.textContent = "AI 一致";
      actualVerdictText.className = "kd-tag-verdict best";
    } else if (dec.verdict === "blunder") {
      actualVerdictText.textContent = "恶手";
      actualVerdictText.className = "kd-tag-verdict blunder";
    } else {
      actualVerdictText.textContent = dec.verdict_cn || "微差";
      actualVerdictText.className = "kd-tag-verdict question";
    }
  }
  if (actualBlock) {
    if (isExactStep && dec.verdict === "blunder") actualBlock.classList.add("is-blunder");
    else actualBlock.classList.remove("is-blunder");
  }

  // 2. AI Recommended Choice (On Bottom)
  const expPai = dec.expected?.pai || dec.candidates?.[0]?.pai;
  const isExpTile = expPai && expPai !== "none" && expPai !== "pass";
  const bestCand = dec.candidates?.[0];
  if (aiTileWrap) {
    aiTileWrap.innerHTML = renderDecisionTileOrBadge(expPai, dec.expected, dec.expected_cn);
  }
  if (aiTileName) {
    if (isExpTile) {
      aiTileName.textContent = "";
      aiTileName.style.display = "none";
    } else {
      aiTileName.style.display = "block";
      aiTileName.textContent = dec.expected_cn || "跳过 (Pass)";
    }
  }
  if (aiProbBadge) {
    aiProbBadge.textContent = "";
  }
  if (aiShantenBadge) {
    const sVal = bestCand?.shanten_after ?? bestCand?.shanten ?? dec.shanten;
    aiShantenBadge.textContent = shantenText(sVal);
  }
  if (aiUkeireBadge) {
    const uk = bestCand?.ukeire ?? dec.expected?.ukeire;
    aiUkeireBadge.textContent = uk !== undefined && uk !== null ? `进张 ${uk} 张` : "进张 --";
  }

  // 3. Top Candidates Table with Pure Graphic & Expandable Ukeire Detail Drawer
  const cands = (dec.candidates || []).slice(0, 5);
  const dynamicDesc = !isExactStep ? getStepActionDesc(snap, currentKyoku) : "";

  if (tableBody) {
    let headerRow = "";
    if (!isExactStep && dynamicDesc) {
      headerRow = `<tr><td colspan="5" style="padding:6px 6px;"><div class="kd-step-dynamic-desc">${escapeHtml(dynamicDesc)}</div></td></tr>`;
    }

    const rowsHtml = cands
      .map((c, i) => {
        const pai = c.pai || c.action?.pai;
        const isTile = pai && pai !== "none" && pai !== "pass";
        const prob = fmtPct(c.prob);
        const isAct = (pai && pai === actPai) || c.action_cn === dec.actual_cn;
        const isAi = c.rank === 1;
        const tileDisplay = isTile
          ? `<img src="/tiles/${encodeURIComponent(pai)}.svg" class="kd-cand-tile-img" title="${tileCn(pai)}">`
          : `<span class="kd-cand-action" style="padding:3px 7px;">${escapeHtml(c.action_cn || "跳过")}</span>`;
        const rowBg = isAct
          ? "background:rgba(255, 69, 58, 0.16);"
          : isAi
          ? "background:rgba(48, 209, 88, 0.14);"
          : "";
        const sVal = c.shanten_after ?? c.shanten ?? dec.shanten;
        const uVal = c.ukeire !== undefined && c.ukeire !== null ? `${c.ukeire} 张` : "-";
        const ukeireList = c.ukeire_tiles || [];
        const hasUkeireTiles = ukeireList.length > 0;
        const isExpanded = false;
        const uCell = hasUkeireTiles
          ? `<div class="kd-ukeire-cell" title="点击展开/收起具体进张有效牌"><span>${uVal}</span><span class="kd-ukeire-arrow">▾</span></div>`
          : `<span>${uVal}</span>`;

        const detailRow = hasUkeireTiles
          ? `
          <tr class="kd-cand-detail-row ${isExpanded ? '' : 'hidden'}" id="candDetail_${i}">
            <td colspan="5" style="padding:4px 8px 8px; border-bottom:1px solid rgba(255,255,255,0.06); background:rgba(0,0,0,0.22);" onclick="event.stopPropagation()">
              <div class="kd-ukeire-container">
                <div class="kd-ukeire-grid">
                  ${ukeireList.map(u => `
                    <div class="kd-ukeire-item">
                      <img src="/tiles/${encodeURIComponent(u.pai)}.svg" class="kd-ukeire-img" alt="${u.pai}">
                      <span class="kd-ukeire-mul">×${u.count}</span>
                    </div>
                  `).join("")}
                </div>
              </div>
            </td>
          </tr>
          `
          : "";

        return `
          <tr class="kd-cand-row ${isExpanded ? 'is-expanded' : ''}" style="${rowBg}" onclick="toggleCandUkeire(event, ${i})">
            <td><span class="kd-cand-action">${isTile ? "切" : escapeHtml(c.action_cn || "跳过")}</span></td>
            <td style="text-align:center;">${tileDisplay}</td>
            <td style="font-weight:bold; color:${isAi ? "#30d158" : "var(--color-text-bright)"}">${prob}</td>
            <td><span class="kd-tag-shanten" style="padding:1px 5px;">${shantenText(sVal)}</span></td>
            <td>${uCell}</td>
          </tr>
          ${detailRow}
        `;
      })
      .join("");

    tableBody.innerHTML = headerRow + rowsHtml;
  }
}

window.toggleCandUkeire = function(e, idx) {
  if (e) e.stopPropagation();
  const detail = document.getElementById(`candDetail_${idx}`);
  if (!detail) return;
  const isHidden = detail.classList.toggle("hidden");
  const row = detail.previousElementSibling;
  if (row) {
    if (isHidden) row.classList.remove("is-expanded");
    else row.classList.add("is-expanded");
  }
};

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
  if (!kyoku?.steps?.length) return;
  const decs = kyoku.decisions || [];
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < state.stepIdx) {
      setStep(decs[i].step_idx);
      return;
    }
  }
  if (state.stepIdx > 0) {
    setStep(0);
    return;
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
  if (!kyoku?.steps?.length) return;
  for (const d of kyoku.decisions || []) {
    if (d.step_idx > state.stepIdx) {
      setStep(d.step_idx);
      return;
    }
  }
  // If there are no more decisions for Hero, but we have not reached the final step of the kyoku:
  if (state.stepIdx < kyoku.steps.length - 1) {
    setStep(kyoku.steps.length - 1);
    return;
  }
  if (state.kyokuIdx < state.reviewData.kyokus.length - 1) {
    jumpNextKyoku();
  } else {
    showToast("已经是最后一局的终局了", "info");
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

function getRoundDeltas(k) {
  if (k.end_scores && k.init_scores && k.end_scores.length === 4 && k.init_scores.length === 4) {
    return [
      k.end_scores[0] - k.init_scores[0],
      k.end_scores[1] - k.init_scores[1],
      k.end_scores[2] - k.init_scores[2],
      k.end_scores[3] - k.init_scores[3],
    ];
  }
  if (k.score_deltas && Array.isArray(k.score_deltas) && k.score_deltas.length === 4) {
    return k.score_deltas;
  }
  return [0, 0, 0, 0];
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString();
}

function fmtDeltaHtml(n) {
  const num = Number(n || 0);
  if (num > 0) return `<span class="delta-badge delta-plus">+${num.toLocaleString()}</span>`;
  if (num < 0) return `<span class="delta-badge delta-minus">${num.toLocaleString()}</span>`;
  return `<span class="delta-badge delta-zero">0</span>`;
}

function renderKyokuTable() {
  const res = state.reviewData;
  if (!res || !res.kyokus) return;
  const thead = $("roundTableHead");
  const tbody = $("roundTableBody");
  const tfoot = $("roundTableFoot");
  if (!tbody) return;

  const names = [0, 1, 2, 3].map((s) => getPlayerDisplayName(s));
  const targetActor = state.targetActor ?? 0;

  // Subtitle info & Quick Pov switcher
  const subEl = $("roundModalSub");
  if (subEl) {
    subEl.innerHTML = `当前检讨视角：<strong style="color:var(--color-text-bright);">${escapeHtml(names[targetActor])}</strong> (起${WIND_CN[targetActor]} / P${targetActor}) · 点击任意局可直接跳转该局检讨`;
  }

  const povTabs = $("roundModalPovTabs");
  if (povTabs) {
    povTabs.innerHTML = `
      <span style="font-size:12px; color:var(--color-text-secondary); margin-right:4px;">切换视角:</span>
      ${names.map((name, s) => {
        const active = s === targetActor ? "active" : "";
        return `<button class="round-modal-pov-btn ${active}" data-seat="${s}">
          <b>P${s}</b> ${escapeHtml(name)} ${s === targetActor ? "(自家)" : ""}
        </button>`;
      }).join("")}
    `;
    povTabs.querySelectorAll(".round-modal-pov-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const s = parseInt(btn.dataset.seat, 10);
        if (s !== state.targetActor) {
          await switchSeatPerspective(s);
          renderKyokuTable();
        }
      });
    });
  }

  // 1. Table Header
  if (thead) {
    thead.innerHTML = `
      <tr>
        <th style="width:68px;">局名</th>
        <th style="width:64px;">本场</th>
        <th style="width:110px;">庄家</th>
        <th style="text-align:left; min-width:180px;">和牌 / 结算</th>
        ${names.map((name, s) => {
          const isTarget = s === targetActor;
          const thClass = isTarget ? "active-pov-th" : "";
          const povBadge = isTarget ? " <span style='font-size:10px; color:#ffcc00;'>(我)</span>" : "";
          return `<th class="${thClass}" style="text-align:right;">P${s} ${escapeHtml(name)} (起${WIND_CN[s]})${povBadge}</th>`;
        }).join("")}
        <th style="width:85px; text-align:right;">当前增减</th>
        <th style="width:96px; text-align:center;" title="分子为与AI一致决策数，分母为有效决策总数">AI 一致率</th>
        <th style="width:60px; text-align:center;">恶手</th>
      </tr>
    `;
  }

  // 2. Table Body (12 Kyokus)
  const cumDeltas = [0, 0, 0, 0];
  let totDecs = 0;
  let totMatches = 0;
  let totBlunders = 0;

  tbody.innerHTML = res.kyokus
    .map((k, idx) => {
      const oyaName = names[k.oya] ? `${names[k.oya]} (P${k.oya})` : `P${k.oya}家`;
      const isOya = k.oya === targetActor;
      const oyaDisplay = `<span style="${isOya ? 'color:#ffcc00; font-weight:700;' : ''}">${escapeHtml(oyaName)}</span>`;

      // Label & Honba separation
      const fullLabel = k.label || `第 ${idx + 1} 局`;
      const labelParts = fullLabel.split(" ");
      const kyokuName = labelParts[0];
      const honbaStr = `${k.honba ?? 0}本场`;

      // Deltas calculation
      const deltas = getRoundDeltas(k);
      for (let s = 0; s < 4; s++) {
        cumDeltas[s] += deltas[s];
      }

      // 统计与一致率 (当前视角玩家)
      const stats = k.stats || {};
      const decCount = k.decision_count ?? stats.total ?? (k.decisions ? k.decisions.length : 0);
      const matchCount = k.match_count ?? stats.matches ?? (k.decisions ? k.decisions.filter((d) => d.is_equal).length : 0);
      const matchPct = decCount > 0 ? ((matchCount / decCount) * 100).toFixed(1) + "%" : "--";
      const matchText = decCount > 0 ? `${matchPct} <span style="font-size:11px; color:rgba(235,235,245,0.6);">(${matchCount}/${decCount})</span>` : "--";

      const blunders = k.blunder_count ?? stats.blunders ?? (k.decisions ? k.decisions.filter((d) => d.verdict === "blunder").length : 0);

      totDecs += decCount;
      totMatches += matchCount;
      totBlunders += blunders;

      // 结算文本
      const rawSummaries = k.end_summaries && k.end_summaries.length
        ? k.end_summaries.join("<br>")
        : (k.result || "流局");
      const resultText = escapeHtml(sanitizeSummaryText(rawSummaries)).replace(/&lt;br&gt;/g, "<br>");

      // 一致率颜色阶
      const matchNum = decCount > 0 ? (matchCount / decCount) * 100 : 0;
      const matchColor = decCount === 0 ? "rgba(235,235,245,0.4)" : matchNum >= 80 ? "#30d158" : matchNum >= 65 ? "#0a84ff" : "#ff9f0a";

      const currentDelta = deltas[targetActor] ?? 0;

      // 4 Players columns HTML
      const playersColsHtml = [0, 1, 2, 3].map((s) => {
        const scoreVal = k.end_scores ? k.end_scores[s] : 25000;
        const dVal = deltas[s];
        const isTarget = s === targetActor;
        const tdClass = isTarget ? "active-pov-td" : "";
        return `
          <td class="${tdClass}" style="text-align:right;">
            <span class="score-num">${fmtInt(scoreVal)}</span>
            ${fmtDeltaHtml(dVal)}
          </td>
        `;
      }).join("");

      const isCurrentKyoku = idx === state.kyokuIdx;
      return `
        <tr data-kidx="${idx}" class="${isCurrentKyoku ? 'active-kyoku-row' : ''}">
          <td style="font-weight:700; color:var(--color-text-bright); text-align:center;">${escapeHtml(kyokuName)}</td>
          <td style="text-align:center; color:var(--color-text-secondary);">${escapeHtml(honbaStr)}</td>
          <td style="text-align:center;">${oyaDisplay}</td>
          <td style="text-align:left; font-size:12px; line-height:1.4;">${resultText}</td>
          ${playersColsHtml}
          <td style="text-align:right; font-weight:700;">${fmtDeltaHtml(currentDelta)}</td>
          <td style="color:${matchColor}; font-weight:700; text-align:center;" title="${stats.discard_total !== undefined ? `切牌: ${stats.discard_matches}/${stats.discard_total}, 鸣牌: ${stats.call_matches}/${stats.call_total}` : `${matchCount}/${decCount}`}">${matchText}</td>
          <td style="text-align:center;">
            <span class="blunder-badge-num ${blunders > 0 ? 'has-blunder' : 'zero'}">${blunders}</span>
          </td>
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

  // 3. Table Footer (全庄终局汇总)
  if (tfoot && res.kyokus.length > 0) {
    const lastKyoku = res.kyokus[res.kyokus.length - 1];
    const finalScores = lastKyoku.end_scores || [25000, 25000, 25000, 25000];
    const totalMatchPct = totDecs > 0 ? ((totMatches / totDecs) * 100).toFixed(1) + "%" : "--";
    const totalMatchText = `${totalMatchPct} <span style="font-size:11px; color:rgba(235,235,245,0.6);">(${totMatches}/${totDecs})</span>`;
    const targetCumDelta = cumDeltas[targetActor] ?? 0;

    // Calculate ranking 1位..4位
    const sortedSeats = [0, 1, 2, 3].sort((a, b) => finalScores[b] - finalScores[a]);
    const ranks = {};
    sortedSeats.forEach((s, r) => { ranks[s] = r + 1; });

    tfoot.innerHTML = `
      <tr>
        <td style="text-align:center; font-weight:700; color:#ffcc00;">全庄终局</td>
        <td style="text-align:center; color:var(--color-text-secondary);">${res.kyokus.length} 局</td>
        <td style="text-align:center; color:var(--color-text-secondary);">-</td>
        <td style="text-align:left; font-weight:700; color:var(--color-text-bright);">全庄结算总分</td>
        ${[0, 1, 2, 3].map((s) => {
          const isTarget = s === targetActor;
          const tdClass = isTarget ? "active-pov-td" : "";
          const rankBadge = `<span style="font-size:10px; color:${ranks[s] === 1 ? '#ffcc00' : 'rgba(235,235,245,0.6)'}; margin-left:3px;">[${ranks[s]}位]</span>`;
          return `
            <td class="${tdClass}" style="text-align:right;">
              <span class="score-num">${fmtInt(finalScores[s])}</span>
              ${fmtDeltaHtml(cumDeltas[s])}
              ${rankBadge}
            </td>
          `;
        }).join("")}
        <td style="text-align:right; font-weight:700;">${fmtDeltaHtml(targetCumDelta)}</td>
        <td style="text-align:center; font-weight:700; color:#30d158;">${totalMatchText}</td>
        <td style="text-align:center;">
          <span class="blunder-badge-num ${totBlunders > 0 ? 'has-blunder' : 'zero'}">${totBlunders}</span>
        </td>
      </tr>
    `;
  }
}

function openRoundScoresModal() {
  const res = state.reviewData;
  if (!res || !res.kyokus) return;
  renderKyokuTable();
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
    if (state.isPlaying) togglePlay(false);
    showTier(1);
    const input = $("homePaipuInput");
    if (input) {
      input.value = "";
      input.focus();
    }
    const newUrl = new URL(window.location.href);
    newUrl.searchParams.delete("paipu");
    window.history.pushState(null, "", newUrl.toString());
  });

  // Kyoku Select Dropdown (东场/南场局数直接选择)
  $("kyokuSelect")?.addEventListener("change", (e) => {
    if (state.isPlaying) togglePlay(false);
    const kidx = parseInt(e.target.value, 10);
    if (!isNaN(kidx)) {
      selectKyoku(kidx, true);
    }
  });

  // Play/Pause Auto Step
  $("btnPlayPause")?.addEventListener("click", () => togglePlay());

  // Toggle Hands Visibility
  $("btnToggleHands")?.addEventListener("click", toggleHandsVisibility);

  // Toggle Hide/Show Player ID
  $("btnToggleHideId")?.addEventListener("click", toggleHidePlayerId);

  // Global robust delegation for clicking anywhere on hands / tiles / back tiles / blank area:
  document.addEventListener("click", (e) => {
    const handTarget = e.target.closest(".grid-hand, .grid-hand-p0-container, [id^='hand'], .hand-closed-p0, .hand-closed-p1, .hand-closed-p2, .hand-closed-p3");
    if (handTarget) {
      if (e.target.closest("button, a, select, input")) return;
      toggleHandsVisibility();
    }
  });

  // Theme & Appearance Selectors
  $("themeSelect")?.addEventListener("change", (e) => applyTheme(e.target.value));
  $("themeSelectCard")?.addEventListener("change", (e) => applyTheme(e.target.value));
  $("backSelectCard")?.addEventListener("change", (e) => applyBack(e.target.value));

  // Step timeline slider
  $("stepSlider")?.addEventListener("input", (e) => {
    if (state.isPlaying) togglePlay(false);
    setStep(parseInt(e.target.value, 10));
  });

  // Navigation controls (Bound to both top header stepper & sidebar buttons)
  const bindNavClick = (ids, fn) => {
    ids.forEach((id) => {
      $(id)?.addEventListener("click", () => {
        if (state.isPlaying) togglePlay(false);
        fn();
      });
    });
  };
  bindNavClick(["btnPrevBlunderTop", "btnPrevBlunder"], jumpPrevBlunder);
  bindNavClick(["btnNextBlunderTop", "btnNextBlunder"], jumpNextBlunder);
  bindNavClick(["btnPrevDecTop", "btnPrevDec"], jumpPrevDecision);
  bindNavClick(["btnNextDecTop", "btnNextDec"], jumpNextDecision);

  // Round modal
  $("btnRoundScore")?.addEventListener("click", openRoundScoresModal);
  $("gameSummaryPill")?.addEventListener("click", openRoundScoresModal);
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

    if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      togglePlay();
      return;
    }

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
      h: toggleHandsVisibility,
      H: toggleHandsVisibility,
      " ": togglePlay,
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

function updateResponsiveZoom() {
  const w = window.innerWidth;
  const zoom = Math.min(1.0, Math.max(0.46, (w - 24) / 655));
  document.documentElement.style.setProperty("--zoom", zoom.toFixed(3));
}
window.addEventListener("resize", updateResponsiveZoom);

// ============================================================================
// Initialization
// ============================================================================
window.addEventListener("DOMContentLoaded", async () => {
  updateResponsiveZoom();
  applyTheme(state.currentTheme, false);
  applyBack(state.customBack, false);
  applyHidePlayerId();
  bindEvents();
  await refreshServerStatus();

  // Check URL query for paipu parameter
  const urlParams = new URLSearchParams(window.location.search);
  const paipuParam = urlParams.get("paipu");
  if (paipuParam) {
    const input = $("homePaipuInput");
    if (input) input.value = paipuParam;
    await startAnalysisFromInput(paipuParam, "akagi", null);
  } else {
    const input = $("homePaipuInput");
    if (input) input.value = "";
    showTier(1);
  }
});
