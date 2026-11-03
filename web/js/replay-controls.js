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

function setStep(stepIdx) {
  const res = state.reviewData;
  if (!res) return;
  const kyoku = res.kyokus[state.kyokuIdx];
  if (!kyoku || !kyoku.steps.length) return;

  const clamped = Math.max(0, Math.min(kyoku.steps.length - 1, stepIdx));
  state.stepIdx = clamped;

  const snap = kyoku.steps[clamped];

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
