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
          ${honbaCount > 0 ? `<span class="info-tag-honba">${honbaCount}本</span>` : ""}
          ${kyotakuCount > 0 ? `<span class="info-tag-kyotaku"><span class="riichi-stick-mini"></span>${kyotakuCount}</span>` : ""}
        </span>
      `;
      const roundDescription = `${roundName}${honbaCount > 0 ? ` ${honbaCount}本场` : ""}${kyotakuCount > 0 ? ` · 立直供托 ${kyotakuCount}根 (${kyotakuCount * 1000}点)` : ""} · 点击查看局数得点表`;
      btnRound.title = roundDescription;
      btnRound.setAttribute("aria-label", roundDescription);
    } else {
      btnRound.innerHTML = `<span class="info-round-title only">${escapeHtml(roundName)}</span>`;
      btnRound.title = `${roundName} · 点击查看局数得点表`;
      btnRound.setAttribute("aria-label", btnRound.title);
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

  // A ron can claim a discard or rob a kan. Inspect the actual preceding
  // action rather than assuming the target's most recent river tile was won.
  const ronSource = isRon ? findRonSource(kyoku, snap, targetActor) : null;
  const robbingKan = isRon && (["ankan", "kakan"].includes(ronSource?.event?.type) || ronSource?.robbingKan || (Array.isArray(snap.event.fans) && snap.event.fans.some((fan) =>
    Number(fan?.id) === 3 || fan?.name === "抢杠")));
  const river = isRon ? (snap.rivers?.[targetActor] || []) : [];
  const lastRiverTile = river[river.length - 1];
  const sourceMatchesTile = ronSource?.event?.pai == null || ronSource.event.pai === lastRiverTile?.pai;
  const winMatchesTile = snap.event?.pai == null || snap.event.pai === lastRiverTile?.pai;
  const identifiedDiscard = lastRiverTile?.step != null
    ? Number.isInteger(lastRiverTile.step) && lastRiverTile.step === ronSource?.index
    : ronSource?.event?.pai != null && ronSource.event.pai === lastRiverTile?.pai;
  const isRonDiscard = isRon && !robbingKan && ronSource?.event?.type === "dahai" &&
    lastRiverTile && !lastRiverTile.called && sourceMatchesTile && winMatchesTile && identifiedDiscard;

  let agariTile = null;
  if (isHora) {
    if (isRon) {
      // A missing kan cannot be inferred from an unrelated discard.
      const sourceEvent = ronSource?.event;
      const sourceTile = sourceEvent?.type === "ankan" ? sourceEvent.consumed?.[0] :
        robbingKan && sourceEvent?.type !== "kakan" ? null : sourceEvent?.pai;
      agariTile = snap.event.pai || sourceTile || null;
    } else if (winActor !== null) {
      agariTile = snap.drawn?.[winActor] || null;
    }
  }

  // 4 Seat positions relative to hero
  const p1 = (hero + 1) % 4; // Right
  const p2 = (hero + 2) % 4; // Top
  const p3 = (hero + 3) % 4; // Left

  const roundScores = snap.scores || kyoku.init_scores || kyoku.steps?.[0]?.scores || [25000, 25000, 25000, 25000];
  const giP0 = $("giP0");
  if (giP0) giP0.innerHTML = `<span class="gi-wind">${handWind(hero, kyoku)}</span><span class="gi-score">${roundScores[hero] ?? 25000}</span>`;
  const giP1 = $("giP1");
  if (giP1) giP1.innerHTML = `<span class="gi-wind">${handWind(p1, kyoku)}</span><span class="gi-score">${roundScores[p1] ?? 25000}</span>`;
  const giP2 = $("giP2");
  if (giP2) giP2.innerHTML = `<span class="gi-wind">${handWind(p2, kyoku)}</span><span class="gi-score">${roundScores[p2] ?? 25000}</span>`;
  const giP3 = $("giP3");
  if (giP3) giP3.innerHTML = `<span class="gi-wind">${handWind(p3, kyoku)}</span><span class="gi-score">${roundScores[p3] ?? 25000}</span>`;

  // Rivers
  renderPlayerRiver("Bottom", hero, snap, isRonDiscard, targetActor);
  renderPlayerRiver("Right", p1, snap, isRonDiscard, targetActor);
  renderPlayerRiver("Top", p2, snap, isRonDiscard, targetActor);
  renderPlayerRiver("Left", p3, snap, isRonDiscard, targetActor);

  // Hands
  const nextSnap = kyoku.steps?.[state.stepIdx + 1] || null;
  let nextHeroSnap = nextSnap;
  // A riichi discard can have its own intermediate replay snapshot. Look past
  // that declaration, but never past an unrelated action or across rounds.
  if (activeDecision?.actual?.type === "reach" && nextSnap?.event?.type === "reach" && nextSnap.event.actor === hero) {
    nextHeroSnap = kyoku.steps?.[state.stepIdx + 2] || null;
  }
  renderHeroHand(hero, snap, activeDecision, agariTile, winActor, isRon, nextHeroSnap);
  renderOpponentHand("Right", p1, snap, agariTile, winActor, isRon, nextSnap);
  renderOpponentHand("Top", p2, snap, agariTile, winActor, isRon, nextSnap);
  renderOpponentHand("Left", p3, snap, agariTile, winActor, isRon, nextSnap);
}

function findRonSource(kyoku, snap, targetActor) {
  const steps = kyoku?.steps;
  if (!Array.isArray(steps)) return null;
  const currentIndex = steps.indexOf(snap);
  if (currentIndex < 0) return null;
  let robbingKan = false;
  for (let index = currentIndex - 1; index >= 0; index--) {
    const event = steps[index]?.event;
    if (!event?.type) return null;
    // Consecutive wins on the same target share one discard or added kan tile.
    if (event.type === "hora" && event.target === targetActor && event.actor !== targetActor) {
      robbingKan ||= Array.isArray(event.fans) && event.fans.some((fan) =>
        Number(fan?.id) === 3 || fan?.name === "抢杠");
      continue;
    }
    if (["dora", "reach_accepted", "none", "pass"].includes(event.type)) continue;
    if (["dahai", "kakan", "ankan"].includes(event.type) && event.actor === targetActor) {
      return { event, index, robbingKan };
    }
    // A draw, call, different player's discard or incomplete history breaks
    // the causal chain; a matching older tile is not sufficient evidence.
    return null;
  }
  return null;
}

function renderPlayerRiver(pos, seat, snap, isRon = false, targetActor = null) {
  const container = $(`river${pos}`);
  if (!container) return;
  const fullRiver = snap.rivers?.[seat] || [];
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
  const actual = activeDecision?.actual;
  const actualPai = actual?.pai || null;
  const isAnkanDec = actual?.type === "ankan" || activeDecision?.decision_type === "ankan";
  const ankanTile = actual?.consumed?.[0];

  // Highlight intended discard or ankan tiles in hand during draw/decision step or after call
  const highlightedIdxs = new Set();
  if (activeDecision) {
    if (isAnkanDec && ankanTile) {
      // Red fives are distinct physical tiles but the same kind for an ankan.
      const tileKind = (pai) => typeof pai === "string" ? pai.replace(/^5([mps])r$/, "5$1") : pai;
      allTiles.forEach(({ pai }, idx) => {
        if (tileKind(pai) === tileKind(ankanTile)) highlightedIdxs.add(idx);
      });
    } else if (actualPai && (actual?.type === "dahai" || actual?.type === "reach")) {
      // Equal tile values can occur both in the closed hand and in the draw.
      // Only the replay's tsumogiri flag identifies which physical tile was cut.
      const nextEvent = nextSnap?.event;
      const nextDiscard = nextEvent?.type === "dahai" && nextEvent.actor === seat && nextEvent.pai === actualPai
        ? nextEvent : null;
      const tsumogiri = actual?.type === "dahai" ? (actual.tsumogiri ?? nextDiscard?.tsumogiri)
        : actual?.type === "reach" ? nextDiscard?.tsumogiri : undefined;
      if (tsumogiri === true) {
        if (drawn === actualPai) highlightedIdxs.add(hand.length);
      } else {
        const handIdx = hand.indexOf(actualPai);
        if (handIdx >= 0) highlightedIdxs.add(handIdx);
        else if (tsumogiri !== false && drawn === actualPai) highlightedIdxs.add(hand.length);
      }
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

  if (isRon && seat === winActor && agariTile) {
    handHtml += `<div class="tileDiv is-drawn agari-tile" data-pai="${escapeHtml(agariTile)}" title="荣和: ${escapeHtml(tileCn(agariTile))}"><img src="/tiles/${encodeURIComponent(agariTile)}.svg" class="tileImg agari-tile"></div>`;
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

      const barAreaHeight = svgRect.height || 50;
      const barScale = barAreaHeight / 50;
      const barWidth = 9 * barScale;
      if (svgRect.width > 0) {
        // Use the rendered SVG height as well as its width so scaling the
        // board does not shift bars away from the DOM-measured tiles.
        svgEl.setAttribute("viewBox", `0 0 ${svgRect.width} ${barAreaHeight}`);
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
            const height = Math.max(3 * barScale, (prob / 100) * barAreaHeight * 0.9);
            const y = barAreaHeight - height;
            svgHtml += `<rect x="${centerX - barWidth / 2}" y="${y}" width="${barWidth}" height="${height}" fill="#5ecb7d" rx="${2 * barScale}"></rect>`;
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

  if (isRon && isWinner && agariTile) {
    const src = `/tiles/${encodeURIComponent(agariTile)}.svg`;
    html += `<div class="tileDiv is-drawn agari-tile"><img src="${src}" class="tileImg agari-tile" title="荣和: ${escapeHtml(tileCn(agariTile))}"></div>`;
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
    const pai = m.pai ?? m.tiles?.[0];

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

    // Current records store only hand tiles in consumed; older caches may
    // instead have tiles=[called,...consumed,added]. Do not duplicate the
    // called or added tile when recovering from that older representation.
    const handTileCount = type === "daiminkan" ? 3 : 2;
    const storedTiles = Array.isArray(m.tiles) ? m.tiles : [];
    let consumed = Array.isArray(m.consumed) ? m.consumed : [];
    if (consumed.length !== handTileCount && storedTiles.length >= handTileCount + 1) {
      const remaining = [...storedTiles];
      const calledIndex = remaining.indexOf(pai);
      if (calledIndex >= 0) remaining.splice(calledIndex, 1);
      if (type === "kakan" && remaining.length > handTileCount) remaining.pop();
      consumed = remaining.slice(0, handTileCount);
    } else {
      consumed = consumed.slice(0, handTileCount);
    }
    const allTiles = [...consumed];
    // A four-tile open kan places the right-hand discard at the far end.
    const calledIndex = type === "daiminkan" && fromIdxRel === 2 ? 3 : fromIdxRel;
    allTiles.splice(calledIndex, 0, pai);
    if (type === "kakan") {
      // Keep the actual added tile (including red five) in the stacked slot.
      const added = m.added ?? (storedTiles.length >= 4 ? storedTiles[3] : pai);
      allTiles.splice(calledIndex + 1, 0, added);
    }

    for (let i = 0; i < allTiles.length; i++) {
      const tile = allTiles[i];
      const isFloated = type === "kakan" && i === calledIndex + 1;
      const isRotated = i === calledIndex || isFloated;

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
