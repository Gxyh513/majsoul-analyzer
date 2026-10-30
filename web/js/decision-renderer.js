// ============================================================================
// Active Decision Details Card (In Right Sidebar)
// ============================================================================
// Mahjong Soul fan IDs differ from Tenhou's. Source: 雀魂API 定数一覧にゃ.
// https://wikiwiki.jp/majsoul-api/定数一覧にゃ
const MAJSOUL_YAKU_NAMES = {
  1: "门前清自摸和", 2: "立直", 3: "抢杠", 4: "岭上开花", 5: "海底捞月",
  6: "河底捞鱼", 7: "役牌 白", 8: "役牌 发", 9: "役牌 中", 10: "自风牌",
  11: "场风牌", 12: "断幺九", 13: "一杯口", 14: "平和", 15: "混全带幺九",
  16: "一气通贯", 17: "三色同顺", 18: "两立直", 19: "三色同刻", 20: "三杠子",
  21: "对对和", 22: "三暗刻", 23: "小三元", 24: "混老头", 25: "七对子",
  26: "纯全带幺九", 27: "混一色", 28: "二杯口", 29: "清一色", 30: "一发",
  31: "宝牌", 32: "红宝牌", 33: "里宝牌", 34: "拔北",
  35: "天和", 36: "地和", 37: "大三元", 38: "四暗刻", 39: "字一色",
  40: "绿一色", 41: "清老头", 42: "国士无双", 43: "小四喜", 44: "四杠子",
  45: "九莲宝灯", 46: "八连庄", 47: "纯正九莲宝灯", 48: "四暗刻单骑",
  49: "国士无双十三面", 50: "大四喜", 51: "燕返し", 52: "槓振り", 53: "十二落抬",
  54: "五門斉", 55: "三連刻", 56: "一色三順", 57: "一筒摸月", 58: "九筒撈魚",
  59: "人和", 60: "大車輪", 61: "大竹林", 62: "大数隣", 63: "石の上にも三年", 64: "大七星",
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
  10: "六倍役满",
  11: "累计役满",
};

function validYakuFans(event) {
  const fans = Array.isArray(event.fans) ? event.fans : [];
  return fans.filter((fan) => fan && Number.isFinite(Number(fan.val)) && Number(fan.val) > 0);
}

function yakuName(fan) {
  const knownName = Object.prototype.hasOwnProperty.call(MAJSOUL_YAKU_NAMES, fan.id) ? MAJSOUL_YAKU_NAMES[fan.id] : null;
  return fan.name || knownName || `未知役种（ID ${fan.id ?? "?"}）`;
}

function yakumanTitle(event, fans = validYakuFans(event)) {
  // Mahjong Soul count (converted to han) is the authoritative total when
  // yiman=true. A title supplies the total if absent; fans may be incomplete.
  const rawTotal = event.count ?? event.han;
  const total = typeof rawTotal === "number" ? rawTotal : 0;
  const titleId = event.title_id;
  const byTitle = Number.isInteger(titleId) && titleId >= 5 && titleId <= 10 ? titleId - 4 : 0;
  const byFans = fans.reduce((sum, fan) => sum + Number(fan.val), 0);
  const multiple = (Number.isInteger(total) && total > 0 ? total : 0) || byTitle || byFans || 1;
  return multiple > 1 ? `${multiple}倍役满` : "役满";
}

