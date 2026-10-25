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
  soundEnabled: localStorage.getItem("majsoul_sound") !== "0",
  selectedModel: "akagi",
  serverStatus: null,
  isPlaying: false,
  playTimer: null,
  hidePlayerId: localStorage.getItem("majsoul_hide_id") === "true",
  playSpeed: parseFloat(localStorage.getItem("majsoul_play_speed")) || 1.0,
};

const WIND_CN = ["东", "南", "西", "北"];
const TILE_CN_MAP = {
  E: "东", S: "南", W: "西", N: "北", P: "白", F: "发", C: "中",
  "5mr": "赤五万", "5pr": "赤五筒", "5sr": "赤五索",
};

// Web Audio API Player for tactile Mahjong tile discard
const SoundFX = {
  ctx: null,
  buffer: null,
  loading: false,
  currentUrl: null,
  getAudioUrl() {
    const variant = localStorage.getItem("majsoul_sound_variant") || "warm";
    if (variant === "soft") return "/audio/preview/discard_soft.wav";
    if (variant === "solid") return "/audio/preview/discard_solid.wav";
    if (variant === "crisp") return "/audio/preview/discard_crisp.wav";
    if (variant === "amos") return "/audio/preview/discard_amos.wav";
    return "/audio/discard.wav";
  },
  init(forceReload = false) {
    if (!this.ctx && (window.AudioContext || window.webkitAudioContext)) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx();
    }
    if (this.ctx && this.ctx.state === "suspended") {
      this.ctx.resume().catch(() => {});
    }
    const targetUrl = this.getAudioUrl();
    if ((!this.buffer || forceReload || this.currentUrl !== targetUrl) && !this.loading && this.ctx) {
      this.loading = true;
      this.currentUrl = targetUrl;
      fetch(targetUrl)
        .then((res) => {
          if (!res.ok) throw new Error("Audio fetch failed");
          return res.arrayBuffer();
        })
        .then((buf) => this.ctx.decodeAudioData(buf))
        .then((decoded) => {
          this.buffer = decoded;
          this.loading = false;
        })
        .catch(() => {
          this.loading = false;
        });
    }
  },
  playDiscard() {
    try {
      this.init();
      if (!this.ctx) return;

      const t = this.ctx.currentTime;
      // Organic pitch micro-variation
      const pitch = 0.96 + Math.random() * 0.08;

      if (this.buffer) {
        const source = this.ctx.createBufferSource();
        source.buffer = this.buffer;
        source.playbackRate.value = pitch;

        // Gentle lowpass filter at 3200Hz to eliminate harsh high-frequency spikes across earphones/speakers
        const filter = this.ctx.createBiquadFilter();
        filter.type = "lowpass";
        filter.frequency.setValueAtTime(3200, t);
        filter.Q.setValueAtTime(0.7, t);

        const gainNode = this.ctx.createGain();
        gainNode.gain.setValueAtTime(0.8, t);

        source.connect(filter);
        filter.connect(gainNode);
        gainNode.connect(this.ctx.destination);
        source.start(t);
        return;
      }

      // Warm fallback synthesizer if buffer is loading
      const oscClick = this.ctx.createOscillator();
      const gainClick = this.ctx.createGain();
      oscClick.type = "sine";
      oscClick.frequency.setValueAtTime(1550 * pitch, t);
      oscClick.frequency.exponentialRampToValueAtTime(820 * pitch, t + 0.016);
      gainClick.gain.setValueAtTime(0.25, t);
      gainClick.gain.exponentialRampToValueAtTime(0.001, t + 0.02);
      oscClick.connect(gainClick);
      gainClick.connect(this.ctx.destination);
      oscClick.start(t);
      oscClick.stop(t + 0.022);

      const oscBody = this.ctx.createOscillator();
      const gainBody = this.ctx.createGain();
      oscBody.type = "triangle";
      oscBody.frequency.setValueAtTime(680 * pitch, t);
      oscBody.frequency.exponentialRampToValueAtTime(380 * pitch, t + 0.035);
      gainBody.gain.setValueAtTime(0.32, t);
      gainBody.gain.exponentialRampToValueAtTime(0.001, t + 0.038);
      oscBody.connect(gainBody);
      gainBody.connect(this.ctx.destination);
      oscBody.start(t);
      oscBody.stop(t + 0.042);
    } catch (_) {
      // AudioContext unavailable or autoplay prevented
    }
  }
};

let lastSoundTime = 0;
function playTileDiscardSound() {
  if (!state.soundEnabled) return;
  const now = performance.now();
  if (now - lastSoundTime < 60) return;
  lastSoundTime = now;
  SoundFX.playDiscard();
}

function toggleSound() {
  state.soundEnabled = !state.soundEnabled;
  localStorage.setItem("majsoul_sound", state.soundEnabled ? "1" : "0");
  updateSoundButton();
  if (state.soundEnabled) {
    SoundFX.playDiscard();
  }
}

function updateSoundButton() {
  const txt = $("toggleAudioText");
  const btn = $("btnToggleAudio");
  if (txt) txt.textContent = state.soundEnabled ? "音效: 开" : "音效: 关";
  if (btn) {
    if (state.soundEnabled) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  }
}

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
    const targetActor = state.targetActor ?? 0;
    const isHeroDahai = snap?.event?.type === "dahai" && snap?.event?.actor === targetActor;
    const isExact = snap?.decision_idx !== null && snap?.decision_idx !== undefined && !isHeroDahai;
    const dec = isExact ? kyoku?.decisions?.[snap.decision_idx] : null;
    renderBoard(kyoku, snap, isExact ? dec : null);
    renderDecisionCard(dec, isExact, snap);
  }
}

function getTileBackSrc() {
  return "/tiles/back.svg";
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

function shantenText(s, agariLabel = "自摸") {
  if (s === undefined || s === null) return "-";
  if (s < 0) return agariLabel || "自摸";
  if (s === 0) return "听牌";
  return `${s} 向听`;
}

function tileSortOrder(pai) {
  if (!pai) return 999;
  const suitOrder = { m: 100, p: 200, s: 300, z: 400 };
  const charOrder = { E: 401, S: 402, W: 403, N: 404, P: 405, F: 406, C: 407, "1z": 401, "2z": 402, "3z": 403, "4z": 404, "5z": 405, "6z": 406, "7z": 407 };
  if (charOrder[pai]) return charOrder[pai];
  const num = pai[0] === "0" ? 5.5 : parseInt(pai[0], 10);
  const suit = pai[1];
  return (suitOrder[suit] || 900) + (isNaN(num) ? 0 : num);
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

// Lightweight Pure Native IndexedDB Cache for Offline Review Instant Load
const idbReviewCache = {
  _db: null,
  async getDb() {
    if (this._db) return this._db;
    return new Promise((resolve) => {
      if (!window.indexedDB) {
        resolve(null);
        return;
      }
      try {
        const req = indexedDB.open("MajsoulAnalyzerDB", 1);
        req.onupgradeneeded = (e) => {
          const db = req.result;
          if (!db.objectStoreNames.contains("reviews")) {
            const store = db.createObjectStore("reviews", { keyPath: "key" });
            store.createIndex("timestamp", "timestamp", { unique: false });
          }
        };
        req.onsuccess = () => {
          this._db = req.result;
          resolve(this._db);
        };
        req.onerror = (e) => {
          console.warn("IndexedDB open error:", e);
          resolve(null);
        };
      } catch (err) {
        console.warn("IndexedDB init failed:", err);
        resolve(null);
      }
    });
  },
  async get(key) {
    try {
      const db = await this.getDb();
      if (!db) return null;
      return new Promise((resolve) => {
        const tx = db.transaction("reviews", "readonly");
        const store = tx.objectStore("reviews");
        const req = store.get(key);
        req.onsuccess = () => resolve(req.result ? req.result.data : null);
        req.onerror = () => resolve(null);
      });
    } catch (e) {
      console.warn("IndexedDB get error:", e);
      return null;
    }
  },
  async set(key, data) {
    try {
      const db = await this.getDb();
      if (!db) return;
      return new Promise((resolve) => {
        const tx = db.transaction("reviews", "readwrite");
        const store = tx.objectStore("reviews");
        store.put({ key, data, timestamp: Date.now() });
        tx.oncomplete = () => {
          this.prune();
          resolve();
        };
        tx.onerror = () => resolve();
      });
    } catch (e) {
      console.warn("IndexedDB set error:", e);
    }
  },
  async findByGame(gameId, engine, seat = null) {
    if (seat !== null && seat !== undefined) {
      const exact = await this.get(`${gameId}_${seat}_${engine}`);
      if (exact) return exact;
      return await this.get(`${gameId}_${engine}_${seat}`);
    }
    const defaultSeat = await this.get(`${gameId}_0_${engine}`);
    if (defaultSeat) return defaultSeat;

    try {
      const db = await this.getDb();
      if (!db) return null;
      return new Promise((resolve) => {
        const tx = db.transaction("reviews", "readonly");
        const store = tx.objectStore("reviews");
        const req = store.openCursor();
        req.onsuccess = (e) => {
          const cursor = e.target.result;
          if (cursor) {
            const k = String(cursor.key);
            if (k.startsWith(`${gameId}_`) && (k.endsWith(`_${engine}`) || k.includes(`_${engine}_`))) {
              resolve(cursor.value.data);
              return;
            }
            cursor.continue();
          } else {
            resolve(null);
          }
        };
        req.onerror = () => resolve(null);
      });
    } catch (_) {
      return null;
    }
  },
  async prune(maxRecords = 20) {
    try {
      const db = await this.getDb();
      if (!db) return;
      const tx = db.transaction("reviews", "readwrite");
      const store = tx.objectStore("reviews");
      const idx = store.index("timestamp");
      const req = idx.getAllKeys();
      req.onsuccess = () => {
        const keys = req.result;
        if (keys && keys.length > maxRecords) {
          const countToDelete = keys.length - maxRecords;
          for (let i = 0; i < countToDelete; i++) {
            store.delete(keys[i]);
          }
        }
      };
    } catch (e) {
      console.warn("IndexedDB prune error:", e);
    }
  }
};

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

function setLoading(visible, title = "AI 牌谱智能检讨", sub = "Mortal 神经网络正在检讨每一手决策") {
  const overlay = $("loadingOverlay");
  if (!overlay) return;
  if (visible) {
    const normalState = $("analysisNormalState");
    const errorBox = $("analysisErrorBox");
    if (normalState) normalState.classList.remove("hidden");
    if (errorBox) errorBox.classList.add("hidden");
    if ($("loadingTitle")) $("loadingTitle").textContent = title;
    if ($("loadingSub")) $("loadingSub").textContent = sub || "Mortal 神经网络正在检讨每一手决策";
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
  if (errorDetail) errorDetail.textContent = errMsg || "检讨服务暂时不可用，请检查网络或重试。";
  errorBox.classList.remove("hidden");
  overlay.classList.remove("hidden");
}

async function fetchReviewStream(postBody, onProgress) {
  const url = buildApiUrl(getBackendUrl(), "/api/analyze/stream");
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(postBody),
  });

  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${errText || resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalResult = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep last incomplete line in buffer

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const jsonStr = trimmed.slice(5).trim();
      if (!jsonStr) continue;
      try {
        const ev = JSON.parse(jsonStr);
        if (ev.type === "progress") {
          onProgress(ev.percent || 0, ev.message || "");
        } else if (ev.type === "queued") {
          onProgress(5, ev.message || "服务器正忙，当前正在排队中...");
        } else if (ev.type === "complete") {
          finalResult = ev.result;
        } else if (ev.type === "error") {
          throw new Error(ev.detail || "分析发生错误");
        }
      } catch (err) {
        if (err.message && !err.message.includes("Unexpected token") && !err.message.includes("JSON")) {
          throw err;
        }
      }
    }
  }

  if (buffer.trim().startsWith("data:")) {
    try {
      const ev = JSON.parse(buffer.trim().slice(5).trim());
      if (ev.type === "complete") finalResult = ev.result;
      else if (ev.type === "error") throw new Error(ev.detail || "分析发生错误");
    } catch (e) {
      if (e.message && !e.message.includes("Unexpected token") && !e.message.includes("JSON")) {
        throw e;
      }
    }
  }

  if (!finalResult) {
    throw new Error("服务端未正常返回分析结果");
  }

  return finalResult;
}

