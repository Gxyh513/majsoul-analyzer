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
  if (!kyoku) return;
  const current = pendingTargetStep !== null ? pendingTargetStep : state.stepIdx;
  const decs = kyoku.decisions || [];
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < current && decs[i].is_equal === false) {
      scheduleStep(decs[i].step_idx);
      return;
    }
  }
  cancelScheduledStep();
  jumpPrevBlunder();
}

function jumpNextBlunderFast() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return;
  const current = pendingTargetStep !== null ? pendingTargetStep : state.stepIdx;
  for (const d of kyoku.decisions || []) {
    if (d.step_idx > current && d.is_equal === false) {
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
      selectKyoku(state.kyokuIdx + 1);
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