function correctedRoundSummaries(kyoku) {
  const summaries = kyoku.end_summaries || [];
  const endings = (kyoku.steps || []).map((step) => step.event).filter((event) =>
    event && (event.type === "hora" || event.type === "ryukyoku" || event.type === "ryuukyoku"));
  return summaries.map((summary, i) => {
    const event = endings[i];
    const text = sanitizeSummaryText(summary, event, kyoku);
    if (event?.type !== "hora") return text;
    const legacyScore = /(^|\s)\d+符\d+番(?=\s|$)/;
    if (event.yiman !== true) {
      // Counted yakuman retains its ordinary fu/han and fan values.
      if (event.title_id !== 11 || text.includes("累计役满")) return text;
      if (legacyScore.test(text)) return text.replace(legacyScore, (match, prefix) => `${prefix}累计役满 ${match.trim()}`);
      return text.replace(/(\s+[+-]\d+)?\s*$/, (_, points = "") => ` 累计役满${points}`);
    }
    const fans = validYakuFans(event);
    const names = fans.map(yakuName);
    const score = yakumanTitle(event, fans) + (names.length ? `（${names.join("、")}）` : "");
    // Older review data stored yiman count as fu/han. Preserve the
    // original winner, target and delta while replacing only that score field.
    if (legacyScore.test(text)) return text.replace(legacyScore, (_, prefix) => `${prefix}${score}`);
    if (text.includes("役满")) return text;
    return text.replace(/(\s+[+-]\d+)?\s*$/, (_, points = "") => ` ${score}${points}`);
  });
}

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
    kan: "杠（具体牌未判定）",
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
  const finiteNumber = (value) => {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value) && Number.isFinite(Number(value))) return Number(value);
    return null;
  };
  const eventDelta = (seat) => finiteNumber(snap?.event?.deltas?.[seat]);
  const stateCount = (value) => {
    const count = finiteNumber(value);
    return Number.isInteger(count) && count > 0 ? count : 0;
  };
  const honbaCount = stateCount(snap?.honba ?? currentKyoku?.honba);
  const kyotakuCount = stateCount(snap?.kyotaku ?? currentKyoku?.kyotaku);
  const roundState = [honbaCount ? `${honbaCount}本场` : "", kyotakuCount ? `立直供托 ${kyotakuCount}根` : ""].filter(Boolean);
  const roundStateHtml = roundState.length ? `<tr style="border-top:1px solid rgba(255,255,255,0.06);">
    <td colspan="2" style="padding:5px 8px; color:rgba(255,255,255,0.65);">本局状态：${roundState.join(" · ")}</td>
  </tr>` : "";

  let html = "";
  if (isHora) {
    const winActor = snap.event.actor;
    const tgtActor = snap.event.target;
    const isRon = winActor !== tgtActor && tgtActor !== undefined && tgtActor !== null;
    const isHeroWin = winActor === targetActor;
    const isHeroLoser = isRon && tgtActor === targetActor;
    const horaEvents = (currentKyoku?.steps || []).filter((step) => step?.event?.type === "hora");
    // Legacy caches can retain both win summaries but omit a later step.
    const isMultiHora = horaEvents.length > 1 || (currentKyoku?.end_summaries?.length ?? 0) > 1;
    const hasEventDeltas = [0, 1, 2, 3].some((seat) => eventDelta(seat) !== null);

    const relativeSeatName = (seat) => {
      if (!Number.isInteger(seat) || seat < 0 || seat > 3) return "未知座位";
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
    const event = snap.event;
    const han = finiteNumber(event.han ?? event.count);
    const fu = finiteNumber(event.fu);
    const titleId = finiteNumber(event.title_id) ?? 0;
    const isYakuman = event.yiman === true;
    const validFans = validYakuFans(event);
    let titleName = Object.prototype.hasOwnProperty.call(MAJSOUL_TITLES, titleId) ? MAJSOUL_TITLES[titleId] : null;
    if (isYakuman) {
      titleName = yakumanTitle(event, validFans);
    } else if (!titleName && han !== null) {
      if (han >= 13) titleName = "役满";
      else if (han >= 11) titleName = "三倍满";
      else if (han >= 8) titleName = "倍满";
      else if (han >= 6) titleName = "跳满";
      else if (han === 5 || (han === 4 && fu >= 40) || (han === 3 && fu >= 70)) titleName = "满贯";
    }

    // Whole-round deltas also include riichi deposits and possibly other wins;
    // only an explicitly recorded event delta is a settlement amount.
    const scoreDelta = eventDelta(winActor);
    const fuHanText = isYakuman ? "" : [fu ? `${fu} 符` : "", han ? `${han} 番` : ""].filter(Boolean).join(" ");
    const scoreText = !isMultiHora && scoreDelta > 0 ? `${scoreDelta} 点` : "";

    html += `<div style="font-size:14px; font-weight:600; color:#ffcc00; display:flex; align-items:center; justify-content:center; gap:8px;">
      ${titleName ? `<span class="kd-settle-tag" style="background:rgba(255,204,0,0.18); border:1px solid rgba(255,204,0,0.35); padding:1px 6px; border-radius:4px; font-size:12px; color:#ffcc00; font-weight:700;">${titleName}</span>` : ""}
      <span>${fuHanText}${fuHanText && scoreText ? " · " : ""}${scoreText}</span>
    </div></div>`;

    // 3. Yaku List Breakdown
    if (validFans.length > 0) {
      html += `<div class="kd-settle-yaku-list" style="margin:10px 0 12px; border-top:1px solid rgba(255,255,255,0.08); border-bottom:1px solid rgba(255,255,255,0.08); padding:6px 2px;">`;
      for (const f of validFans) {
        const name = yakuName(f);
        const value = Number(f.val);
        const valueText = isYakuman ? (value === 1 ? "役满" : `${value}倍役满`) : `${value} 番`;
        html += `<div style="display:flex; justify-content:space-between; align-items:center; font-size:12.5px; color:rgba(255,255,255,0.85); padding:2.5px 4px;">
          <span>${escapeHtml(name)}</span>
          <span style="font-family:monospace; color:#ffd60a; font-weight:600;">${valueText}</span>
        </div>`;
      }
      html += `</div>`;
    }

    // Some sources attach only the aggregate settlement to the first win.
    // When multiple wins exist, do not present a winner's aggregate component
    // as an individual award, even if another source supplies separate deltas.
    if (isMultiHora) {
      html += `<div style="font-size:12px; color:rgba(255,255,255,0.7);">${hasEventDeltas ? "多家荣和：事件点数变化（可能为多家结算合计）" : "多家荣和：本次事件未提供点数变化"}</div>`;
    } else if (!hasEventDeltas) {
      html += `<div style="font-size:12px; color:rgba(255,255,255,0.7);">本次事件未提供点数变化</div>`;
    }

    // 4. 4-Seat Delta Table
    html += `<table class="kd-settle-delta-table" style="width:100%; border-collapse:collapse; margin-top:6px; font-size:13px;">
      <tbody>`;
    for (let diff = 0; diff < 4; diff++) {
      const seat = (targetActor + diff) % 4;
      const seatLabel = ["自家", "下家", "对家", "上家"][diff];
      const delta = eventDelta(seat);
      let deltaStr = delta === null ? "—" : String(delta);
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
    html += `${roundStateHtml}</tbody></table>`;
  } else if (isRyuukyoku) {
    const reason = snap?.event?.majsoul?.reason;
    const drawTitles = {
      exhaustive: "荒牌流局", nagashi_mangan: "流局满贯", kyushu_kyuhai: "九种九牌",
      suufon_renda: "四风连打", suukan_sanra: "四杠散了", suucha_riichi: "四家立直",
    };
    const drawTitle = Object.prototype.hasOwnProperty.call(drawTitles, reason) ? drawTitles[reason] : "流局";
    html += `<div class="kd-settle-header" style="text-align:center; margin-bottom:12px; padding-top:4px;">
      <div style="font-size:16px; font-weight:700; color:#ff9f0a; margin-bottom:4px; letter-spacing:0.3px;">${drawTitle}</div>`;

    const pureDeltas = [0, 1, 2, 3].map(eventDelta);
    const hasAllDeltas = pureDeltas.every((delta) => delta !== null);
    const tenpaiSeats = [0, 1, 2, 3].filter((s) => pureDeltas[s] > 0);
    const summary = !hasAllDeltas ? "点数变化未提供" : reason === "exhaustive"
      ? (tenpaiSeats.length > 0 ? "罚符结算" : "无罚符结算") : "点数变化";
    html += `<div style="font-size:13px; color:rgba(255,255,255,0.7);">${summary}</div></div>`;

    html += `<table class="kd-settle-delta-table" style="width:100%; border-collapse:collapse; margin-top:6px; font-size:13px;">
      <tbody>`;
    for (let diff = 0; diff < 4; diff++) {
      const seat = (targetActor + diff) % 4;
      const seatLabel = ["自家", "下家", "对家", "上家"][diff];
      const delta = pureDeltas[seat];
      let deltaStr = delta === null ? "—" : String(delta);
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
    html += `${roundStateHtml}</tbody></table>`;
  }

  contentEl.innerHTML = html;
  const modal = $("infoThisRoundModal");
  if (modal) {
    // Native dialogs are hidden by their open state, not the .hidden class.
    // Clear any stale class before entering the top layer with showModal().
    modal.classList.remove("hidden");
    if (!modal.open) {
      modal.dataset.openedAt = String(Date.now());
      if (typeof modal.showModal === "function") {
        modal.showModal();
      } else {
        modal.setAttribute("open", "");
      }
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
  if (roundModal?.open) closeModal("infoThisRoundModal");

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