const DEFAULT_MODELSCOPE_BACKEND = "https://gxyh513-majsoul-analyzer.ms.show?studio_token=REDACTED_STUDIO_TOKEN";

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

function formatModelDisplayName(rawName) {
  if (!rawName) return "Mortal-V4-582500 (进攻强化)";
  let name = String(rawName);
  name = name.replace(/赤木\s*Akagi/gi, "Mortal-V4-582500")
             .replace(/赤木/g, "Mortal-V4-582500")
             .replace(/Akagi/gi, "Mortal-V4-582500")
             .replace(/v4\s*582k步\s*/gi, "")
             .replace(/mortal-v4-b40c192-26082106/gi, "")
             .trim();
  if (name.includes("Mortal-V4-582500") && !name.includes("进攻强化")) {
    name = "Mortal-V4-582500 (进攻强化)";
  }
  return name;
}

function updateEngineStatusDisplay(modelId, device) {
  const targetModel = (modelId || state.selectedModel || "akagi").toLowerCase();
  const dev = device || state.serverStatus?.engine?.device || "CPU";
  let friendlyName = "Mortal-V4-582500";
  if (targetModel.includes("mortal")) {
    friendlyName = "Mortal";
  } else if (targetModel.includes("akagi")) {
    friendlyName = "Mortal-V4-582500";
  } else {
    const available = state.serverStatus?.engine?.available_models || [];
    const found = available.find((m) => m.id && m.id.toLowerCase() === targetModel);
    if (found && found.name) {
      friendlyName = formatModelDisplayName(found.name).split("(")[0].trim() || found.name;
    } else {
      friendlyName = modelId || state.selectedModel || "Mortal-V4-582500";
    }
  }

  const btn = $("engineStatusText");
  if (btn) {
    btn.textContent = `${friendlyName} · ${dev}`;
  }
}

