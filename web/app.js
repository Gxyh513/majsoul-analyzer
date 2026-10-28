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

// Audio effects removed per UI simplification
function playTileDiscardSound() {}
function toggleSound() {}
function updateSoundButton() {}

const $ = (id) => document.getElementById(id);

function getPlayerDisplayName(seat, kyoku = null) {
  const s = seat ?? 0;
  const currentKyoku = kyoku || state.reviewData?.kyokus?.[state.kyokuIdx];
  const wind = currentKyoku ? handWind(s, currentKyoku) : WIND_CN[s % 4];
  const isTarget = s === (state.targetActor ?? 0);
  return isTarget ? `${wind}家 (自家)` : `${wind}家`;
}

function sanitizeSummaryText(text) {
  if (!text) return "";
  let s = String(text);
  if (state.reviewData?.names) {
    const currentKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
    state.reviewData.names.forEach((origName, seat) => {
      if (origName && origName.trim()) {
        const wind = currentKyoku ? handWind(seat, currentKyoku) : WIND_CN[seat % 4];
        const isTarget = seat === (state.targetActor ?? 0);
        const replacement = isTarget ? `${wind}家 (自家)` : `${wind}家`;
        s = s.split(origName).join(replacement);
      }
    });
  }
  return s;
}

function toggleHidePlayerId() {
  // Permanent neutral seat identity mode
}

function applyHidePlayerId() {
  document.body.classList.add("hide-player-ids");
}

