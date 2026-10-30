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
  const hasMatchCounts = Number.isSafeInteger(sum.total_matches) && sum.total_matches >= 0 &&
    Number.isSafeInteger(sum.total_reviewed) && sum.total_reviewed >= sum.total_matches;
  const pill = $("gameSummaryPill");
  if (pill) {
    pill.innerHTML = `
      <div class="kd-badge-item">一致率: <strong>${fmtPct(sum.match_rate)}${hasMatchCounts ? `（${sum.total_matches} / ${sum.total_reviewed}）` : ""}</strong></div>
      <div class="kd-badge-item">恶手: <strong style="color:#ff453a;">${sum.blunders || 0}</strong></div>
      <div class="kd-badge-item">Rating: <strong style="color:#ffcc00;">${sum.rating == null ? "--" : Number(sum.rating).toFixed(1)}</strong></div>
      ${sum.unscored_kan ? `<div class="kd-badge-item">未判定杠: <strong>${Number(sum.unscored_kan)}</strong></div>` : ""}
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