function updateTopbarStatus(st) {
  const eng = st.engine || {};
  updateEngineStatusDisplay(state.selectedModel || eng.selected_model, eng.device);

  const sel = $("engineSelect");
  if (sel) {
    const models = (eng.available_models && eng.available_models.length > 0)
      ? eng.available_models
      : [{ id: "akagi", name: "Mortal-V4-582500 (进攻强化)" }];
    sel.innerHTML = models
      .map((m) => `<option value="${m.id}">${escapeHtml(formatModelDisplayName(m.name))}</option>`)
      .join("");
    if (state.selectedModel) {
      sel.value = state.selectedModel;
    } else if (eng.selected_model) {
      sel.value = eng.selected_model;
    }
  }

  const detail = $("engineModalDetail");
  if (detail) {
    detail.innerHTML = `
      <div><b>当前模型：</b>${escapeHtml(formatModelDisplayName(eng.model_name || "Mortal-V4-582500"))}</div>
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
    updateEngineStatusDisplay();
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
        let res;
        if (gameId === "demo" || !isBackendOnline()) {
          try {
            const resp = await fetch(`/demo_review_${seat}.json`).catch(() => fetch("/demo_review.json"));
            if (resp && resp.ok) res = await resp.json();
          } catch (_) {}
        }
        if (!res) {
          const p = apiFetch("/api/analyze/paipu", {
            method: "POST",
            body: JSON.stringify({ paipu: gameId, target_actor: seat, engine: model }),
          });
          inFlightRequests[cacheKey] = p;
          res = await p;
        }
        if (res) {
          if (!state.perspectiveCache) state.perspectiveCache = {};
          state.perspectiveCache[cacheKey] = res;
          await idbReviewCache.set(`${gameId}_${seat}_${model}`, res);
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
    rawInput = "demo";
  }
  const trimmedInput = rawInput.trim();
  const isJsonInput = trimmedInput.startsWith("{") || trimmedInput.startsWith("[");
  const paipuId = isJsonInput ? "custom-json" : parsePaipuUuid(trimmedInput);
  state.selectedModel = model;
  updateEngineStatusDisplay(model);

  // 1. 检查内存缓存
  const cacheKey = seat !== null && seat !== undefined ? `${paipuId}_${model}_${seat}` : null;
  if (!isJsonInput && cacheKey && state.perspectiveCache && state.perspectiveCache[cacheKey]) {
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

  // 2. 检查 IndexedDB 离线持久缓存（0ms 秒开）
  if (!isJsonInput) {
    try {
      const idbCached = await idbReviewCache.findByGame(paipuId, model, seat);
      if (idbCached) {
        state.reviewData = idbCached;
        state.gameId = idbCached.game_id || paipuId;
        state.targetActor = idbCached.target_actor !== undefined ? idbCached.target_actor : (seat ?? 0);
        state.kyokuIdx = 0;

        if (!state.perspectiveCache) state.perspectiveCache = {};
        const k1 = `${state.gameId}_${state.targetActor}_${model}`;
        const k2 = `${state.gameId}_${model}_${state.targetActor}`;
        state.perspectiveCache[k1] = idbCached;
        state.perspectiveCache[k2] = idbCached;

        showTier(2);

        const newUrl = new URL(window.location.href);
        newUrl.searchParams.set("paipu", paipuId);
        window.history.pushState(null, "", newUrl.toString());

        renderSummaryDashboard();
        selectKyoku(0, true);
        showToast("已从本地离线缓存秒级载入", "success");
        preloadOtherPerspectives(state.gameId, state.selectedModel, state.targetActor);
        return;
      }
    } catch (e) {
      console.warn("IndexedDB cache check error:", e);
    }
  }

  setLoading(true, "正在分析牌谱...", "正在使用深度神经网络检讨每一手决策");

  try {
    let res;
    if (paipuId === "demo" && !isBackendOnline()) {
      try {
        const s = seat !== null && seat !== undefined ? seat : 0;
        const resp = await fetch(`/demo_review_${s}.json`).catch(() => fetch("/demo_review.json"));
        if (resp && resp.ok) {
          res = await resp.json();
        }
      } catch (e) {
        console.warn("Static demo fetch fallback:", e);
      }
    }

    if (!res) {
      const postBody = isJsonInput
        ? { content: trimmedInput, engine: model }
        : { paipu: trimmedInput, engine: model };
      if (seat !== null && seat !== undefined) {
        postBody.target_actor = seat;
      }

      try {
        res = await fetchReviewStream(postBody, (pct, msg) => {
          updateAnalysisProgress(pct, msg);
        });
      } catch (streamErr) {
        console.warn("SSE stream failed, falling back to /api/analyze/paipu:", streamErr);
        try {
          const fallbackEndpoint = isJsonInput ? "/api/analyze/json" : "/api/analyze/paipu";
          res = await apiFetch(fallbackEndpoint, {
            method: "POST",
            body: JSON.stringify(postBody),
          });
        } catch (apiErr) {
          throw new Error(apiErr.message || streamErr.message);
        }
      }
    }

    state.reviewData = res;
    state.gameId = res.game_id || paipuId;
    state.targetActor = res.target_actor !== undefined ? res.target_actor : (seat ?? 0);
    state.kyokuIdx = 0;

    if (!state.perspectiveCache) state.perspectiveCache = {};
    const k1 = `${state.gameId}_${state.targetActor}_${state.selectedModel}`;
    const k2 = `${state.gameId}_${state.selectedModel}_${state.targetActor}`;
    state.perspectiveCache[k1] = res;
    state.perspectiveCache[k2] = res;

    // 保存到 IndexedDB
    await idbReviewCache.set(k1, res);

    showTier(2);

    const newUrl = new URL(window.location.href);
    newUrl.searchParams.set("paipu", paipuId);
    window.history.pushState(null, "", newUrl.toString());

    renderSummaryDashboard();
    selectKyoku(0, true);
    showToast(`分析完成：共检讨 ${res.kyokus?.length || 0} 局`, "success");

    preloadOtherPerspectives(state.gameId, state.selectedModel, state.targetActor);
    setLoading(false);
  } catch (err) {
    if (paipuId === "demo" || !isBackendOnline()) {
      try {
        const s = seat !== null && seat !== undefined ? seat : 0;
        const resp = await fetch(`/demo_review_${s}.json`).catch(() => fetch("/demo_review.json"));
        if (resp && resp.ok) {
          const res = await resp.json();
          state.reviewData = res;
          state.gameId = res.game_id || "demo";
          state.targetActor = res.target_actor !== undefined ? res.target_actor : s;
          state.kyokuIdx = 0;
          if (!state.perspectiveCache) state.perspectiveCache = {};
          state.perspectiveCache[`${state.gameId}_${state.selectedModel}_${state.targetActor}`] = res;
          showTier(2);
          renderSummaryDashboard();
          selectKyoku(0, true);
          showToast(`已载入示例牌谱（共检讨 ${res.kyokus?.length || 0} 局）`, "success");
          preloadOtherPerspectives(state.gameId, state.selectedModel, state.targetActor);
          setLoading(false);
          return;
        }
      } catch (_) {}
    }
    showAnalysisError(`分析失败：${err.message}`, () => {
      startAnalysisFromInput(rawInput, model, seat);
    });
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

  // 1.5 检查 IndexedDB 本地缓存
  try {
    const idbCached = await idbReviewCache.findByGame(paipuId, state.selectedModel, newSeat);
    if (idbCached) {
      if (!state.perspectiveCache) state.perspectiveCache = {};
      state.perspectiveCache[cacheKey] = idbCached;
      state.reviewData = idbCached;
      state.targetActor = newSeat;
      renderSummaryDashboard();
      selectKyoku(state.kyokuIdx);
      if ($("roundModal") && !$("roundModal").classList.contains("hidden")) {
        renderKyokuTable();
      }
      return;
    }
  } catch (_) {}

  // 2. 若未命中缓存：仅在首次请求该视角时向后端请求
  const seatName = state.reviewData?.names?.[newSeat] ?? `${newSeat}`;
  setLoading(true, `切换到「${seatName}」视角...`);
  try {
    let res;
    if (paipuId === "demo" || !isBackendOnline()) {
      try {
        const resp = await fetch(`/demo_review_${newSeat}.json`).catch(() => fetch("/demo_review.json"));
        if (resp && resp.ok) res = await resp.json();
      } catch (_) {}
    }
    if (!res) {
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
    }
    if (!state.perspectiveCache) state.perspectiveCache = {};
    state.perspectiveCache[cacheKey] = res;
    await idbReviewCache.set(`${paipuId}_${newSeat}_${state.selectedModel}`, res);
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
    pill.innerHTML = `
      <span class="kd-badge-match">模型一致率: <strong>${fmtPct(sum.match_rate)}</strong></span>
      <span class="kd-badge-sep">|</span>
      <span class="kd-badge-blunder">恶手: <strong>${sum.blunders || 0}</strong></span>
      <span class="kd-badge-sep">|</span>
      <span class="kd-badge-rating">Rating: <strong>${(sum.rating ?? 0).toFixed(1)}</strong></span>
    `;
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
        <span class="kd-seat-wind">${wind}家</span><span class="kd-seat-name">${escapeHtml(displayName)}</span>
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

// RAF-based Step Debouncing & Scheduler to eliminate layout thrashing on key-hold
let pendingTargetStep = null;
let stepRafId = null;

function scheduleStep(targetStep) {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku || !kyoku.steps.length) return;
  pendingTargetStep = Math.max(0, Math.min(kyoku.steps.length - 1, targetStep));

  if (stepRafId !== null) return;

  stepRafId = requestAnimationFrame(() => {
    stepRafId = null;
    if (pendingTargetStep !== null) {
      const step = pendingTargetStep;
      pendingTargetStep = null;
      setStep(step);
    }
  });
}

function cancelScheduledStep() {
  if (stepRafId !== null) {
    cancelAnimationFrame(stepRafId);
    stepRafId = null;
  }
  pendingTargetStep = null;
}

function stepDeltaFast(delta) {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return;
  const current = pendingTargetStep !== null ? pendingTargetStep : state.stepIdx;
  const target = current + delta;
  if (target >= 0 && target < kyoku.steps.length) {
    scheduleStep(target);
  } else {
    cancelScheduledStep();
    stepDelta(delta);
  }
}

function jumpPrevBlunderFast() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.decisions?.length) return;
  const current = pendingTargetStep !== null ? pendingTargetStep : state.stepIdx;
  const decs = kyoku.decisions;
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < current && !decs[i].is_equal) {
      scheduleStep(decs[i].step_idx);
      return;
    }
  }
  cancelScheduledStep();
  jumpPrevBlunder();
}

function jumpNextBlunderFast() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.decisions?.length) return;
  const current = pendingTargetStep !== null ? pendingTargetStep : state.stepIdx;
  for (const d of kyoku.decisions) {
    if (d.step_idx > current && !d.is_equal) {
      scheduleStep(d.step_idx);
      return;
    }
  }
  cancelScheduledStep();
  jumpNextBlunder();
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

function getPlayIntervalMs() {
  const speed = state.playSpeed || 1.0;
  return Math.max(150, Math.round(800 / speed));
}

function setPlaySpeed(speed) {
  const num = parseFloat(speed);
  if (isNaN(num) || num <= 0) return;
  state.playSpeed = num;
  localStorage.setItem("majsoul_play_speed", String(num));
  const sel = $("playSpeedSelect");
  if (sel && sel.value !== String(num)) {
    sel.value = String(num);
  }
  if (state.isPlaying && state.playTimer) {
    clearInterval(state.playTimer);
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
    }, getPlayIntervalMs());
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
    }, getPlayIntervalMs());
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
  const getPlayerLabel = (seat) => {
    if (seat === undefined || seat === null) return "";
    const w = currentKyoku ? handWind(seat, currentKyoku) : "";
    const isHero = seat === state.targetActor;
    const pName = ` ${getPlayerDisplayName(seat)}`;
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
        return `${actorLabel} 自摸${deltaStr}`;
      }
    case "ryuukyoku":
    case "ryukyoku":
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

  const prevStep = state.stepIdx;
  const clamped = Math.max(0, Math.min(kyoku.steps.length - 1, stepIdx));
  state.stepIdx = clamped;

  const snap = kyoku.steps[clamped];

  // Play tile discard sound when advancing or landing on a discard
  if (clamped !== prevStep && snap?.event?.type === "dahai") {
    playTileDiscardSound();
  }

  if ($("stepSlider")) $("stepSlider").value = clamped;
  if ($("stepCounterText")) $("stepCounterText").textContent = `${clamped} / ${kyoku.steps.length - 1}`;

  const targetActor = state.targetActor ?? 0;
  const isHeroDahai = snap?.event?.type === "dahai" && snap?.event?.actor === targetActor;
  const isExactDecisionStep = snap.decision_idx !== null && snap.decision_idx !== undefined && !isHeroDahai;
  const decision = isExactDecisionStep ? kyoku.decisions[snap.decision_idx] : null;

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
  const honbaCount = snap.honba ?? 0;
  const kyotakuCount = snap.kyotaku ?? 0;
  if (btnRound) {
    if (honbaCount > 0 || kyotakuCount > 0) {
      btnRound.innerHTML = `
        <span class="info-round-title">${escapeHtml(roundName)}</span>
        <span class="info-round-sub">
          ${honbaCount > 0 ? `<span class="info-tag-honba">${honbaCount}本场</span>` : ""}
          ${kyotakuCount > 0 ? `<span class="info-tag-kyotaku"><span class="riichi-stick-mini"></span>${kyotakuCount * 1000}</span>` : ""}
        </span>
      `;
      btnRound.title = `${escapeHtml(roundName)}${honbaCount > 0 ? ` ${honbaCount}本场` : ""}${kyotakuCount > 0 ? ` · 场供供托 ${kyotakuCount * 1000}点` : ""} · 点击查看局数得点表`;
    } else {
      btnRound.innerHTML = `<span class="info-round-title only">${escapeHtml(roundName)}</span>`;
      btnRound.title = `${escapeHtml(roundName)} · 点击查看局数得点表`;
    }
  }

  const tilesLeftEl = $("centerTilesLeft");
  if (tilesLeftEl) tilesLeftEl.textContent = `x${snap.tiles_left ?? 70}`;

  const honbaEl = $("centerHonba");
  if (honbaEl) honbaEl.style.display = "none";

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
  if (giP0) giP0.innerHTML = `<span class="gi-wind">${handWind(hero, kyoku)}</span><span class="gi-score">${snap.scores[hero] ?? 25000}</span>`;
  const giP1 = $("giP1");
  if (giP1) giP1.innerHTML = `<span class="gi-wind">${handWind(p1, kyoku)}</span><span class="gi-score">${snap.scores[p1] ?? 25000}</span>`;
  const giP2 = $("giP2");
  if (giP2) giP2.innerHTML = `<span class="gi-wind">${handWind(p2, kyoku)}</span><span class="gi-score">${snap.scores[p2] ?? 25000}</span>`;
  const giP3 = $("giP3");
  if (giP3) giP3.innerHTML = `<span class="gi-wind">${handWind(p3, kyoku)}</span><span class="gi-score">${snap.scores[p3] ?? 25000}</span>`;

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
  const fullRiver = snap.rivers[seat] || [];
  const river = fullRiver.filter((r) => !r.called);
  const isLastDiscardActor = snap.event?.type === "dahai" && snap.event?.actor === seat;
  const isRonTarget = isRon && seat === targetActor;

  // Avoid rebuilding river DOM if length, last tile, and flags have not changed
  const lastR = river.length > 0 ? river[river.length - 1] : null;
  const lastSig = lastR ? `${lastR.pai}_${lastR.tsumogiri ? 1 : 0}_${lastR.riichi ? 1 : 0}_${lastR.called ? 1 : 0}` : "none";
  const sig = `${river.length}#${lastSig}#${isLastDiscardActor ? 1 : 0}#${isRonTarget ? 1 : 0}`;
  if (container.dataset.riverSig === sig) {
    return;
  }
  container.dataset.riverSig = sig;

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
  const actualPai = activeDecision?.actual?.pai || null;

  // Only highlight the intended discard in hand during active decision step (before tile is played)
  let highlightedIdx = -1;
  if (activeDecision && actualPai) {
    allTiles.forEach(({ pai }, idx) => {
      if (actualPai === pai && highlightedIdx === -1) {
        highlightedIdx = idx;
      }
    });
  }

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
    handHtml += `<div class="tileDiv ${isWinTile ? "agari-tile" : ""}" data-pai="${escapeHtml(drawn)}" title="${isWinTile ? `自摸: ${tileCn(drawn)}` : tileCn(drawn)}"><img src="/tiles/${encodeURIComponent(drawn)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""} ${isWinTile ? "agari-tile" : ""}"></div>`;
  }

  closedWrap.innerHTML = handHtml;

  // 2. Render Green Discard Recommendation Bars (SVG) — Absolutely Centered on Actual DOM Tiles
  if (svgEl) {
    const barKeys = Object.keys(tileBars);
    const hasAnyBars = barKeys.some((k) => (tileBars[k]?.prob || 0) > 0);
    if (!hasAnyBars) {
      if (svgEl.innerHTML !== "") svgEl.innerHTML = "";
    } else {
      let svgHtml = "";
      const renderedTiles = closedWrap.querySelectorAll(".tileDiv:not(.narrow)");
      // Batch DOM measurements together to eliminate layout thrashing
      const svgRect = svgEl.getBoundingClientRect();
      const tileRects = Array.from(renderedTiles).map((t) => t.getBoundingClientRect());

      if (svgRect.width > 0) {
        svgEl.setAttribute("viewBox", `0 0 ${svgRect.width} 50`);
      }
      const seenPai = new Set();
      renderedTiles.forEach((tileEl, idx) => {
        if (idx < allTiles.length) {
          const pai = allTiles[idx].pai;
          if (seenPai.has(pai)) return;
          seenPai.add(pai);
          const barInfo = tileBars[pai];
          const prob = barInfo ? barInfo.prob : 0;
          if (prob > 0 && idx < tileRects.length) {
            const tileRect = tileRects[idx];
            const centerX = (tileRect.left + tileRect.width / 2) - svgRect.left;
            const height = Math.max(3, Math.round((prob / 100) * 45));
            const y = 50 - height;
            svgHtml += `<rect x="${Math.round(centerX - 5)}" y="${y}" width="10" height="${height}" fill="#30d158" rx="3"></rect>`;
          }
        }
      });
      svgEl.innerHTML = svgHtml;
    }
  }

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
    html += `<div class="tileDiv ${isWinner ? "agari-tile" : ""}"><img src="${src}" class="tileImg ${isWinner ? "agari-tile" : ""}" title="${isWinner ? `自摸: ${tileCn(drawn)}` : ""}"></div>`;
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
  const actType = (actionObj?.type || actionObj?.action || actionCn || (pai ? "dahai" : "pass")).toLowerCase();

  if (actType.includes("pass") || actType.includes("none") || actType.includes("跳过") || actType.includes("过") || actType.includes("不鸣")) {
    return `
      <div class="action-badge action-badge-pass" title="PASS">
        <span class="action-badge-text">PASS</span>
      </div>
    `;
  }
  if (actType.includes("chi") || actType.includes("吃")) {
    return `
      <div class="action-badge action-badge-chi" title="Chi">
        <span class="action-badge-text">Chi</span>
      </div>
    `;
  }
  if (actType.includes("pon") || actType.includes("碰")) {
    return `
      <div class="action-badge action-badge-pon" title="Pon">
        <span class="action-badge-text">Pon</span>
      </div>
    `;
  }
  if (actType.includes("kan") || actType.includes("杠")) {
    return `
      <div class="action-badge action-badge-kan" title="Kan">
        <span class="action-badge-text">Kan</span>
      </div>
    `;
  }
  if (actType.includes("riichi") || actType.includes("reach") || actType.includes("立直")) {
    const reachTile = pai || actionObj?.pai;
    if (reachTile && reachTile !== "none" && reachTile !== "pass" && !reachTile.startsWith("none")) {
      return `<img src="/tiles/${encodeURIComponent(reachTile)}.svg" class="tileImg" title="立直: ${tileCn(reachTile)}">`;
    }
    return `
      <div class="action-badge action-badge-riichi" title="立直">
        <span class="action-badge-text">立直</span>
      </div>
    `;
  }
  if (actType.includes("hora") || actType.includes("ron") || actType.includes("tsumo") || actType.includes("和")) {
    const isRon = actType.includes("ron") || actType.includes("荣") || actionCn.includes("荣");
    const label = isRon ? "Ron" : "自摸";
    return `
      <div class="action-badge action-badge-hora" title="${label}">
        <span class="action-badge-text">${label}</span>
      </div>
    `;
  }

  if (pai && pai !== "none" && pai !== "pass" && !pai.startsWith("none")) {
    return `<img src="/tiles/${encodeURIComponent(pai)}.svg" class="tileImg">`;
  }

  const defaultLabel = actionCn ? escapeHtml(actionCn) : "等待";
  return `
    <div class="action-badge action-badge-pass" style="opacity:0.65;">
      <span class="action-badge-text">${defaultLabel}</span>
    </div>
  `;
}