function getTileBackSrc() {
  return "/tiles/back-navy.svg?v=canglan";
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

function setLoading(visible, title = "AI 牌谱智能检讨", sub = null) {
  const overlay = $("loadingOverlay");
  if (!overlay) return;
  if (visible) {
    const normalState = $("analysisNormalState");
    const errorBox = $("analysisErrorBox");
    if (normalState) normalState.classList.remove("hidden");
    if (errorBox) errorBox.classList.add("hidden");
    if ($("loadingTitle")) $("loadingTitle").textContent = title;

    let effectiveSub = sub;
    if (!effectiveSub) {
      effectiveSub = "Mortal-V4-582500 神经网络正在检讨每一手决策";
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

  if (errMsg && (errMsg.includes("MAJSOUL_API_TOKEN") || errMsg.includes("401"))) {
    if (errorDetail) {
      errorDetail.innerHTML = `
        <div style="margin-bottom: 10px; font-weight: 500; color: #ffffff;">本服务器已启用远程访问口令保护，请输入口令：</div>
        <div style="display: flex; gap: 8px; justify-content: center; max-width: 400px; margin: 0 auto 10px;">
          <input id="promptApiTokenInput" type="password" placeholder="请输入 MAJSOUL_API_TOKEN" class="kd-landing-input" style="padding: 8px 12px; font-size: 13px; text-align: center; border-radius: 8px;" value="${escapeHtml(getApiToken())}" />
          <button id="btnSavePromptToken" type="button" class="kd-retry-btn" style="white-space: nowrap; padding: 8px 14px; border-radius: 8px;">保存并继续</button>
        </div>
        <div style="font-size: 11.5px; color: rgba(235, 235, 245, 0.45);">口令安全保存在当前浏览器本地，后续分析将自动携带。</div>
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

// A remote API token is kept only for this browser tab. It can be supplied
// as #api_token=... by a private launcher, then removed from the address bar.
function getApiToken() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("api_token");
  const backend = getBackendUrl();
  const origin = backend ? new URL(backend.startsWith("http") ? backend : `http://${backend}`).origin : window.location.origin;
  const key = `majsoul_api_token:${origin}`;
  if (token) {
    sessionStorage.setItem(key, token.trim());
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(key, token.trim());
    }
    fragment.delete("api_token");
    const url = new URL(window.location.href);
    url.hash = fragment.toString();
    window.history.replaceState(null, "", url.toString());
  }
  const sessionVal = sessionStorage.getItem(key);
  const localVal = typeof localStorage !== "undefined" ? localStorage.getItem(key) : null;
  return sessionVal || localVal || "";
}

function setApiToken(token) {
  const backend = getBackendUrl();
  const origin = backend ? new URL(backend.startsWith("http") ? backend : `http://${backend}`).origin : window.location.origin;
  const key = `majsoul_api_token:${origin}`;
  if (token && token.trim()) {
    sessionStorage.setItem(key, token.trim());
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(key, token.trim());
    }
  } else {
    sessionStorage.removeItem(key);
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(key);
    }
  }
}

function apiAuthHeaders() {
  const token = getApiToken();
  if (!token) return {};
  const backend = getBackendUrl();
  // ModelScope *.ms.show gateway intercepts standard Authorization header for its own SDK tokens
  if (backend.includes("ms.show") || window.location.hostname.includes("ms.show")) {
    return {};
  }
  return { Authorization: `Bearer ${token}` };
}

async function fetchReviewStream(postBody, onProgress) {
  const url = buildApiUrl(getBackendUrl(), "/api/analyze/stream");
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...apiAuthHeaders(),
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
    const apiToken = getApiToken();
    if (apiToken && !full.searchParams.has("api_token") && path.startsWith("/api/") && path !== "/api/status") {
      full.searchParams.set("api_token", apiToken);
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

  const headers = {
    Accept: "application/json",
    ...apiAuthHeaders(),
    ...(options.headers || {}),
  };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(url, {
    mode: "cors",
    ...options,
    headers,
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

function updateEngineStatusDisplay(modelId, device, activeEngine, hasWeights) {
  const dev = device || state.serverStatus?.engine?.device || "CPU";
  const eng = state.serverStatus?.engine || {};
  const actualActive = activeEngine !== undefined ? activeEngine : (eng.active_engine || (eng.has_mortal_weights ? "mortal" : "unavailable"));
  const weightsAvailable = hasWeights !== undefined ? hasWeights : Boolean(eng.has_mortal_weights);

  const targetModel = (modelId || state.selectedModel || eng.selected_model || "akagi").toLowerCase();
  let friendlyName = "";

  if (targetModel === "builtin" || actualActive === "builtin" || (!weightsAvailable && targetModel !== "custom")) {
    friendlyName = "Mortal-V4-582500（不可用）";
  } else if (targetModel.includes("akagi") || targetModel.includes("582500") || targetModel.includes("v4")) {
    friendlyName = "Mortal-V4-582500";
  } else if (targetModel.includes("mortal") || targetModel.includes("v3")) {
    friendlyName = "Mortal (v3)";
  } else {
    const available = eng.available_models || [];
    const found = available.find((m) => m.id && m.id.toLowerCase() === targetModel);
    if (found && found.name) {
      friendlyName = formatModelDisplayName(found.name).split("(")[0].trim() || found.name;
    } else {
      friendlyName = modelId || state.selectedModel || "Mortal";
    }
  }

  const btn = $("engineStatusText");
  if (btn) {
    btn.textContent = `${friendlyName} · ${dev}`;
  }
  const landingDev = $("landingModelDevice");
  if (landingDev) {
    landingDev.textContent = `${dev} 就绪`;
  }
}

function updateTopbarStatus(st) {
  const eng = st.engine || {};
  const hasWeights = Boolean(eng.has_mortal_weights);
  const activeEngine = eng.active_engine || (hasWeights ? "mortal" : "unavailable");
  updateEngineStatusDisplay(state.selectedModel || eng.selected_model, eng.device, activeEngine, hasWeights);

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
    const modelName = formatModelDisplayName(eng.model_name || "Mortal-V4-582500");
    const weightFile = eng.model_path ? eng.model_path.split(/[\\/]/).pop() : "mortal_582500.pth";
    const statusNotice = (!hasWeights && (state.selectedModel || eng.selected_model) !== "builtin")
      ? `<div style="color: #ff9f0a; margin-top: 8px; font-size: 12px; padding: 6px 10px; background: rgba(255,159,10,0.12); border-radius: 6px;">⚠️ 未检测到有效神经网络权重文件，无法分析，不会回退到其他引擎。</div>`
      : "";
    detail.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:6px;">
        <div style="display:flex; justify-content:space-between;"><span style="color:rgba(235,235,245,0.6);">当前运行模型：</span><strong style="color:#ffffff;">${escapeHtml(modelName)}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="color:rgba(235,235,245,0.6);">当前计算硬件：</span><span style="color:#30d158; font-weight:600;">${escapeHtml(eng.device || "CPU")}</span></div>
        <div style="display:flex; justify-content:space-between;"><span style="color:rgba(235,235,245,0.6);">生效权重文件：</span><code style="color:rgba(235,235,245,0.85); font-size:11.5px;">${escapeHtml(weightFile)}</code></div>
        ${statusNotice}
      </div>
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

const REVIEW_SCHEMA = "mortal-582500-only-v2";

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
        if (!res) {
          const p = apiFetch("/api/analyze/paipu", {
            method: "POST",
            body: JSON.stringify({ paipu: gameId, target_actor: seat, engine: model }),
          });
          inFlightRequests[cacheKey] = p;
          res = await p;
        }
        if (res) {
          requireTrustedReview(res);
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
  try {
    await requireModelStatus();
  } catch (err) {
    setLoading(true);
    showAnalysisError(`分析失败：${err.message}`, () => startAnalysisFromInput(rawInput, model, seat));
    return;
  }

  // 1. 检查内存缓存
  const cacheKey = seat !== null && seat !== undefined ? `${paipuId}_${model}_${seat}` : null;
  if (!isJsonInput && cacheKey && state.perspectiveCache && isTrustedReview(state.perspectiveCache[cacheKey])) {
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
      if (isTrustedReview(idbCached)) {
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

  const loadingSub = "正在使用 Mortal-V4-582500 神经网络检讨每一手决策";
  setLoading(true, "正在分析牌谱...", loadingSub);

  try {
    let res;

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

    requireTrustedReview(res);
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

    if (res.engine) {
      const isResBuiltin = res.engine.type === "builtin" || res.engine.id === "builtin";
      updateEngineStatusDisplay(
        res.engine.id,
        res.engine.device,
        isResBuiltin ? "builtin" : "mortal",
        !isResBuiltin
      );
    }

    preloadOtherPerspectives(state.gameId, state.selectedModel, state.targetActor);
    setLoading(false);
  } catch (err) {
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
  try {
    await requireModelStatus();
  } catch (err) {
    showToast(`切换视角失败：${err.message}`, "error");
    return;
  }

  // 1. 若已命中缓存：直接同步切换，0 毫秒瞬间秒切！
  if (state.perspectiveCache && isTrustedReview(state.perspectiveCache[cacheKey])) {
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
    if (isTrustedReview(idbCached)) {
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
  const seatName = getPlayerDisplayName(newSeat);
  setLoading(true, `切换到「${seatName}」视角...`);
  try {
    let res;

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
    requireTrustedReview(res);
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
      <div class="kd-badge-item">一致率: <strong>${fmtPct(sum.match_rate)}</strong></div>
      <div class="kd-badge-item">恶手: <strong style="color:#ff453a;">${sum.blunders || 0}</strong></div>
      <div class="kd-badge-item">Rating: <strong style="color:#ffcc00;">${(sum.rating ?? 0).toFixed(1)}</strong></div>
    `;
  }
}

function renderSeatTabs() {
  const res = state.reviewData;
  if (!res) return;
  const currentKyoku = res.kyokus?.[state.kyokuIdx];
  const oya = currentKyoku?.oya ?? 0;
  const seatTabs = $("seatTabs");
  if (!seatTabs) return;
  const seats = [0, 1, 2, 3];
  seatTabs.innerHTML = seats
    .map((seat) => {
      const wind = WIND_CN[(seat - oya + 4) % 4];
      const isTarget = seat === res.target_actor;
      const active = isTarget ? "active" : "";
      const label = isTarget ? `${wind}·自家` : `${wind}家`;
      return `<button class="kd-tab ${active}" data-seat="${seat}" role="tab" title="${label}视角">
        <span class="kd-seat-label">${label}</span>
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
    const w = currentKyoku ? handWind(seat, currentKyoku) : WIND_CN[seat % 4];
    const isHero = seat === state.targetActor;
    return `${w}家${isHero ? " (自家)" : ""}`;
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


  const totalSteps = Math.max(0, kyoku.steps.length - 1);
  const sliderEl = $("stepSlider");
  if (sliderEl) {
    sliderEl.max = totalSteps;
    sliderEl.value = clamped;
    const pct = totalSteps > 0 ? ((clamped / totalSteps) * 100).toFixed(1) : 0;
    sliderEl.style.setProperty("--slider-pct", `${pct}%`);
  }
  const curNum = $("stepCurrentNum");
  const totNum = $("stepTotalNum");
  if (curNum) curNum.textContent = clamped;
  if (totNum) totNum.textContent = totalSteps;
  const counterText = $("stepCounterText");
  if (counterText && !curNum) counterText.textContent = `${clamped} / ${totalSteps}`;

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

  const roundScores = kyoku.init_scores || kyoku.steps?.[0]?.scores || [25000, 25000, 25000, 25000];
  const giP0 = $("giP0");
  if (giP0) giP0.innerHTML = `<span class="gi-wind">${handWind(hero, kyoku)}</span><span class="gi-score">${roundScores[hero] ?? 25000}</span>`;
  const giP1 = $("giP1");
  if (giP1) giP1.innerHTML = `<span class="gi-wind">${handWind(p1, kyoku)}</span><span class="gi-score">${roundScores[p1] ?? 25000}</span>`;
  const giP2 = $("giP2");
  if (giP2) giP2.innerHTML = `<span class="gi-wind">${handWind(p2, kyoku)}</span><span class="gi-score">${roundScores[p2] ?? 25000}</span>`;
  const giP3 = $("giP3");
  if (giP3) giP3.innerHTML = `<span class="gi-wind">${handWind(p3, kyoku)}</span><span class="gi-score">${roundScores[p3] ?? 25000}</span>`;

  // Rivers
  renderPlayerRiver("Bottom", hero, snap, isRon, targetActor);
  renderPlayerRiver("Right", p1, snap, isRon, targetActor);
  renderPlayerRiver("Top", p2, snap, isRon, targetActor);
  renderPlayerRiver("Left", p3, snap, isRon, targetActor);

  // Hands
  const nextSnap = kyoku.steps?.[state.stepIdx + 1] || null;
  renderHeroHand(hero, snap, activeDecision, agariTile, winActor, isRon, nextSnap);
  renderOpponentHand("Right", p1, snap, agariTile, winActor, isRon, nextSnap);
  renderOpponentHand("Top", p2, snap, agariTile, winActor, isRon, nextSnap);
  renderOpponentHand("Left", p3, snap, agariTile, winActor, isRon, nextSnap);
}

function renderPlayerRiver(pos, seat, snap, isRon = false, targetActor = null) {
  const container = $(`river${pos}`);
  if (!container) return;
  const fullRiver = snap.rivers[seat] || [];
  const river = fullRiver;
  const isLastDiscardActor = snap.event?.type === "dahai" && snap.event?.actor === seat;
  const isRonTarget = isRon && seat === targetActor;

  // Include every tile and its flags: length plus the final tile is not unique
  // across rounds, seats or rewound replay states.
  const sig = JSON.stringify([state.gameId, state.kyokuIdx, state.targetActor, seat,
    river, isLastDiscardActor, isRonTarget]);
  if (container.dataset.riverSig === sig) {
    return;
  }
  container.dataset.riverSig = sig;

  // Strictly 3 rows max: Row 1 (0..6), Row 2 (6..12), Row 3 (12+)
  // Overflow discards remain in Row 3 without creating a 4th row!
  const rowSlices = [
    river.slice(0, 6),
    river.slice(6, 12),
    river.slice(12)
  ];

  let html = "";
  for (let rowIdx = 0; rowIdx < 3; rowIdx++) {
    const rowTiles = rowSlices[rowIdx];
    let rowHtml = `<div class="river-row river-row-${rowIdx + 1}">`;
    for (let colIdx = 0; colIdx < rowTiles.length; colIdx++) {
      const r = rowTiles[colIdx];
      const globalIdx = rowIdx === 0 ? colIdx : (rowIdx === 1 ? 6 + colIdx : 12 + colIdx);
      const isLast = isLastDiscardActor && globalIdx === river.length - 1;
      const isRonTile = isRonTarget && globalIdx === river.length - 1;
      const cls = ["tileImg"];
      if (r.tsumogiri) cls.push("tsumogiri");
      if (r.riichi) cls.push("rotate");
      if (isLast) cls.push("last-discard");
      if (isRonTile) cls.push("ron-claimed");

      let calledDesc = "";
      if (r.called) {
        let callerName = "";
        let callType = "鸣";
        const currentKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
        if (currentKyoku && currentKyoku.steps) {
          // Look for who called this tile in the round's events
          for (let si = 0; si < currentKyoku.steps.length; si++) {
            const ev = currentKyoku.steps[si]?.event;
            if (ev && (ev.type === "chi" || ev.type === "pon" || ev.type === "daiminkan")) {
              if (ev.target === seat && (ev.pai === r.pai || (ev.consumed && ev.consumed.includes(r.pai)))) {
                const heroSeat = state.targetActor ?? 0;
                const diff = (ev.actor - heroSeat + 4) % 4;
                callerName = ["自家", "下家", "对家", "上家"][diff];
                callType = ev.type === "chi" ? "吃" : (ev.type === "pon" ? "碰" : "大明杠");
                break;
              }
            }
          }
        }
        calledDesc = callerName ? ` · 被${callerName}${callType}` : " · 被鸣";
      }
      const tip = `${tileCn(r.pai)}（${r.tsumogiri ? "摸切" : "手切"}${r.riichi ? " · 立直宣言" : ""}${calledDesc}${isRonTile ? " · 放铳 (被荣和)" : ""}）`;
      const divCls = ["tileDiv"];
      if (r.riichi) divCls.push("has-rotate");
      if (isLast) divCls.push("has-last-discard");
      if (r.called) divCls.push("called");

      rowHtml += `<div class="${divCls.join(" ")}" title="${escapeHtml(tip)}"><img src="/tiles/${encodeURIComponent(r.pai)}.svg" class="${cls.join(" ")}"></div>`;
    }
    rowHtml += `</div>`;
    html += rowHtml;
  }

  container.innerHTML = html;
}

function renderHeroHand(seat, snap, activeDecision, agariTile = null, winActor = null, isRon = false, nextSnap = null) {
  const closedWrap = $("handBottomClosed");
  const callsWrap = $("handBottomCalls");
  const svgEl = $("discardBarsSvg");
  if (!closedWrap) return;

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  const allTiles = hand.map((pai) => ({ pai, isDrawn: false }));
  if (drawn) allTiles.push({ pai: drawn, isDrawn: true });

  const tileBars = activeDecision?.tile_bars || {};
  let actualPai = activeDecision?.actual?.pai || null;
  const isAnkanDec = activeDecision?.actual?.type === "ankan" || activeDecision?.decision_type === "ankan";
  const ankanTile = activeDecision?.actual?.consumed?.[0];

  // Highlight intended discard or ankan tiles in hand during draw/decision step or after call
  const highlightedIdxs = new Set();
  if (activeDecision) {
    if (isAnkanDec && ankanTile) {
      allTiles.forEach(({ pai }, idx) => {
        if (pai === ankanTile) highlightedIdxs.add(idx);
      });
    } else if (actualPai) {
      allTiles.forEach(({ pai }, idx) => {
        if (pai === actualPai && highlightedIdxs.size === 0) {
          highlightedIdxs.add(idx);
        }
      });
    }
  } else {
    // If hero is about to discard (on normal draw OR after calling pon/chi)
    const nextEvent = nextSnap?.event;
    if (nextEvent?.type === "dahai" && nextEvent?.actor === seat) {
      if (nextEvent.tsumogiri && drawn) {
        highlightedIdxs.add(hand.length);
      } else {
        for (let i = 0; i < hand.length; i++) {
          if (hand[i] === nextEvent.pai) {
            highlightedIdxs.add(i);
            break;
          }
        }
      }
    }
  }

  // 1. Render Closed Hand Tiles into DOM first
  let handHtml = "";
  hand.forEach((pai, idx) => {
    const isAct = highlightedIdxs.has(idx);
    handHtml += `<div class="tileDiv" data-pai="${escapeHtml(pai)}"><img src="/tiles/${encodeURIComponent(pai)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""}"></div>`;
  });

  if (drawn) {
    const isAct = highlightedIdxs.has(hand.length);
    const isWinTile = seat === winActor;
    handHtml += `<div class="tileDiv is-drawn ${isWinTile ? "agari-tile" : ""}" data-pai="${escapeHtml(drawn)}" title="${isWinTile ? `自摸: ${tileCn(drawn)}` : tileCn(drawn)}"><img src="/tiles/${encodeURIComponent(drawn)}.svg" class="tileImg ${isAct ? "tileImgHighlight" : ""} ${isWinTile ? "agari-tile" : ""}"></div>`;
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
      const renderedTiles = closedWrap.querySelectorAll(".tileDiv");
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
            svgHtml += `<rect x="${Math.round(centerX - 4.5)}" y="${y}" width="9" height="${height}" fill="#5ecb7d" rx="2"></rect>`;
          }
        }
      });
      svgEl.innerHTML = svgHtml;
    }
  }

  // 3. Render Calls: Direct tiles, newest on left towards closed hand, earliest on far right
  if (callsWrap) {
    const melds = snap.melds[seat] || [];
    callsWrap.innerHTML = renderCallsHtml(melds, seat, 0);
  }
}

function renderOpponentHand(pos, seat, snap, agariTile = null, winActor = null, isRon = false, nextSnap = null) {
  const closedWrap = $(`hand${pos}Closed`);
  const callsWrap = $(`hand${pos}Calls`);
  if (!closedWrap) return;

  const hand = snap.hands[seat] || [];
  const drawn = snap.drawn[seat];
  let html = "";
  const backSrc = getTileBackSrc();
  const isWinner = seat === winActor;
  const showTiles = state.openHands || isWinner;

  // Highlight the intended discard tile immediately on draw or after call (before dahai!)
  // ONLY show red outline when showTiles is TRUE ("暗牌的时候不需要显示红框")
  let highlightedIdx = -1;
  if (showTiles) {
    const nextEvent = nextSnap?.event;
    if (nextEvent?.type === "dahai" && nextEvent?.actor === seat) {
      if (nextEvent.tsumogiri && drawn) {
        highlightedIdx = 999;
      } else {
        for (let i = 0; i < hand.length; i++) {
          if (hand[i] === nextEvent.pai) {
            highlightedIdx = i;
            break;
          }
        }
      }
    }
  }

  hand.forEach((pai, idx) => {
    const isThisDiscard = idx === highlightedIdx;
    const src = showTiles ? `/tiles/${encodeURIComponent(pai)}.svg` : backSrc;
    html += `<div class="tileDiv"><img src="${src}" class="tileImg ${isThisDiscard ? "tileImgHighlight" : ""}"></div>`;
  });

  if (drawn) {
    const isDrawnDiscard = highlightedIdx === 999;
    const src = showTiles ? `/tiles/${encodeURIComponent(drawn)}.svg` : backSrc;
    html += `<div class="tileDiv is-drawn ${isWinner ? "agari-tile" : ""}"><img src="${src}" class="tileImg ${isWinner ? "agari-tile" : ""} ${isDrawnDiscard ? "tileImgHighlight" : ""}" title="${isWinner ? `自摸: ${tileCn(drawn)}` : ""}"></div>`;
  }

  closedWrap.innerHTML = html;

  if (callsWrap) {
    const melds = snap.melds[seat] || [];
    const povSeat = pos === "Right" ? 1 : (pos === "Top" ? 2 : 3);
    callsWrap.innerHTML = renderCallsHtml(melds, seat, povSeat);
  }
}

function renderCallsHtml(melds, seat, povSeat) {
  if (!melds || !melds.length) return "";
  const backSrc = getTileBackSrc();

  // In KillerDucky & Riichi Mahjong, calls are ordered NEWEST first, EARLIEST last!
  // Earliest call is positioned furthest from the closed hand (outer edge).
  const orderedMelds = [...melds].reverse();
  let html = "";

  for (let meldIdx = 0; meldIdx < orderedMelds.length; meldIdx++) {
    const m = orderedMelds[meldIdx];
    const type = (m.type || "").toLowerCase();
    const pai = m.pai;
    const consumed = [...(m.consumed || m.tiles || [])];

    if (type === "ankan") {
      // Standard Riichi Mahjong Ankan: [back, tile, tile, back]
      const cTiles = m.consumed?.length >= 4 ? m.consumed : (m.tiles?.length >= 4 ? m.tiles : [pai, pai, pai, pai]);
      for (let i = 0; i < 4; i++) {
        const src = (i === 0 || i === 3) ? backSrc : `/tiles/${encodeURIComponent(cTiles[i] || pai)}.svg`;
        html += `<div class="tileDiv"><img src="${src}" class="tileImg"></div>`;
      }
      continue;
    }

    // Relative seat from who discarded:
    // Kamicha (上家, left): fromIdxRel = 0
    // Toimen (对家, across): fromIdxRel = 1
    // Shimocha (下家, right): fromIdxRel = 2
    let fromIdxRel = 0;
    if (m.from !== undefined && m.from !== null) {
      fromIdxRel = (4 + seat - m.from - 1) % 4;
      if (fromIdxRel > 2) fromIdxRel = 2;
    }

    const allTiles = [...consumed];
    allTiles.splice(fromIdxRel, 0, pai);
    if (type === "kakan") {
      // Keep the actual added tile (including red five) in the stacked slot.
      const added = m.added || (m.tiles?.length === 4 ? m.tiles[3] : pai);
      allTiles.splice(fromIdxRel + 1, 0, added);
    }

    for (let i = 0; i < allTiles.length; i++) {
      const tile = allTiles[i];
      const isDaiminkanOrKakan = (type === "daiminkan" || type === "kakan");
      const isRotated = (i === fromIdxRel || (i === fromIdxRel + 1 && isDaiminkanOrKakan));
      const isFloated = (i === fromIdxRel + 1 && isDaiminkanOrKakan);

      const divClasses = ["tileDiv"];
      if (isRotated) divClasses.push("has-rotate");
      if (isFloated) divClasses.push("has-float");

      const imgClasses = ["tileImg"];
      if (isRotated) imgClasses.push("rotate");
      if (isFloated) imgClasses.push("float");

      html += `<div class="${divClasses.join(" ")}"><img src="/tiles/${encodeURIComponent(tile)}.svg" class="${imgClasses.join(" ")}"></div>`;
    }
  }

  return html;
}

function renderDecisionTileOrBadge(pai, actionObj, actionCn = "") {
  const actType = (actionObj?.type || actionObj?.action?.type || actionCn || (pai ? "dahai" : "pass")).toLowerCase();

  if (actType.includes("pass") || actType.includes("none") || actType.includes("跳过") || actType.includes("过") || actType.includes("不鸣")) {
    return `
      <div class="action-badge action-badge-pass" title="PASS">
        <span class="action-badge-text">PASS</span>
      </div>
    `;
  }
  if (actType === "ankan" || actType.includes("暗杠")) {
    const consumed = actionObj?.consumed || [];
    const tile = pai || consumed[0];
    const tilesList = consumed.length >= 4 ? consumed.slice(0, 4) : (tile ? [tile, tile, tile, tile] : []);
    const tilesHtml = tilesList.length > 0
      ? `<div class="kd-action-combo-tiles">${tilesList.map((t) => `<img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg" title="暗杠: ${tileCn(t)}">`).join("")}</div>`
      : "";
    return `
      <div class="kd-action-combo-wrap">
        <div class="action-badge action-badge-kan action-badge-ankan" title="暗杠">
          <span class="action-badge-text">暗杠</span>
        </div>
        ${tilesHtml}
      </div>
    `;
  }
  if (actType.includes("kakan") || actType.includes("加杠")) {
    const tile = pai || actionObj?.pai || actionObj?.consumed?.[0];
    const tileHtml = tile ? `<div class="kd-action-combo-tiles"><img src="/tiles/${encodeURIComponent(tile)}.svg" class="tileImg" title="加杠: ${tileCn(tile)}"></div>` : "";
    return `
      <div class="kd-action-combo-wrap">
        <div class="action-badge action-badge-kan" title="加杠">
          <span class="action-badge-text">加杠</span>
        </div>
        ${tileHtml}
      </div>
    `;
  }
  if (actType.includes("daiminkan") || actType.includes("大明杠") || actType === "kan" || actType.includes("杠")) {
    const consumed = actionObj?.consumed || [];
    const tile = pai || actionObj?.pai || consumed[0];
    const tilesList = consumed.length >= 3 ? consumed : (tile ? [tile, tile, tile] : []);
    const tilesHtml = tilesList.length > 0
      ? `<div class="kd-action-combo-tiles">${tilesList.map((t) => `<img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg" title="明杠: ${tileCn(t)}">`).join("")}</div>`
      : "";
    return `
      <div class="kd-action-combo-wrap">
        <div class="action-badge action-badge-kan" title="明杠">
          <span class="action-badge-text">明杠</span>
        </div>
        ${tilesHtml}
      </div>
    `;
  }
  if (actType.includes("chi") || actType.includes("吃")) {
    const consumed = actionObj?.consumed || [];
    const comboTiles = consumed.length > 0 ? [...consumed] : (pai ? [pai] : []);
    if (pai && !comboTiles.includes(pai)) comboTiles.push(pai);
    comboTiles.sort((a, b) => tileSortOrder(a) - tileSortOrder(b));
    const tilesHtml = comboTiles.length > 0
      ? `<div class="kd-action-combo-tiles">${comboTiles.map((t) => `<img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg" title="吃: ${tileCn(t)}">`).join("")}</div>`
      : "";
    return `
      <div class="kd-action-combo-wrap">
        <div class="action-badge action-badge-chi" title="Chi">
          <span class="action-badge-text">Chi</span>
        </div>
        ${tilesHtml}
      </div>
    `;
  }
  if (actType.includes("pon") || actType.includes("碰")) {
    const tile = pai || actionObj?.pai || actionObj?.consumed?.[0];
    const comboTiles = actionObj?.consumed?.length ? actionObj.consumed : (tile ? [tile, tile] : []);
    const tilesHtml = comboTiles.length > 0
      ? `<div class="kd-action-combo-tiles">${comboTiles.map((t) => `<img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg" title="碰: ${tileCn(t)}">`).join("")}</div>`
      : "";
    return `
      <div class="kd-action-combo-wrap">
        <div class="action-badge action-badge-pon" title="Pon">
          <span class="action-badge-text">Pon</span>
        </div>
        ${tilesHtml}
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

function getKillerActionSpan(item) {
  if (!item) return "";
  const act = item.action || item;
  if (!act || !act.type) return "";

  const typeMap = {
    dahai: "切",
    chi: "吃",
    pon: "碰",
    daiminkan: "杠",
    kakan: "杠",
    ankan: "暗杠",
    reach: "立直",
    hora: act.actor !== act.target && act.target !== undefined && act.target !== null ? "荣和" : "自摸",
    ron: "荣和",
    tsumo: "自摸",
    none: "跳过",
    pass: "跳过"
  };
  const label = typeMap[act.type] !== undefined ? typeMap[act.type] : (act.type || "");

  let pai = (act.type && act.type.endsWith("kan")) ? (act.consumed?.[0] || act.pai) : act.pai;
  let tilesHtml = "";
  if (pai) {
    let tiles = [pai];
    if (act.consumed && !act.type.endsWith("kan")) {
      tiles = act.consumed;
    }
    const tilesImgHtml = tiles.map((t) => {
      return `<img src="/tiles/${encodeURIComponent(t)}.svg" class="tileImg" alt="${escapeHtml(t)}">`;
    }).join("");
    tilesHtml = `<span class="killer-act-tiles">${tilesImgHtml}</span>`;
  }
  const labelHtml = label ? `<span class="killer-act-label">${escapeHtml(label)}</span>` : "";
  return `<span class="killer-act-wrap">${labelHtml}${tilesHtml}</span>`;
}

function renderThisRoundModalContent(snap, currentKyoku) {
  const contentEl = $("infoThisRoundContent");
  if (!contentEl) return;
  const targetActor = state.targetActor ?? 0;
  const isHora = snap?.event?.type === "hora";
  const isRyuukyoku = snap?.event?.type === "ryuukyoku" || snap?.event?.type === "ryukyoku";

  let html = "";
  if (isHora) {
    const winActor = snap.event.actor;
    const tgtActor = snap.event.target;
    const isRon = winActor !== tgtActor && tgtActor !== undefined && tgtActor !== null;
    const isHeroWin = winActor === targetActor;
    const isHeroLoser = isRon && tgtActor === targetActor;

    const relativeSeatName = (seat) => {
      const diff = (seat - targetActor + 4) % 4;
      return ["自家", "下家", "对家", "上家"][diff];
    };

    const winLabel = relativeSeatName(winActor);
    const tgtLabel = isRon ? relativeSeatName(tgtActor) : "";

    // 1. Header: Winner and Action
    const winTitle = isRon ? `${winLabel} 荣和 ${tgtLabel}` : `${winLabel} 自摸`;
    const winTitleColor = isHeroWin ? "#30d158" : isHeroLoser ? "#ff453a" : "#ffffff";
    html += `<div class="kd-settle-header" style="text-align:center; margin-bottom:12px; padding-top:4px;">
      <div style="font-size:16px; font-weight:700; color:${winTitleColor}; margin-bottom:4px; letter-spacing:0.3px;">${winTitle}</div>`;

    // 2. Score, Han/Fu, and Title Tag
    let han = snap.event?.han ?? snap.event?.count ?? null;
    let fu = snap.event?.fu ?? null;
    let titleId = snap.event?.title_id ?? 0;
    let titleName = MAJSOUL_TITLES[titleId] || null;
    if (!titleName && han !== null) {
      if (han >= 13) titleName = "役满";
      else if (han >= 11) titleName = "三倍满";
      else if (han >= 8) titleName = "倍满";
      else if (han >= 6) titleName = "跳满";
      else if (han === 5 || (han === 4 && fu >= 40) || (han === 3 && fu >= 70)) titleName = "满贯";
    }

    const scoreDelta = snap.event.deltas?.[winActor] ?? currentKyoku?.score_deltas?.[winActor] ?? 0;
    const fuHanText = [fu ? `${fu} 符` : "", han ? `${han} 番` : ""].filter(Boolean).join(" ");
    const scoreText = scoreDelta > 0 ? `${scoreDelta} 点` : "";

    html += `<div style="font-size:14px; font-weight:600; color:#ffcc00; display:flex; align-items:center; justify-content:center; gap:8px;">
      ${titleName ? `<span class="kd-settle-tag" style="background:rgba(255,204,0,0.18); border:1px solid rgba(255,204,0,0.35); padding:1px 6px; border-radius:4px; font-size:12px; color:#ffcc00; font-weight:700;">${titleName}</span>` : ""}
      <span>${fuHanText}${fuHanText && scoreText ? " · " : ""}${scoreText}</span>
    </div></div>`;

    // 3. Yaku List Breakdown
    const rawFans = snap.event?.fans || [];
    const validFans = rawFans.filter((f) => (f.val || 0) > 0);
    if (validFans.length > 0) {
      html += `<div class="kd-settle-yaku-list" style="margin:10px 0 12px; border-top:1px solid rgba(255,255,255,0.08); border-bottom:1px solid rgba(255,255,255,0.08); padding:6px 2px;">`;
      for (const f of validFans) {
        const name = f.name || MAJSOUL_YAKU_NAMES[f.id] || `役种 ${f.id}`;
        html += `<div style="display:flex; justify-content:space-between; align-items:center; font-size:12.5px; color:rgba(255,255,255,0.85); padding:2.5px 4px;">
          <span>${escapeHtml(name)}</span>
          <span style="font-family:monospace; color:#ffd60a; font-weight:600;">${f.val} 番</span>
        </div>`;
      }
      html += `</div>`;
    }

    // 4. 4-Seat Delta Table
    html += `<table class="kd-settle-delta-table" style="width:100%; border-collapse:collapse; margin-top:6px; font-size:13px;">
      <tbody>`;
    for (let diff = 0; diff < 4; diff++) {
      const seat = (targetActor + diff) % 4;
      const seatLabel = ["自家", "下家", "对家", "上家"][diff];
      const delta = snap.event.deltas?.[seat] ?? currentKyoku?.score_deltas?.[seat] ?? 0;
      let deltaStr = String(delta);
      let deltaColor = "rgba(255,255,255,0.7)";
      if (delta > 0) {
        deltaStr = `+${delta}`;
        deltaColor = "#30d158";
      } else if (delta < 0) {
        deltaColor = "#ff453a";
      }
      const isTarget = diff === 0;
      const rowBg = isTarget ? "background:rgba(10,132,255,0.12); border-radius:4px;" : "";
      html += `<tr style="border-top:1px solid rgba(255,255,255,0.06); ${rowBg}">
        <td style="padding:5px 8px; font-weight:${isTarget ? "700" : "500"}; color:${isTarget ? "#ffffff" : "rgba(255,255,255,0.8)"};">${seatLabel}</td>
        <td style="padding:5px 8px; text-align:right; font-family:monospace; font-weight:700; color:${deltaColor};">${deltaStr}</td>
      </tr>`;
    }
    const honbaCount = snap.honba ?? currentKyoku?.honba ?? 0;
    if (honbaCount > 0) {
      html += `<tr style="border-top:1px solid rgba(255,255,255,0.06);">
        <td style="padding:5px 8px; color:rgba(255,255,255,0.65);">本场供 (${honbaCount}本场)</td>
        <td style="padding:5px 8px; text-align:right; font-family:monospace; color:#ffd60a; font-weight:700;">${honbaCount * 300}</td>
      </tr>`;
    }
    const kyotaku = snap.kyotaku ?? currentKyoku?.kyotaku ?? 0;
    if (kyotaku > 0) {
      html += `<tr style="border-top:1px solid rgba(255,255,255,0.06);">
        <td style="padding:5px 8px; color:rgba(255,255,255,0.65);">立直场供 (${kyotaku}根)</td>
        <td style="padding:5px 8px; text-align:right; font-family:monospace; color:rgba(255,255,255,0.65);">${kyotaku * 1000}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  } else if (isRyuukyoku) {
    html += `<div class="kd-settle-header" style="text-align:center; margin-bottom:12px; padding-top:4px;">
      <div style="font-size:16px; font-weight:700; color:#ff9f0a; margin-bottom:4px; letter-spacing:0.3px;">荒牌流局</div>`;

    const pureDeltas = [0, 1, 2, 3].map((seat) => {
      let d = snap?.event?.deltas?.[seat];
      if (d === undefined || d === null) {
        d = currentKyoku?.score_deltas?.[seat] ?? 0;
        if (snap?.riichi?.[seat]) d += 1000;
      }
      return d;
    });
    const tenpaiSeats = [0, 1, 2, 3].filter((s) => pureDeltas[s] > 0);
    const summary = tenpaiSeats.length > 0 ? "罚符结算" : "全员不听";
    html += `<div style="font-size:13px; color:rgba(255,255,255,0.7);">${summary}</div></div>`;

    html += `<table class="kd-settle-delta-table" style="width:100%; border-collapse:collapse; margin-top:6px; font-size:13px;">
      <tbody>`;
    for (let diff = 0; diff < 4; diff++) {
      const seat = (targetActor + diff) % 4;
      const seatLabel = ["自家", "下家", "对家", "上家"][diff];
      const delta = pureDeltas[seat] ?? 0;
      let deltaStr = String(delta);
      let deltaColor = "rgba(255,255,255,0.7)";
      if (delta > 0) {
        deltaStr = `+${delta}`;
        deltaColor = "#30d158";
      } else if (delta < 0) {
        deltaColor = "#ff453a";
      }
      const isTarget = diff === 0;
      const rowBg = isTarget ? "background:rgba(10,132,255,0.12); border-radius:4px;" : "";
      html += `<tr style="border-top:1px solid rgba(255,255,255,0.06); ${rowBg}">
        <td style="padding:5px 8px; font-weight:${isTarget ? "700" : "500"}; color:${isTarget ? "#ffffff" : "rgba(255,255,255,0.8)"};">${seatLabel}</td>
        <td style="padding:5px 8px; text-align:right; font-family:monospace; font-weight:700; color:${deltaColor};">${deltaStr}</td>
      </tr>`;
    }
    const honbaCount = snap.honba ?? currentKyoku?.honba ?? 0;
    if (honbaCount > 0) {
      html += `<tr style="border-top:1px solid rgba(255,255,255,0.06);">
        <td style="padding:5px 8px; color:rgba(255,255,255,0.65);">本场供 (${honbaCount}本场)</td>
        <td style="padding:5px 8px; text-align:right; font-family:monospace; color:#ffd60a; font-weight:700;">${honbaCount * 300}</td>
      </tr>`;
    }
    const kyotaku = snap.kyotaku ?? currentKyoku?.kyotaku ?? 0;
    if (kyotaku > 0) {
      html += `<tr style="border-top:1px solid rgba(255,255,255,0.06);">
        <td style="padding:5px 8px; color:rgba(255,255,255,0.65);">立直场供 (${kyotaku}根)</td>
        <td style="padding:5px 8px; text-align:right; font-family:monospace; color:rgba(255,255,255,0.65);">${kyotaku * 1000}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  contentEl.innerHTML = html;
  const modal = $("infoThisRoundModal");
  if (modal && !modal.open) {
    modal.dataset.openedAt = String(Date.now());
    if (typeof modal.showModal === "function") {
      modal.showModal();
    } else {
      modal.setAttribute("open", "");
    }
  }
}

function renderDecisionCard(dec, isExactStep, snap) {
  const optInfo = $("optInfo");
  const actualCell = $("optActualCell");
  const aiCell = $("optAiCell");
  const tableBody = $("candTableBody");
  const roundModal = $("infoThisRoundModal");

  const currentKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  const isHora = snap?.event?.type === "hora";
  const isRyuukyoku = snap?.event?.type === "ryuukyoku" || snap?.event?.type === "ryukyoku";

  // Case 1: Round End (Hora or Ryuukyoku)
  if (isHora || isRyuukyoku) {

    if (actualCell) actualCell.innerHTML = '<span style="opacity:0;">&nbsp;</span>';
    if (aiCell) aiCell.innerHTML = '<span style="opacity:0;">&nbsp;</span>';
    if (tableBody) tableBody.innerHTML = "";
    renderThisRoundModalContent(snap, currentKyoku);
    return;
  }

  // If not round end, close the round settlement modal if open
  if (roundModal && roundModal.open) {
    if (typeof roundModal.close === "function") roundModal.close();
    else roundModal.removeAttribute("open");
  }

  // Case 2: Non-decision step -> Keep table structure but do NOT show tiles
  if (optInfo) optInfo.style.display = "flex";

  if (!dec || !isExactStep) {
    if (actualCell) actualCell.innerHTML = '<span style="opacity:0;">&nbsp;</span>';
    if (aiCell) aiCell.innerHTML = '<span style="opacity:0;">&nbsp;</span>';
    if (tableBody) tableBody.innerHTML = "";
    return;
  }

  // Case 3: Exact Decision Step
  const heroAct = dec.actual || dec.candidates?.find((c) => c.is_actual);
  const aiAct = dec.expected || dec.candidates?.[0];

  if (actualCell) actualCell.innerHTML = getKillerActionSpan(heroAct);
  if (aiCell) aiCell.innerHTML = getKillerActionSpan(aiAct);

  if (tableBody) {
    const rowsHtml = (dec.candidates || []).map((cand) => {
      const qVal = Number(cand.q_value ?? cand.raw_q ?? 0).toFixed(2);
      const probVal = Number(cand.prob ?? 0).toFixed(2);
      return `<tr>
        <td>${getKillerActionSpan(cand)}</td>
        <td>${qVal}</td>
        <td>${probVal}</td>
      </tr>`;
    }).join("");
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
  const terminalIdx = kyoku.steps.findIndex((s) => s.event?.type === "hora" || s.event?.type === "ryukyoku" || s.event?.type === "ryuukyoku");
  const targetEndStep = terminalIdx !== -1 ? terminalIdx : kyoku.steps.length - 1;
  if (state.stepIdx < targetEndStep) {
    setStep(targetEndStep);
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
    subEl.innerHTML = `当前检讨视角：<strong style="color:var(--color-text-bright);">${WIND_CN[targetActor]}家 (自家)</strong> · 点击任意局可直接跳转该局检讨`;
  }

  const povTabs = $("roundModalPovTabs");
  if (povTabs) {
    povTabs.innerHTML = `
      <span style="font-size:12px; color:var(--color-text-secondary); margin-right:4px;">切换视角:</span>
      ${[0, 1, 2, 3].map((s) => {
        const active = s === targetActor ? "active" : "";
        const label = s === targetActor ? `${WIND_CN[s]}家 (自家)` : `${WIND_CN[s]}家`;
        return `<button class="round-modal-pov-btn ${active}" data-seat="${s}">
          ${label}
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

  // 1. Table Header (Pure Match Points History)
  if (thead) {
    thead.innerHTML = `
      <tr>
        <th style="width:70px;">局名</th>
        <th style="width:65px;">本场</th>
        <th style="width:80px;">庄家</th>
        <th style="text-align:left; min-width:200px;">自摸 / 荣和 / 结算详情</th>
        ${[0, 1, 2, 3].map((s) => {
          const isTarget = s === targetActor;
          const thClass = isTarget ? "active-pov-th" : "";
          const povBadge = isTarget ? " <span style='font-size:10px; color:#ffcc00;'>(自家)</span>" : "";
          return `<th class="${thClass}" style="text-align:right;">${WIND_CN[s]}家${povBadge}</th>`;
        }).join("")}
        <th style="width:90px; text-align:right;">自家增减</th>
      </tr>
    `;
  }

  // 2. Table Body (Score Deltas per Kyoku)
  const cumDeltas = [0, 0, 0, 0];

  tbody.innerHTML = res.kyokus
    .map((k, idx) => {
      const oyaName = `${WIND_CN[k.oya]}家`;
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

      // 结算文本
      const rawSummaries = k.end_summaries && k.end_summaries.length
        ? k.end_summaries.join("<br>")
        : (k.result || "流局");
      const resultText = escapeHtml(sanitizeSummaryText(rawSummaries)).replace(/&lt;br&gt;/g, "<br>");

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

  // 3. Table Footer (全庄各家结算总分)
  if (tfoot && res.kyokus.length > 0) {
    const lastKyoku = res.kyokus[res.kyokus.length - 1];
    const finalScores = lastKyoku.end_scores || [25000, 25000, 25000, 25000];
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
// Local Paipu Cache & Drag/Drop Import
// ============================================================================
async function loadLocalCachedRecords() {
  const container = $("localRecordsList");
  const countEl = $("localRecordsCount");
  if (!container) return;

  try {
    const data = await apiFetch("/api/records/local");
    const records = data.records || [];
    if (countEl) countEl.textContent = `共 ${records.length} 场`;

    if (records.length === 0) {
      container.innerHTML = `<div class="kd-local-empty">暂无本地缓存牌谱，可选择或拖入本地文件进行检讨</div>`;
      return;
    }

    container.innerHTML = records.map((rec) => {
      let dateStr = "未知时间";
      if (rec.start_time) {
        try {
          const d = new Date(rec.start_time * 1000);
          const y = d.getFullYear();
          const m = String(d.getMonth() + 1).padStart(2, "0");
          const day = String(d.getDate()).padStart(2, "0");
          const hh = String(d.getHours()).padStart(2, "0");
          const mm = String(d.getMinutes()).padStart(2, "0");
          dateStr = `${y}-${m}-${day} ${hh}:${mm}`;
        } catch (_) {}
      }
      const sizeKb = rec.file_size ? `${Math.round(rec.file_size / 1024)} KB` : "";
      const namesStr = (rec.names && rec.names.length) ? rec.names.join(" · ") : "四人对局";
      const modeLabel = rec.mode === 1 ? "四人东" : "四人南";

      return `
        <div class="kd-local-item" data-uuid="${escapeHtml(rec.uuid)}">
          <div class="kd-local-item-top">
            <div class="kd-local-meta-left">
              <span class="kd-local-badge">${escapeHtml(modeLabel)}</span>
              <span class="kd-local-time">${escapeHtml(dateStr)}</span>
            </div>
            <span class="kd-local-size">${sizeKb}</span>
          </div>
          <div class="kd-local-item-mid">
            <div class="kd-local-names" title="${escapeHtml(namesStr)}">${escapeHtml(namesStr)}</div>
            <button type="button" class="kd-local-btn">立即分析</button>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".kd-local-item").forEach((el) => {
      el.addEventListener("click", () => {
        const uuid = el.getAttribute("data-uuid");
        if (uuid) {
          startAnalysisFromInput(uuid, state.selectedModel);
        }
      });
    });
  } catch (err) {
    if (container) {
      container.innerHTML = `<div class="kd-local-empty">加载本地牌谱失败: ${escapeHtml(err.message)}</div>`;
    }
  }
}

function initLocalFileDropzone() {
  const dropzone = $("fileDropzone");
  const fileInput = $("localFileInput");
  if (!dropzone || !fileInput) return;

  dropzone.addEventListener("click", () => {
    fileInput.click();
  });

  fileInput.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    readAndAnalyzeFile(file);
    fileInput.value = "";
  });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.add("is-dragover");
  });

  dropzone.addEventListener("dragleave", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.remove("is-dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.remove("is-dragover");
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    readAndAnalyzeFile(file);
  });
}

function readAndAnalyzeFile(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    const content = e.target?.result;
    if (!content || typeof content !== "string") {
      showToast("文件内容为空或无法读取", "error");
      return;
    }
    showToast(`正在载入本地牌谱文件: ${file.name}`, "info");
    startAnalysisFromInput(content.trim(), state.selectedModel);
  };
  reader.onerror = () => {
    showToast(`读取文件失败: ${file.name}`, "error");
  };
  reader.readAsText(file, "utf-8");
}

// ============================================================================
// Event Bindings
// ============================================================================
function bindEvents() {
  // Local File Dropzone & File Input
  initLocalFileDropzone();

  // Tier 1 Model: Mortal-V4-582500 (Single, Dedicated Model)
  state.selectedModel = "akagi";
  const landingModel = $("landingModelCard");
  landingModel?.addEventListener("click", () => {
    state.selectedModel = "akagi";
    if ($("engineSelect")) $("engineSelect").value = "akagi";
    updateEngineStatusDisplay("akagi");
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
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        stepDeltaFast(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        stepDeltaFast(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        jumpPrevDecision();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        jumpNextDecision();
      } else if (e.key === " " || e.code === "Space") {
        e.preventDefault();
        togglePlay();
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

  // Round score history modal (Dedicated button, strictly independent from rating badge)
    $("btnOpenScoreModal")?.addEventListener("click", openRoundScoresModal);
  $("btnControlsScores")?.addEventListener("click", openRoundScoresModal);
  $("infoThisRoundClose")?.addEventListener("click", () => {
    $("infoThisRoundModal")?.close();
  });
  $("infoThisRoundModal")?.addEventListener("click", (e) => {
    if (e.target === $("infoThisRoundModal")) $("infoThisRoundModal")?.close();
  });
  $("btnRoundScore")?.addEventListener("click", openRoundScoresModal);
  $("roundModalClose")?.addEventListener("click", () => closeModal("roundModal"));

  // Engine modal
  $("btnOpenEngineModal")?.addEventListener("click", () => {
    const statusMsg = $("engineModalStatusMsg");
    if (statusMsg) statusMsg.textContent = "";
    openModal("engineModal");
  });
  $("engineModalClose")?.addEventListener("click", () => closeModal("engineModal"));
  $("btnCancelEngineModal")?.addEventListener("click", () => closeModal("engineModal"));
  $("btnSaveEngineConfig")?.addEventListener("click", async () => {
    const model = $("engineSelect")?.value || "akagi";
    state.selectedModel = model;
    updateEngineStatusDisplay(model);
    const statusMsg = $("engineModalStatusMsg");
    if (statusMsg) {
      statusMsg.style.color = "#30d158";
      statusMsg.textContent = "正在同步配置并重新检讨...";
    }
    try {
      await apiFetch("/api/engine/config", {
        method: "POST",
        body: JSON.stringify({ selected_model: model }),
      });
      closeModal("engineModal");
      if (state.reviewData && state.gameId) {
        await startAnalysisFromInput(state.gameId, model, state.targetActor);
      }
    } catch (e) {
      if (statusMsg) {
        statusMsg.style.color = "#ff453a";
        statusMsg.textContent = `切换失败: ${e.message || "请求异常"}`;
      }
    }
  });

  // Shortcuts Modal bindings
  $("btnOpenShortcutsModal")?.addEventListener("click", () => openModal("shortcutsModal"));
  $("shortcutsModalClose")?.addEventListener("click", () => closeModal("shortcutsModal"));

  // Backdrop click closes any modal
  document.querySelectorAll(".modal-backdrop").forEach((bd) => {
    bd.addEventListener("click", () => {
      document.querySelectorAll(".modal:not(.hidden)").forEach((m) => m.classList.add("hidden"));
      bd.classList.add("hidden");
    });
  });

  // Global Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    // Dismiss settlement modal or dialog on keyboard shortcuts
    const roundDialog = $("infoThisRoundModal");
    if (roundDialog && roundDialog.open) {
      if (e.key === "Escape" || e.key === "Enter" || e.key === " " || e.key.startsWith("Arrow") || e.key === "[" || e.key === "]" || e.key === "," || e.key === ".") {
        if (typeof roundDialog.close === "function") roundDialog.close();
        else roundDialog.removeAttribute("open");
        if (e.key === "Escape" || e.key === "Enter") {
          e.preventDefault();
          return;
        }
      }
    }

    if (e.key === "Escape") {
      document.querySelectorAll(".modal:not(.hidden)").forEach((m) => m.classList.add("hidden"));
      document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((b) => b.classList.add("hidden"));
      return;
    }

    const activeEl = document.activeElement;
    if (activeEl && (activeEl.tagName === "TEXTAREA" || (activeEl.tagName === "INPUT" && (activeEl.type === "text" || activeEl.type === "search" || activeEl.type === "password")))) {
      return;
    }

    // Toggle shortcuts help modal with '?'
    if (e.key === "?" || (e.shiftKey && e.key === "/")) {
      e.preventDefault();
      const shortcutsModal = $("shortcutsModal");
      if (shortcutsModal?.classList.contains("hidden")) openModal("shortcutsModal");
      else closeModal("shortcutsModal");
      return;
    }

    // If modal is open, don't execute board navigation shortcuts
    const isModalOpen = Array.from(document.querySelectorAll(".modal")).some(m => !m.classList.contains("hidden") && (m.tagName !== "DIALOG" || m.open)) || !!document.querySelector("dialog[open]");
    if (isModalOpen) return;

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
      "<": () => jumpPrevBlunderFast(),
      ".": () => jumpNextBlunderFast(),
      ">": () => jumpNextBlunderFast(),
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
      s: () => {
        const modal = $("roundModal");
        if (modal?.classList.contains("hidden")) openModal("roundModal");
        else closeModal("roundModal");
      },
      S: () => {
        const modal = $("roundModal");
        if (modal?.classList.contains("hidden")) openModal("roundModal");
        else closeModal("roundModal");
      },

      " ": togglePlay,
    };

    if (actions[e.key]) {
      e.preventDefault();
      actions[e.key]();
    }
  });

  // Global mouse wheel steps forward/backward across entire screen (green and black areas alike)
  window.addEventListener("wheel", (e) => {
    if (!state.reviewData) return;
    if (e.target.closest("#roundModal, #shortcutsModal, #engineModal")) return;

    const roundDialog = $("infoThisRoundModal");
    if (roundDialog && roundDialog.open) {
      const openedAt = Number(roundDialog.dataset.openedAt || 0);
      if (Date.now() - openedAt < 300) {
        e.preventDefault();
        return;
      }
      if (typeof roundDialog.close === "function") roundDialog.close();
      else roundDialog.removeAttribute("open");
    }

    e.preventDefault();
    if (e.deltaY > 0) stepDelta(1);
    else if (e.deltaY < 0) stepDelta(-1);
  }, { passive: false });
}

function updateResponsiveZoom() {
  document.documentElement.style.setProperty("--zoom", "1.0");
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
