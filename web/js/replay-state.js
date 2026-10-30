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
  soundEnabled: localStorage.getItem("majsoul_sound") !== "0",
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

function sanitizeSummaryText(text, event = null, kyoku = null) {
  const value = String(text ?? "");
  if (event?.type !== "hora") return value;
  const names = state.reviewData?.names || [];
  const who = names[event.actor];
  if (typeof who !== "string" || !who) return value;
  const seatLabel = (seat) => {
    const wind = kyoku ? handWind(seat, kyoku) : WIND_CN[seat % 4];
    return seat === (state.targetActor ?? 0) ? `${wind}家 (自家)` : `${wind}家`;
  };
  const isRon = event.target !== undefined && event.target !== null && event.actor !== event.target;
  const target = names[event.target];
  const originalPrefix = isRon ? `${who} 荣和 ${target} ` : `${who} 自摸 `;
  if (isRon && (typeof target !== "string" || !target)) return value;
  if (!value.startsWith(originalPrefix)) return value;
  const safePrefix = isRon ? `${seatLabel(event.actor)} 荣和 ${seatLabel(event.target)} ` : `${seatLabel(event.actor)} 自摸 `;
  // Only replace the identity prefix, never score text or a yaku name.
  return safePrefix + value.slice(originalPrefix.length);
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