// ============================================================================
// Active Decision Details Card (In Right Sidebar)
// ============================================================================
const MAJSOUL_YAKU_NAMES = {
  1: "门前清自摸和",
  2: "立直",
  3: "两立直",
  4: "枪杠",
  5: "岭上开花",
  6: "海底捞月",
  7: "役牌 白",
  8: "役牌 发",
  9: "役牌 中",
  10: "场风牌",
  11: "自风牌",
  12: "断幺九",
  13: "一杯口",
  14: "平和",
  15: "河底捞鱼",
  16: "一气通贯",
  17: "三色同顺",
  18: "三色同刻",
  19: "混老头",
  20: "对对和",
  21: "三暗刻",
  22: "三暗刻",
  23: "三杠子",
  24: "小三元",
  25: "七对子",
  26: "纯全带幺九",
  27: "混一色",
  28: "清一色",
  29: "二杯口",
  30: "一发",
  31: "宝牌",
  32: "红宝牌",
  33: "里宝牌",
  34: "拔北",
  37: "混全带幺九",
  38: "国士无双",
  39: "九莲宝灯",
  40: "四暗刻",
  41: "大三元",
  42: "字一色",
  43: "清老头",
  44: "绿一色",
  45: "小四喜",
  46: "大四喜",
  47: "四杠子",
  48: "天和",
  49: "地和",
  50: "国士无双十三面",
  51: "四暗刻单骑",
  52: "纯正九莲宝灯",
};

const MAJSOUL_TITLES = {
  1: "满贯",
  2: "跳满",
  3: "倍满",
  4: "三倍满",
  5: "役满",
  6: "两倍役满",
  7: "三倍役满",
  8: "四倍役满",
  9: "五倍役满",
};

