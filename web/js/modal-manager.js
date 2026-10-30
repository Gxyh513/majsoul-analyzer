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
  if (el?.tagName === "DIALOG") {
    if (el.open) {
      if (typeof el.close === "function") el.close();
      else el.removeAttribute("open");
    }
  } else if (el) {
    el.classList.add("hidden");
  }
  const bd = $(id + "Backdrop");
  if (bd) bd.classList.add("hidden");
}

function closeAllModals() {
  document.querySelectorAll(".modal").forEach((modal) => closeModal(modal.id));
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => backdrop.classList.add("hidden"));
}

function getRoundDeltas(k) {
  return [0, 1, 2, 3].map((seat) => {
    const start = roundScoreNumber(k.init_scores?.[seat]);
    const end = roundScoreNumber(k.end_scores?.[seat]);
    return start !== null && end !== null ? end - start : roundScoreNumber(k.score_deltas?.[seat]);
  });
}

function roundScoreNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value) && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function fmtInt(n) {
  const num = roundScoreNumber(n);
  return num === null ? "—" : num.toLocaleString();
}

function fmtDeltaHtml(n) {
  const num = roundScoreNumber(n);
  if (num === null) return `<span class="delta-badge delta-zero">—</span>`;
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

  const targetActor = state.targetActor ?? 0;
  const currentKyoku = res.kyokus[state.kyokuIdx];
  // Score columns keep the same actors across the game. Recover the original
  // East seat even when a cache starts in the middle of the game.
  let startOya = null;
  for (const k of res.kyokus) {
    const labelNumber = /^\s*[东南西北]\s*([1-4])/.exec(k.label || "")?.[1];
    const handNumber = Number(k.kyoku_num ?? labelNumber);
    if (Number.isInteger(k.oya) && k.oya >= 0 && k.oya < 4 &&
        Number.isInteger(handNumber) && handNumber >= 1 && handNumber <= 4) {
      startOya = (k.oya - handNumber + 5) % 4;
      break;
    }
  }
  const seatIdentity = (seat) => startOya === null ? `起始座位${seat + 1}` : `起始${WIND_CN[(seat - startOya + 4) % 4]}家`;
  const currentWind = (seat) => currentKyoku ? handWind(seat, currentKyoku) : null;

  // Subtitle info & Quick Pov switcher
  const subEl = $("roundModalSub");
  if (subEl) {
    const wind = currentWind(targetActor);
    const currentSeat = wind ? `本局${wind}家 (自家)` : "本局座风未知 (自家)";
    subEl.innerHTML = `当前检讨视角：<strong style="color:var(--color-text-bright);">${escapeHtml(currentSeat)}</strong> · ${escapeHtml(seatIdentity(targetActor))} · 得点列按起始座位固定，结算详情按本局座风 · 点击任意局可直接跳转该局检讨`;
  }

  const povTabs = $("roundModalPovTabs");
  if (povTabs) {
    povTabs.innerHTML = `
      <span style="font-size:12px; color:var(--color-text-secondary); margin-right:4px;">切换视角:</span>
      ${[0, 1, 2, 3].map((s) => {
        const active = s === targetActor ? "active" : "";
        const wind = currentWind(s);
        const label = `${seatIdentity(s)}${wind ? ` · 本局${wind}家` : ""}${s === targetActor ? " (自家)" : ""}`;
        return `<button class="round-modal-pov-btn ${active}" data-seat="${s}">
          ${escapeHtml(label)}
        </button>`;
      }).join("")}
    `;
    povTabs.querySelectorAll(".round-modal-pov-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const s = parseInt(btn.dataset.seat, 10);
        await switchSeatPerspective(s);
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
          return `<th class="${thClass}" style="text-align:right;">${escapeHtml(seatIdentity(s))}${povBadge}</th>`;
        }).join("")}
        <th style="width:90px; text-align:right;">自家增减</th>
      </tr>
    `;
  }

  // 2. Table Body (Score Deltas per Kyoku)
  const cumDeltas = [0, 0, 0, 0];

  tbody.innerHTML = res.kyokus
    .map((k, idx) => {
      const oyaName = Number.isInteger(k.oya) && k.oya >= 0 && k.oya < 4 ? seatIdentity(k.oya) : "未知座位";
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
        cumDeltas[s] = cumDeltas[s] === null || deltas[s] === null ? null : cumDeltas[s] + deltas[s];
      }

      // Summaries use this row's hand winds, not the fixed table columns.
      // In incomplete caches, never leave an unrecognized identity prefix in
      // the output; keep the anonymous action rather than leaking names.
      const endings = (k.steps || []).map((step) => step?.event).filter((event) =>
        event && ["hora", "ryukyoku", "ryuukyoku"].includes(event.type));
      const hasSummaries = Array.isArray(k.end_summaries) && k.end_summaries.length > 0;
      const rawSummaries = hasSummaries ? k.end_summaries : [k.result || "流局"];
      const corrected = hasSummaries ? correctedRoundSummaries(k) : rawSummaries.map((text, i) => sanitizeSummaryText(text, endings[i], k));
      const drawLabels = ["流局", "荒牌流局", "流局满贯", "九种九牌", "四风连打", "四杠散了", "四家立直"];
      const summaries = corrected.map((value, i) => {
        const text = String(value ?? "");
        const event = endings[i];
        if (event?.type === "hora") {
          const label = (seat) => Number.isInteger(seat) && seat >= 0 && seat < 4
            ? `${handWind(seat, k)}家${seat === targetActor ? " (自家)" : ""}` : "未知座位";
          const isRon = event.target != null && event.target !== event.actor;
          const prefix = isRon ? `${label(event.actor)} 荣和 ${label(event.target)}` : `${label(event.actor)} 自摸`;
          return text === prefix || text.startsWith(`${prefix} `) ? text : prefix;
        }
        if (drawLabels.includes(text)) return text;
        if (event?.type === "ryukyoku" || event?.type === "ryuukyoku") return "流局";
        // Old summaries may survive without their events. Resolve only an exact
        // unambiguous name prefix, leaving score and yaku text untouched.
        const names = Array.isArray(res.names) ? res.names : [];
        const candidates = [];
        for (let actor = 0; actor < 4; actor++) {
          if (typeof names[actor] !== "string" || !names[actor]) continue;
          const actorLabel = `${handWind(actor, k)}家${actor === targetActor ? " (自家)" : ""}`;
          const tsumoPrefix = `${names[actor]} 自摸 `;
          if (text.startsWith(tsumoPrefix)) candidates.push(actorLabel + " 自摸 " + text.slice(tsumoPrefix.length));
          for (let target = 0; target < 4; target++) {
            if (actor === target || typeof names[target] !== "string" || !names[target]) continue;
            const ronPrefix = `${names[actor]} 荣和 ${names[target]} `;
            const targetLabel = `${handWind(target, k)}家${target === targetActor ? " (自家)" : ""}`;
            if (text.startsWith(ronPrefix)) candidates.push(`${actorLabel} 荣和 ${targetLabel} ` + text.slice(ronPrefix.length));
          }
        }
        if (candidates.length === 1) return candidates[0];
        if (text.includes(" 荣和 ")) return "荣和（结算详情不可用）";
        if (text.includes(" 自摸 ")) return "自摸（结算详情不可用）";
        return "结算详情不可用";
      });
      // Escape each whole summary before adding our own line separators.
      const resultText = summaries.map(escapeHtml).join("<br>");

      const currentDelta = deltas[targetActor];

      // 4 Players columns HTML
      const playersColsHtml = [0, 1, 2, 3].map((s) => {
        const scoreVal = k.end_scores?.[s];
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
    const finalScores = [0, 1, 2, 3].map((seat) => roundScoreNumber(lastKyoku.end_scores?.[seat]));
    const targetCumDelta = cumDeltas[targetActor];

    // A rank is meaningful only when all four final scores are known.
    const ranks = {};
    if (finalScores.every((score) => score !== null)) {
      const sortedSeats = [0, 1, 2, 3].sort((a, b) => finalScores[b] - finalScores[a]);
      sortedSeats.forEach((s, r) => { ranks[s] = r + 1; });
    }

    tfoot.innerHTML = `
      <tr>
        <td style="text-align:center; font-weight:700; color:#ffcc00;">全庄终局</td>
        <td style="text-align:center; color:var(--color-text-secondary);">${res.kyokus.length} 局</td>
        <td style="text-align:center; color:var(--color-text-secondary);">-</td>
        <td style="text-align:left; font-weight:700; color:var(--color-text-bright);">全庄结算总分</td>
        ${[0, 1, 2, 3].map((s) => {
          const isTarget = s === targetActor;
          const tdClass = isTarget ? "active-pov-td" : "";
          const rankBadge = ranks[s] ? `<span style="font-size:10px; color:${ranks[s] === 1 ? '#ffcc00' : 'rgba(235,235,245,0.6)'}; margin-left:3px;">[${ranks[s]}位]</span>` : "";
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