function renderDecisionCard(dec, isExactStep, snap) {
  const card = $("decisionCard");
  if (!card) return;

  const headerEl = card.querySelector(".kd-dec-header");
  const turnEl = $("decTurnText");
  const badgeEl = $("decVerdictBadge");
  const compWrap = $("decComparisonWrap");
  const settWrap = $("decSettlementWrap");
  const candTitle = $("decCandTitle");
  const candTable = $("decCandTable");
  const candTableHead = $("decCandTableHead");

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
  const actualLabel = card.querySelector(".kd-choice-actual .kd-choice-badge-label");
  const aiLabel = card.querySelector(".kd-choice-ai .kd-choice-badge-label");

  const tableBody = $("decCandTableBody");

  const targetActor = state.targetActor ?? 0;
  const currentKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  const wind = currentKyoku ? handWind(targetActor, currentKyoku) : "";
  const names = [0, 1, 2, 3].map((s) => getPlayerDisplayName(s));
  const isHora = snap?.event?.type === "hora";
  const isRyuukyoku = snap?.event?.type === "ryuukyoku" || snap?.event?.type === "ryukyoku";

  const setTileWrapContent = (wrap, html) => {
    if (!wrap) return;
    wrap.innerHTML = html;
    wrap.classList.toggle("has-action-badge", html.includes("action-badge"));
    wrap.classList.toggle("has-rotate", html.includes("rotate"));
  };

  // CASE 1: Round End (Hora / 自摸/荣和结算) - Clean Standalone Round Settlement Card
  if (isHora) {
    card.classList.remove("is-idle");
    if (headerEl) headerEl.style.display = "flex";
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
    const rawSummaryText = currentKyoku?.end_summaries?.length
      ? currentKyoku.end_summaries.join("<br>")
      : (isRon ? `${winWind}家 ${escapeHtml(winName)} 荣和 ${tgtWind}家 ${escapeHtml(tgtName)} +${scoreDelta}` : `${winWind}家 ${escapeHtml(winName)} 自摸 +${scoreDelta}`);
    const summaryText = sanitizeSummaryText(rawSummaryText);

    if (turnEl) turnEl.textContent = "";

    // 1. Title badge: [自家自摸] / [自家荣和] / [自家放铳] / [X家自摸] / [X家荣和]
    if (badgeEl) {
      if (isHeroWin) {
        badgeEl.textContent = isRon ? "自家荣和" : "自家自摸";
        badgeEl.className = "kd-dec-verdict best round-end";
      } else if (isHeroLoser) {
        badgeEl.textContent = "自家放铳";
        badgeEl.className = "kd-dec-verdict blunder round-end";
      } else {
        badgeEl.textContent = `${winWind}家${isRon ? "荣和" : "自摸"}`;
        badgeEl.className = "kd-dec-verdict agari round-end";
      }
    }

    // 2. Hide active decision comparison blocks completely (Duelling 'Player vs AI' eliminated on round settlement)
    if (compWrap) compWrap.style.display = "none";
    setTileWrapContent(actualTileWrap, "");
    setTileWrapContent(aiTileWrap, "");
    if (actualTileName) actualTileName.textContent = "";
    if (aiTileName) aiTileName.textContent = "";

    // 3. Identify Winning Agari Tile
    let agariTile = null;
    if (isRon && tgtActor !== null && tgtActor !== undefined) {
      const river = snap.rivers?.[tgtActor] || [];
      if (river.length > 0) agariTile = river[river.length - 1].pai;
      if (!agariTile && currentKyoku?.steps?.[state.stepIdx - 1]?.event?.pai) {
        agariTile = currentKyoku.steps[state.stepIdx - 1].event.pai;
      }
    } else if (winActor !== null && winActor !== undefined) {
      agariTile = snap.drawn?.[winActor];
      if (!agariTile && currentKyoku?.steps?.[state.stepIdx - 1]?.event?.pai) {
        agariTile = currentKyoku.steps[state.stepIdx - 1].event.pai;
      }
    }

    // Ura Dora Information
    const uras = snap.event.ura_markers || [];
    const uraHtml = uras.length
      ? `<div class="kd-settlement-uradora-badge">
           <span class="kd-settlement-uradora-label">里宝牌</span>
           <div class="kd-settlement-uradora-tiles">
             ${uras.map((u) => `<img src="/tiles/${encodeURIComponent(u)}.svg" class="kd-settlement-uradora-tile" title="里宝牌: ${tileCn(u)}" alt="${tileCn(u)}">`).join("")}
           </div>
         </div>`
      : `<div class="kd-settlement-uradora-badge"><span class="kd-settlement-uradora-label">里宝牌: 无</span></div>`;

    // 4. Render Single Prominent Settlement Banner & 4-Seat Delta Grid directly inside decSettlementWrap
    const roundHonba = snap.honba ?? currentKyoku?.honba ?? 0;
    const honbaPts = roundHonba * 300;
    const roundKyotaku = snap.kyotaku ?? currentKyoku?.kyotaku ?? 0;
    const kyotakuPts = roundKyotaku * 1000;
    const dadian = snap.event?.dadian || (scoreDelta > 0 ? (scoreDelta - honbaPts - kyotakuPts) : null);

    // Extract Han & Fu and Title
    let han = snap.event?.han ?? snap.event?.count ?? null;
    let fu = snap.event?.fu ?? null;
    let titleId = snap.event?.title_id ?? 0;

    if (han === null && rawSummaryText) {
      const m = rawSummaryText.match(/(\d+)符\s*(\d+)番/);
      if (m) {
        fu = parseInt(m[1], 10);
        han = parseInt(m[2], 10);
      }
    }

    let titleName = MAJSOUL_TITLES[titleId] || null;
    if (!titleName && han !== null) {
      if (han >= 13) titleName = "役满";
      else if (han >= 11) titleName = "三倍满";
      else if (han >= 8) titleName = "倍满";
      else if (han >= 6) titleName = "跳满";
      else if (han === 5 || (han === 4 && fu >= 40) || (han === 3 && fu >= 70)) titleName = "满贯";
    }

    let titleCls = "";
    if (titleName === "满贯") titleCls = "mangan";
    else if (titleName === "跳满") titleCls = "haneman";
    else if (titleName && (titleName.includes("倍满") || titleName.includes("役满"))) titleCls = "yakuman";

    const hanFuPillHtml = (han !== null && han !== undefined)
      ? `<div class="kd-settlement-hanfu-row">
           <span class="kd-settlement-hanfu-pill">${fu ? `${fu} 符 ` : ""}${han} 番</span>
           ${dadian && dadian > 0 ? `<span class="kd-settlement-dadian-pill">手牌 ${dadian} 点</span>` : ""}
           ${titleName ? `<span class="kd-settlement-title-pill ${titleCls}">${titleName}</span>` : ""}
         </div>`
      : "";

    // Subtitle under total score
    let scoreSubParts = [];
    if (kyotakuPts > 0) scoreSubParts.push(`场供 +${kyotakuPts}`);
    if (honbaPts > 0) scoreSubParts.push(`本场 +${honbaPts}`);
    const scoreSubText = scoreSubParts.length > 0 ? `(含${scoreSubParts.join(" / ")})` : "";

    // Yaku Breakdown Chips
    const rawFans = snap.event?.fans || [];
    const validFans = rawFans
      .filter((f) => (f.val || 0) > 0)
      .map((f) => {
        const name = f.name || MAJSOUL_YAKU_NAMES[f.id] || `役种 ${f.id}`;
        const isDora = f.id === 31 || f.id === 32 || f.id === 33 || name.includes("宝牌");
        return { name, val: f.val, isDora };
      });

    const yakuChipsHtml = validFans.length > 0
      ? `<div class="kd-settlement-yaku-grid">
           ${validFans.map((f) => `
             <span class="kd-settlement-yaku-chip ${f.isDora ? 'dora' : ''}">
               <span class="kd-yaku-name">${escapeHtml(f.name)}</span>
               <span class="kd-yaku-val">${f.val}番</span>
             </span>
           `).join("")}
         </div>`
      : (summaryText ? `<div class="kd-settlement-yaku-text">${summaryText}</div>` : "");

    if (settWrap) {
      settWrap.style.display = "flex";
      const bannerClass = isHeroWin ? "is-hero-win" : isHeroLoser ? "is-hero-blunder" : "";
      settWrap.innerHTML = `
        <div class="kd-settlement-banner ${bannerClass}">
          <div class="kd-settlement-banner-top">
            ${agariTile ? `
              <div class="kd-settlement-agari-tile-wrap" title="和了牌: ${tileCn(agariTile)}">
                <img src="/tiles/${encodeURIComponent(agariTile)}.svg" class="tileImg agari-tile" alt="${tileCn(agariTile)}">
              </div>
            ` : `
              <div class="kd-settlement-agari-tile-wrap" style="background:transparent; border:none; box-shadow:none;">
                <span class="action-badge-text" style="color:#30d158;">${isRon ? "Ron" : "自摸"}</span>
              </div>
            `}
            <div class="kd-settlement-banner-info">
              <div class="kd-settlement-winner-title">
                <span class="kd-settlement-seat-wind">${winWind}家</span>
                <span class="kd-settlement-player-highlight winner">${escapeHtml(winName)}</span>
                <span class="kd-settlement-win-badge ${isRon ? 'ron' : 'tsumo'}">${isRon ? "荣和" : "自摸"}</span>
              </div>
              ${hanFuPillHtml}
            </div>
            <div class="kd-settlement-score-tag ${scoreDelta < 0 ? 'neg' : ''}">
              <span class="kd-settlement-score-num">${scoreDelta > 0 ? `+${scoreDelta}` : scoreDelta} 点</span>
              ${scoreSubText ? `<span class="kd-settlement-score-sub">${scoreSubText}</span>` : ""}
            </div>
          </div>

          <div class="kd-settlement-details-box">
            ${isRon ? `
              <div class="kd-settlement-loser-line">
                <span class="kd-settlement-loser-action">放铳:</span>
                <span class="kd-settlement-seat-wind">${tgtWind}家</span>
                <span class="kd-settlement-player-highlight loser">${escapeHtml(tgtName)}</span>
                <span class="kd-settlement-loser-points">(-${Math.abs(snap.event.deltas?.[tgtActor] || 0)}点${honbaPts > 0 ? `，含本场+${honbaPts}` : ""})</span>
              </div>
            ` : `
              <div class="kd-settlement-tsumo-action">
                自摸和了 ${roundHonba > 0 ? `<span class="kd-settlement-tsumo-honba-note">(含各家本场+${roundHonba * 100}点)</span>` : ""}
              </div>
            `}
            ${uraHtml}
            ${yakuChipsHtml}
          </div>

          ${roundHonba > 0 ? `
            <div class="kd-settlement-bonus-strip honba">
              <div class="kd-bonus-pill">
                <span class="honba-stick-mini"></span>
                <span class="kd-bonus-tag">本场加成</span>
              </div>
              <div class="kd-bonus-pts">+${honbaPts} 点</div>
              <div class="kd-bonus-desc">${roundHonba} 本场 (${isRon ? `放铳全付` : `各家付 +${roundHonba * 100}`})</div>
            </div>
          ` : ""}

          ${roundKyotaku > 0 ? `
            <div class="kd-settlement-bonus-strip kyotaku">
              <div class="kd-bonus-pill">
                <span class="riichi-stick-mini"></span>
                <span class="kd-bonus-tag">场供收入</span>
              </div>
              <div class="kd-bonus-pts">+${kyotakuPts} 点</div>
              <div class="kd-bonus-desc">含 ${roundKyotaku} 根立直供托棒</div>
            </div>
          ` : ""}
        </div>

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
                <div class="kd-settlement-player-name">${escapeHtml(pName)}</div>
                <div class="kd-settlement-score-diff ${deltaClass}">${deltaFormatted}</div>
              </div>
            `;
          }).join("")}
        </div>
      `;
    }

    // 5. Hide Candidates Table completely on Round End
    if (candTitle) candTitle.style.display = "none";
    if (candTable) candTable.style.display = "none";
    if (tableBody) tableBody.innerHTML = "";
    return;
  }

  // CASE 2: Round End (Ryuukyoku / 流局结算)
  if (isRyuukyoku) {
    card.classList.remove("is-idle");
    if (headerEl) headerEl.style.display = "flex";
    if (turnEl) turnEl.textContent = "";
    if (badgeEl) {
      badgeEl.textContent = "荒牌流局";
      badgeEl.className = "kd-dec-verdict cruise round-end";
    }

    if (compWrap) compWrap.style.display = "none";
    setTileWrapContent(actualTileWrap, "");
    setTileWrapContent(aiTileWrap, "");
    if (actualTileName) actualTileName.textContent = "";
    if (aiTileName) aiTileName.textContent = "";

    const roundKyotaku = snap.kyotaku ?? currentKyoku?.kyotaku ?? 0;

    // Calculate pure round-end tenpai fee (+3000 / -1000 etc.)
    // Note: Do NOT subtract the 1000 riichi stick deposit here. Per Majsoul rule and user request:
    // Display pure tenpai fee directly (+3000, not +2000), without "(立直-1k)".
    const pureDeltas = [0, 1, 2, 3].map((seat) => {
      let d = snap?.event?.deltas?.[seat];
      if (d === undefined || d === null) {
        d = currentKyoku?.score_deltas?.[seat] ?? 0;
        // If cumulative score_deltas had deducted the 1000 riichi stick, restore pure tenpai fee
        if (snap?.riichi?.[seat]) {
          d += 1000;
        }
      }
      return d;
    });

    const tenpaiSeats = [0, 1, 2, 3].filter((s) => pureDeltas[s] > 0);
    const notenSeats = [0, 1, 2, 3].filter((s) => pureDeltas[s] < 0);

    let tenpaiSummary = "荒牌流局 · 罚符结算";
    if (tenpaiSeats.length === 1) {
      tenpaiSummary = "1人听牌 · 罚符收付 (+3000 / -1000)";
    } else if (tenpaiSeats.length === 2) {
      tenpaiSummary = "2人听牌 · 罚符收付 (+1500 / -1500)";
    } else if (tenpaiSeats.length === 3) {
      tenpaiSummary = "3人听牌 · 罚符收付 (+1000 / -3000)";
    } else if (tenpaiSeats.length === 4) {
      tenpaiSummary = "全员听牌 · 无罚符收支 (0点)";
    } else if (tenpaiSeats.length === 0) {
      tenpaiSummary = "全员不听 · 无罚符收支 (0点)";
    }

    if (settWrap) {
      settWrap.style.display = "flex";
      settWrap.innerHTML = `
        <div class="kd-settlement-banner is-ryuukyoku">
          <div class="kd-settlement-banner-top">
            <div class="kd-settlement-ryuukyoku-icon">🀄</div>
            <div class="kd-settlement-banner-info">
              <div class="kd-settlement-winner-title">
                <span>荒牌流局</span>
                <span class="kd-settlement-ryuukyoku-subtag">${tenpaiSummary}</span>
              </div>
              <div class="kd-settlement-meta-row" style="color:rgba(235,235,245,0.7); font-size:12px; margin-top:2px;">
                ${tenpaiSeats.length > 0 ? `听牌玩家：<strong style="color:#ffffff;">${tenpaiSeats.map((s) => escapeHtml(names[s])).join("、")}</strong>` : "无玩家听牌 (全员不听)"}
              </div>
            </div>
          </div>
          ${roundKyotaku > 0 ? `
            <div class="kd-settlement-kyotaku-strip">
              <div class="kd-kyotaku-pill">
                <span class="riichi-stick-mini"></span>
                <span class="kd-kyotaku-tag">场供留存</span>
              </div>
              <div class="kd-kyotaku-pts">${roundKyotaku * 1000} 点</div>
              <div class="kd-kyotaku-desc">结转至下局</div>
            </div>
          ` : ""}
        </div>
        <div class="kd-settlement-delta-grid">
          ${[0, 1, 2, 3].map((seat) => {
            const delta = pureDeltas[seat];
            const isTenpai = delta > 0;
            const isNoten = delta < 0;
            const deltaClass = isTenpai ? "pos" : isNoten ? "neg" : "zero";
            const deltaFormatted = delta > 0 ? `+${delta}` : `${delta}`;
            const pName = names[seat] || `P${seat + 1}`;
            const cellClass = isTenpai ? "winner" : isNoten ? "loser" : "";
            const statusBadge = isTenpai
              ? `<span class="kd-ryuukyoku-status-tag tenpai">听牌</span>`
              : (isNoten ? `<span class="kd-ryuukyoku-status-tag noten">不听</span>` : `<span class="kd-ryuukyoku-status-tag neutral">${tenpaiSeats.length === 4 ? "听牌" : "不听"}</span>`);
            return `
              <div class="kd-settlement-delta-cell ${cellClass}">
                <div class="kd-settlement-player-name">
                  ${escapeHtml(pName)}
                  ${statusBadge}
                </div>
                <div class="kd-settlement-score-diff ${deltaClass}">${deltaFormatted}</div>
              </div>
            `;
          }).join("")}
        </div>
      `;
    }

    // Hide Candidates Table completely on Round End
    if (candTitle) candTitle.style.display = "none";
    if (candTable) candTable.style.display = "none";
    if (tableBody) tableBody.innerHTML = "";
    return;
  }

  card.classList.remove("is-idle");

  // Restore decision comparison blocks & candidates section for normal steps
  if (compWrap) compWrap.style.display = "flex";
  if (settWrap) settWrap.style.display = "none";
  if (candTitle) candTitle.style.display = "none";
  if (candTable) candTable.style.display = "";
  if (candTableHead) candTableHead.style.display = "";
  if (actualLabel) actualLabel.textContent = "玩家";
  if (aiLabel) aiLabel.textContent = "模型";

  // Non-decision steps: strictly keep right decision card empty per user requirement
  if (!dec || !isExactStep) {
    card.classList.add("is-idle");
    if (headerEl) headerEl.style.display = "none";
    if (turnEl) turnEl.textContent = "";
    if (badgeEl) {
      badgeEl.textContent = "";
      badgeEl.className = "kd-dec-verdict is-empty";
    }
    if (compWrap) compWrap.style.display = "none";
    if (settWrap) settWrap.style.display = "none";
    if (candTitle) candTitle.style.display = "none";
    if (candTable) candTable.style.display = "none";
    if (candTableHead) candTableHead.style.display = "none";
    if (tableBody) tableBody.innerHTML = "";
    setTileWrapContent(actualTileWrap, "");
    setTileWrapContent(aiTileWrap, "");
    if (actualTileName) actualTileName.textContent = "";
    if (aiTileName) aiTileName.textContent = "";
    if (actualProbBadge) actualProbBadge.textContent = "";
    if (aiProbBadge) aiProbBadge.textContent = "";
    if (aiShantenBadge) aiShantenBadge.textContent = "";
    if (aiUkeireBadge) aiUkeireBadge.textContent = "";
    if (actualVerdictText) {
      actualVerdictText.textContent = "";
      actualVerdictText.className = "kd-tag-verdict";
    }
    if (actualBlock) actualBlock.classList.remove("is-blunder");
    return;
  }

  card.classList.remove("is-idle");
  if (headerEl) headerEl.style.display = "flex";

  const isMatch = dec.is_equal;
  const isCallDec = dec.decision_type === "call_response";
  const isReachDec = dec.decision_type === "reach";

  if (turnEl) turnEl.textContent = "";

  if (badgeEl) {
    if (isMatch) {
      badgeEl.textContent = "模型一致";
      badgeEl.className = "kd-dec-verdict best";
    } else if (dec.verdict === "blunder") {
      badgeEl.textContent = "恶手";
      badgeEl.className = "kd-dec-verdict blunder";
    } else {
      badgeEl.textContent = dec.verdict_cn || "疑问手";
      badgeEl.className = "kd-dec-verdict question";
    }
  }

  // 1. Actual Choice (On Top)
  let actPai = dec.actual?.pai;
  if (!actPai && (dec.actual?.type === "reach" || dec.decision_type === "reach")) {
    const startIdx = dec.step_idx;
    for (let s = startIdx; s < Math.min(startIdx + 5, currentKyoku?.steps?.length || 0); s++) {
      const ev = currentKyoku?.steps?.[s]?.event;
      if (ev?.actor === targetActor && ev?.type === "dahai") {
        actPai = ev.pai;
        break;
      }
    }
  }
  const isActTile = actPai && actPai !== "none" && actPai !== "pass";
  setTileWrapContent(actualTileWrap, renderDecisionTileOrBadge(actPai, dec.actual, dec.actual_cn));

  if (actualTileName) {
    if (isActTile && (!dec.actual || dec.actual.type === "dahai")) {
      actualTileName.textContent = "";
      actualTileName.style.display = "none";
    } else {
      actualTileName.style.display = "block";
      const actType = (dec.actual?.type || "").toLowerCase();
      let actText = dec.actual_cn || "";
      if (
        actType === "pass" ||
        actType === "none" ||
        !actText ||
        actText.includes("跳过") ||
        actText.toLowerCase().includes("pass") ||
        actText.includes("不鸣") ||
        actText.includes("过")
      ) {
        actText = "PASS";
      } else if (actType.includes("chi") || actText.includes("吃")) {
        actText = "Chi";
      } else if (actType.includes("pon") || actText.includes("碰")) {
        actText = "Pon";
      } else if (actType.includes("kan") || actText.includes("杠")) {
        actText = "Kan";
      } else if (actType.includes("ron") || (isCallDec && (actType.includes("hora") || actText.includes("和")))) {
        actText = "Ron";
      } else if (actType.includes("reach") || actType.includes("riichi") || actText.includes("立直")) {
        actText = actPai ? `立直 ${tileCn(actPai)}` : "Riichi";
      } else {
        actText = actText.replace(/和牌/g, isCallDec ? "Ron" : "自摸");
      }
      actualTileName.textContent = actText;
    }
  }
  if (actualProbBadge) {
    actualProbBadge.textContent = "";
  }
  if (actualVerdictText) {
    if (isMatch) {
      actualVerdictText.textContent = "模型一致";
      actualVerdictText.className = "kd-tag-verdict best";
    } else if (dec.verdict === "blunder") {
      actualVerdictText.textContent = "恶手";
      actualVerdictText.className = "kd-tag-verdict blunder";
    } else {
      actualVerdictText.textContent = dec.verdict_cn || "疑问手";
      actualVerdictText.className = "kd-tag-verdict question";
    }
  }
  if (actualBlock) {
    if (isExactStep && dec.verdict === "blunder") actualBlock.classList.add("is-blunder");
    else actualBlock.classList.remove("is-blunder");
  }

  // 2. AI Recommended Choice (On Bottom)
  let expPai = dec.expected?.pai || dec.candidates?.[0]?.pai;
  if (!expPai && (dec.expected?.type === "reach" || dec.candidates?.[0]?.action?.type === "reach")) {
    expPai = dec.candidates?.[0]?.pai || dec.candidates?.[1]?.pai;
    if (!expPai) {
      const startIdx = dec.step_idx;
      for (let s = startIdx; s < Math.min(startIdx + 5, currentKyoku?.steps?.length || 0); s++) {
        const ev = currentKyoku?.steps?.[s]?.event;
        if (ev?.actor === targetActor && ev?.type === "dahai") {
          expPai = ev.pai;
          break;
        }
      }
    }
  }
  const isExpTile = expPai && expPai !== "none" && expPai !== "pass";
  const bestCand = dec.candidates?.[0];
  setTileWrapContent(aiTileWrap, renderDecisionTileOrBadge(expPai, dec.expected, dec.expected_cn));

  if (aiTileName) {
    if (isExpTile && (!dec.expected || dec.expected.type === "dahai")) {
      aiTileName.textContent = "";
      aiTileName.style.display = "none";
    } else {
      aiTileName.style.display = "block";
      const expType = (dec.expected?.type || "").toLowerCase();
      let expText = dec.expected_cn || "";
      if (
        expType === "pass" ||
        expType === "none" ||
        !expText ||
        expText.includes("跳过") ||
        expText.toLowerCase().includes("pass") ||
        expText.includes("不鸣") ||
        expText.includes("过")
      ) {
        expText = "PASS";
      } else if (expType.includes("chi") || expText.includes("吃")) {
        expText = "Chi";
      } else if (expType.includes("pon") || expText.includes("碰")) {
        expText = "Pon";
      } else if (expType.includes("kan") || expText.includes("杠")) {
        expText = "Kan";
      } else if (expType.includes("ron") || (isCallDec && (expType.includes("hora") || expText.includes("和")))) {
        expText = "Ron";
      } else if (expType.includes("reach") || expType.includes("riichi") || expText.includes("立直")) {
        expText = expPai ? `立直 ${tileCn(expPai)}` : "Riichi";
      } else {
        expText = expText.replace(/和牌/g, isCallDec ? "Ron" : "自摸");
      }
      aiTileName.textContent = expText;
    }
  }
  if (aiProbBadge) {
    aiProbBadge.textContent = "";
  }
  if (aiShantenBadge) {
    const sVal = bestCand?.shanten_after ?? bestCand?.shanten ?? dec.shanten;
    aiShantenBadge.textContent = shantenText(sVal, isCallDec ? "荣和" : "自摸");
  }
  if (aiUkeireBadge) {
    const uk = bestCand?.ukeire ?? dec.expected?.ukeire;
    aiUkeireBadge.textContent = uk !== undefined && uk !== null ? `进张 ${uk} 张` : "进张 --";
  }

  // 3. Top Candidates Table with Pure Graphic & Expandable Ukeire Detail Drawer
  const cands = (dec.candidates || []).slice(0, 5);

  if (tableBody) {
    const rowsHtml = cands
      .map((c, i) => {
        const act = c.action || {};
        const rawType = (act.type || "").toLowerCase();
        const cn = (c.action_cn || c.short_cn || "").toLowerCase();

        let actionType = "dahai";
        let actionLabel = "切";
        let badgeClass = "action-dahai";

        if (rawType.includes("chi") || cn.includes("吃")) {
          actionType = "chi";
          actionLabel = "Chi";
          badgeClass = "action-chi";
        } else if (rawType.includes("pon") || cn.includes("碰")) {
          actionType = "pon";
          actionLabel = "Pon";
          badgeClass = "action-pon";
        } else if (rawType.includes("kan") || cn.includes("杠")) {
          actionType = "kan";
          actionLabel = "Kan";
          badgeClass = "action-kan";
        } else if (rawType.includes("ron") || cn.includes("荣") || (isCallDec && (rawType.includes("hora") || cn.includes("和")))) {
          actionType = "ron";
          actionLabel = "Ron";
          badgeClass = "action-hora";
        } else if (rawType.includes("tsumo") || cn.includes("自摸") || (!isCallDec && (rawType.includes("hora") || cn.includes("和")))) {
          actionType = "tsumo";
          actionLabel = "自摸";
          badgeClass = "action-hora";
        } else if (rawType.includes("reach") || rawType.includes("riichi") || cn.includes("立直")) {
          actionType = "riichi";
          actionLabel = "Riichi";
          badgeClass = "action-riichi";
        } else if (rawType.includes("none") || rawType.includes("pass") || cn.includes("pass") || cn.includes("跳过") || cn.includes("过") || cn.includes("不鸣")) {
          actionType = "pass";
          actionLabel = "PASS";
          badgeClass = "action-pass";
        } else if (isCallDec) {
          actionType = "pass";
          actionLabel = "PASS";
          badgeClass = "action-pass";
        } else {
          actionType = "dahai";
          actionLabel = "切";
          badgeClass = "action-dahai";
        }

        // 牌面列
        let tileDisplay = "";
        const mainPai = c.pai || act.pai;
        const consumed = act.consumed || [];

        if (actionType === "chi" || actionType === "pon" || actionType === "kan") {
          let comboTiles = [];
          if (consumed.length > 0) {
            comboTiles = [...consumed].filter(Boolean);
          } else if (mainPai) {
            if (actionType === "pon") comboTiles = [mainPai, mainPai];
            else if (actionType === "kan") comboTiles = [mainPai, mainPai, mainPai];
            else comboTiles = [mainPai];
          }
          comboTiles.sort((a, b) => tileSortOrder(a) - tileSortOrder(b));
          if (comboTiles.length > 0) {
            tileDisplay = `<div class="kd-cand-tile-combo">${comboTiles.map((t) => `<img src="/tiles/${encodeURIComponent(t)}.svg" class="kd-cand-tile-img" title="${tileCn(t)}">`).join("")}</div>`;
          } else {
            tileDisplay = `<span class="kd-cand-pass-text">${escapeHtml(actionLabel)}</span>`;
          }
        } else if (actionType === "pass") {
          tileDisplay = `<span class="kd-cand-pass-text">--</span>`;
        } else if (actionType === "riichi") {
          let reachTile = mainPai || dec.actual?.pai;
          if (!reachTile && dec.actual?.type === "reach") {
            const startIdx = dec.step_idx;
            for (let s = startIdx; s < Math.min(startIdx + 5, currentKyoku?.steps?.length || 0); s++) {
              const ev = currentKyoku?.steps?.[s]?.event;
              if (ev?.actor === targetActor && ev?.type === "dahai") {
                reachTile = ev.pai;
                break;
              }
            }
          }
          if (!reachTile && dec.candidates?.[0]?.pai) {
            reachTile = dec.candidates[0].pai;
          }
          if (reachTile && reachTile !== "none" && reachTile !== "pass" && !reachTile.startsWith("none")) {
            tileDisplay = `<img src="/tiles/${encodeURIComponent(reachTile)}.svg" class="kd-cand-tile-img" title="立直: ${tileCn(reachTile)}">`;
          } else {
            tileDisplay = `<span class="kd-cand-pass-text" style="color:#ff453a; font-weight:700;">立直</span>`;
          }
        } else if (actionType === "ron" || actionType === "tsumo") {
          const agariTile = mainPai || dec.context_tile || dec.actual?.pai || dec.expected?.pai;
          if (agariTile && agariTile !== "none" && agariTile !== "pass") {
            tileDisplay = `<img src="/tiles/${encodeURIComponent(agariTile)}.svg" class="kd-cand-tile-img agari-tile" title="${actionType === "ron" ? "Ron" : "自摸"}: ${tileCn(agariTile)}">`;
          } else {
            tileDisplay = `<span class="kd-cand-pass-text" style="color:#30d158; font-weight:700;">${actionType === "ron" ? "Ron" : "自摸"}</span>`;
          }
        } else {
          if (mainPai && mainPai !== "none" && mainPai !== "pass" && !mainPai.startsWith("none")) {
            tileDisplay = `<img src="/tiles/${encodeURIComponent(mainPai)}.svg" class="kd-cand-tile-img" title="${tileCn(mainPai)}">`;
          } else {
            tileDisplay = `<span class="kd-cand-pass-text">--</span>`;
          }
        }

        // 判定是否是实际出牌/动作
        let isAct = false;
        if (dec.actual) {
          if (actionType === "pass" && (dec.actual.type === "none" || dec.actual.type === "pass")) {
            isAct = true;
          } else if (act.type && dec.actual.type === act.type) {
            if (mainPai && dec.actual.pai) {
              isAct = mainPai === dec.actual.pai;
            } else {
              isAct = true;
            }
          } else if (mainPai && dec.actual.pai && mainPai === dec.actual.pai) {
            isAct = true;
          }
          if (!isAct && c.action_cn && dec.actual_cn) {
            isAct = c.action_cn === dec.actual_cn;
          }
        }

        const prob = fmtPct(c.prob);
        const isAi = c.rank === 1;
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
            <td colspan="5" style="box-sizing:border-box; width:100%; padding:4px 8px 8px; border-bottom:1px solid rgba(255,255,255,0.06); background:rgba(0,0,0,0.22);" onclick="event.stopPropagation()">
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
            <td><span class="kd-cand-action ${badgeClass}">${escapeHtml(actionLabel)}</span></td>
            <td style="text-align:center;">${tileDisplay}</td>
            <td style="font-weight:bold; color:${isAi ? "#30d158" : "var(--color-text-bright)"}">${prob}</td>
            <td><span class="kd-tag-shanten" style="padding:1px 5px;">${shantenText(sVal, actionType === "ron" ? "Ron" : "自摸")}</span></td>
            <td>${uCell}</td>
          </tr>
          ${detailRow}
        `;
      })
      .join("");

    tableBody.innerHTML = rowsHtml;
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
    const prevKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
    if (prevKyoku?.decisions?.length) {
      setStep(prevKyoku.decisions[prevKyoku.decisions.length - 1].step_idx);
    }
  } else {
    showToast("已是全场开局了", "info");
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
  // If there are no more decisions for Hero, advance to final round settlement step
  if (state.stepIdx < kyoku.steps.length - 1) {
    setStep(kyoku.steps.length - 1);
    return;
  }
  // Already at settlement or end of kyoku: Automatically jump to next kyoku!
  const maxIdx = (state.reviewData?.kyokus?.length || 1) - 1;
  if (state.kyokuIdx < maxIdx) {
    jumpNextKyoku();
  } else {
    showToast("已经是全场终局了", "info");
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
        <th style="text-align:left; min-width:180px;">自摸 / 荣和 / 结算</th>
        ${names.map((name, s) => {
          const isTarget = s === targetActor;
          const thClass = isTarget ? "active-pov-th" : "";
          const povBadge = isTarget ? " <span style='font-size:10px; color:#ffcc00;'>(我)</span>" : "";
          return `<th class="${thClass}" style="text-align:right;">P${s} ${escapeHtml(name)} (起${WIND_CN[s]})${povBadge}</th>`;
        }).join("")}
        <th style="width:85px; text-align:right;">当前增减</th>
        <th style="width:96px; text-align:center;" title="分子为与模型一致决策数，分母为有效决策总数">模型一致率</th>
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
    if ($("engineSelect")) $("engineSelect").value = "akagi";
    updateEngineStatusDisplay("akagi");
  });
  btnMortal?.addEventListener("click", () => {
    state.selectedModel = "mortal";
    btnMortal.classList.add("active");
    btnAkagi.classList.remove("active");
    if ($("engineSelect")) $("engineSelect").value = "mortal";
    updateEngineStatusDisplay("mortal");
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
    e.target.blur();
  });

  // Play/Pause Auto Step
  $("btnPlayPause")?.addEventListener("click", () => togglePlay());

  // Playback Speed Selector
  const speedSel = $("playSpeedSelect");
  if (speedSel) {
    speedSel.value = String(state.playSpeed || "1");
    speedSel.addEventListener("change", (e) => {
      setPlaySpeed(e.target.value);
    });
  }

  // Toggle Hands Visibility
  $("btnToggleHands")?.addEventListener("click", toggleHandsVisibility);

  // Toggle Hide/Show Player ID
  $("btnToggleHideId")?.addEventListener("click", toggleHidePlayerId);

  // Toggle Audio Sound
  $("btnToggleAudio")?.addEventListener("click", toggleSound);

  // Global robust delegation for clicking anywhere on opponent hands / tiles / back tiles:
  document.addEventListener("click", (e) => {
    if (e.target.closest("button, a, select, input")) return;
    // 如果点击的是自家（p0/底端）手牌或相关区域，坚决不触发别家明牌/暗牌切换！
    if (e.target.closest(".grid-hand-p0-container, #hand0, .hand-closed-p0, .grid-hand-p0")) return;
    // 只有点击其他三家（p1, p2, p3）的手牌区域时，才执行 toggleHandsVisibility()
    const otherHandTarget = e.target.closest(".grid-hand-p1, .grid-hand-p2, .grid-hand-p3, #hand1, #hand2, #hand3, .hand-closed-p1, .hand-closed-p2, .hand-closed-p3");
    if (otherHandTarget) {
      toggleHandsVisibility();
    }
  });

  // Step timeline slider: Prevent focus trap & prevent browser native range decrement on ArrowDown
  const sliderEl = $("stepSlider");
  if (sliderEl) {
    sliderEl.addEventListener("input", (e) => {
      if (state.isPlaying) togglePlay(false);
      scheduleStep(parseInt(e.target.value, 10));
    });
    sliderEl.addEventListener("change", (e) => {
      e.target.blur();
    });
    sliderEl.addEventListener("mouseup", (e) => {
      e.target.blur();
    });
    sliderEl.addEventListener("touchend", (e) => {
      e.target.blur();
    });
    sliderEl.addEventListener("pointerup", (e) => {
      e.target.blur();
    });
    sliderEl.addEventListener("keydown", (e) => {
      if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", " ", "Space"].includes(e.key)) {
        e.preventDefault();
        sliderEl.blur();
      }
    });
  }

  // Dedicated Error Box actions
  $("btnRetryAnalysis")?.addEventListener("click", () => {
    if (currentAnalysisRetryCb) {
      const cb = currentAnalysisRetryCb;
      currentAnalysisRetryCb = null;
      cb();
    } else {
      setLoading(false);
    }
  });

  $("btnCloseAnalysisError")?.addEventListener("click", () => {
    setLoading(false);
    currentAnalysisRetryCb = null;
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
  bindNavClick(["btnPrevKyoku"], jumpPrevKyoku);
  bindNavClick(["btnNextKyoku"], jumpNextKyoku);
  bindNavClick(["btnPrevBlunder", "btnPrevBlunderTop"], jumpPrevBlunder);
  bindNavClick(["btnNextBlunder", "btnNextBlunderTop"], jumpNextBlunder);
  bindNavClick(["btnPrevDec", "btnPrevDecTop"], jumpPrevDecision);
  bindNavClick(["btnNextDec", "btnNextDecTop"], jumpNextDecision);
  bindNavClick(["btnPrevStep", "btnPrevStepTop"], () => stepDelta(-1));
  bindNavClick(["btnNextStep", "btnNextStepTop"], () => stepDelta(1));

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
    updateEngineStatusDisplay(model);
    try {
      await apiFetch("/api/engine/config", {
        method: "POST",
        body: JSON.stringify({ selected_model: model }),
      });
    } catch (e) {
      console.warn("Failed to sync engine config with backend:", e);
    }
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
    const activeEl = document.activeElement;
    if (activeEl && (activeEl.tagName === "TEXTAREA" || (activeEl.tagName === "INPUT" && activeEl.type !== "range" && activeEl.type !== "button"))) {
      return;
    }
    if (document.querySelector(".modal:not(.hidden)")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      togglePlay();
      return;
    }

    const actions = {
      ArrowLeft: () => stepDeltaFast(-1),
      ArrowRight: () => stepDeltaFast(1),
      ArrowUp: jumpPrevDecision,
      ArrowDown: jumpNextDecision,
      PageUp: () => jumpPrevBlunderFast(),
      PageDown: () => jumpNextBlunderFast(),
      ",": () => jumpPrevBlunderFast(),
      ".": () => jumpNextBlunderFast(),
      Home: () => {
        cancelScheduledStep();
        setStep(0);
      },
      End: () => {
        cancelScheduledStep();
        const k = state.reviewData?.kyokus?.[state.kyokuIdx];
        if (k) setStep(k.steps.length - 1);
      },
      "[": () => {
        cancelScheduledStep();
        jumpPrevKyoku();
      },
      "]": () => {
        cancelScheduledStep();
        jumpNextKyoku();
      },
      h: toggleHandsVisibility,
      H: toggleHandsVisibility,
      m: toggleSound,
      M: toggleSound,
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
  document.documentElement.removeAttribute("data-theme");
  try {
    localStorage.removeItem("majsoul_theme_pref");
    localStorage.removeItem("majsoul_back_pref");
  } catch (_) {}
  applyHidePlayerId();
  updateSoundButton();
  const initSpeedSel = $("playSpeedSelect");
  if (initSpeedSel) initSpeedSel.value = String(state.playSpeed || "1");
  document.addEventListener("pointerdown", () => SoundFX.init(), { once: true });
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
