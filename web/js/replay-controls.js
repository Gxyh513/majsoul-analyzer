// RAF-based Step Debouncing & Scheduler to eliminate layout thrashing on key-hold
let pendingTargetStep = null;
let stepRafId = null;
let stepNavigationVersion = 0;

function scheduleStep(targetStep) {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku || !kyoku.steps.length) return;
  pendingTargetStep = Math.max(0, Math.min(kyoku.steps.length - 1, targetStep));

  if (stepRafId !== null) return;

  const version = stepNavigationVersion;
  stepRafId = requestAnimationFrame(() => {
    if (version !== stepNavigationVersion) return;
    stepRafId = null;
    if (pendingTargetStep !== null) {
      const step = pendingTargetStep;
      pendingTargetStep = null;
      setStep(step);
    }
  });
}

function cancelScheduledStep() {
  ++stepNavigationVersion;
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
  const speed = state.playSpeed || DEFAULT_PLAYBACK_SPEED;
  // 10x is 80 ms per replay step; the previous 150 ms floor capped playback below 6x.
  return Math.max(40, Math.round(800 / speed));
}

function setPlaySpeed(speed) {
  const num = parseFloat(speed);
  if (!PLAYBACK_SPEEDS.includes(num)) return;
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
    setStep(state.stepIdx, { renderOnly: true });
  }
}

function setStep(stepIdx, { renderOnly = false } = {}) {
  // An explicit navigation supersedes queued input; a repaint must not drop it.
  if (!renderOnly) cancelScheduledStep();
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

// Replay bindings are installed once, after every replay script and stylesheet loads.
function bindReplayEvents() {
  // Tier 2 Back to Home
  $("btnBackToHome")?.addEventListener("click", () => {
    cancelAnalysisWait(false);
    showTier(1);
    const input = $("homePaipuInput");
    if (input) {
      input.value = "";
      input.focus();
    }
  });

  // Play/Pause Auto Step
  $("btnPlayPause")?.addEventListener("click", () => togglePlay());

  // Playback Speed Selector
  const speedSel = $("playSpeedSelect");
  if (speedSel) {
    speedSel.value = String(state.playSpeed || DEFAULT_PLAYBACK_SPEED);
    speedSel.addEventListener("change", (e) => {
      setPlaySpeed(e.target.value);
    });
  }

  // Global robust delegation for clicking anywhere on opponent hands / tiles / back tiles:
  document.addEventListener("click", (e) => {
    if (e.target.closest("button, a, select, input")) return;
    // 如果点击的是自家（p0/底端）手牌或相关区域，坚决不触发别家明牌/暗牌切换！
    if (e.target.closest(".grid-hand-p0-container, #handBottomClosed, .hand-closed-p0, .grid-hand-p0")) return;
    // 只有点击其他三家（p1, p2, p3）的手牌区域时，才执行 toggleHandsVisibility()
    const otherHandTarget = e.target.closest(".grid-hand-p1, .grid-hand-p2, .grid-hand-p3, #handRightClosed, #handTopClosed, #handLeftClosed, .hand-closed-p1, .hand-closed-p2, .hand-closed-p3");
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
  bindNavClick(["btnPrevBlunder"], jumpPrevBlunder);
  bindNavClick(["btnNextBlunder"], jumpNextBlunder);
  bindNavClick(["btnPrevDec"], jumpPrevDecision);
  bindNavClick(["btnNextDec"], jumpNextDecision);
  bindNavClick(["btnPrevStep"], () => stepDelta(-1));
  bindNavClick(["btnNextStep"], () => stepDelta(1));

  // Round settlement & score history modals
  $("infoThisRoundClose")?.addEventListener("click", () => closeModal("infoThisRoundModal"));
  $("infoThisRoundModal")?.addEventListener("click", (e) => {
    const dialog = $("infoThisRoundModal");
    if (!dialog || e.target !== dialog || e.detail === 0) return;
    // Native ::backdrop clicks are retargeted to the dialog. Its own padding
    // has the same target, so only a pointer outside its box dismisses it.
    const rect = dialog.getBoundingClientRect();
    if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {
      closeModal("infoThisRoundModal");
    }
  });
  $("btnRoundScore")?.addEventListener("click", openRoundScoresModal);
  $("roundModalClose")?.addEventListener("click", () => closeModal("roundModal"));

  // Shortcuts Modal bindings
  $("btnOpenShortcutsModal")?.addEventListener("click", () => openModal("shortcutsModal"));
  $("shortcutsModalClose")?.addEventListener("click", () => closeModal("shortcutsModal"));

  // Global Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.defaultPrevented) return;
    if (e.key === "Escape") return;
    // Never capture browser or operating-system modifier shortcuts.
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    // Replay shortcuts must not operate hidden replay state on the landing page.
    const replayScreen = $("tier2Screen");
    if (!state.reviewData || !replayScreen || replayScreen.classList.contains("hidden")) return;

    const activeEl = document.activeElement;
    const isTextEntry = activeEl && (activeEl.tagName === "INPUT" || activeEl.tagName === "TEXTAREA" || activeEl.tagName === "SELECT" || activeEl.isContentEditable);
    const scoreModal = $("roundModal");
    // The score sheet focuses its close button when opened. S must still close
    // that sheet, but never steal a letter from an editable or selection field.
    if (!isTextEntry && (e.key === "s" || e.key === "S") && scoreModal && !scoreModal.classList.contains("hidden")) {
      e.preventDefault();
      closeModal("roundModal");
      return;
    }
    const shortcutsModal = $("shortcutsModal");
    const isHelpKey = e.key === "?" || (e.shiftKey && (e.key === "/" || e.code === "Slash"));
    // The help sheet focuses its close button. Its own toggle must still work,
    // without stealing a question mark from an editable or selection field.
    if (!isTextEntry && isHelpKey && shortcutsModal && !shortcutsModal.classList.contains("hidden")) {
      e.preventDefault();
      closeModal("shortcutsModal");
      return;
    }
    const isInteractive = activeEl && (isTextEntry || activeEl.tagName === "BUTTON" || activeEl.tagName === "A" ||
      Boolean(activeEl.closest?.('button, a[href], select, input, textarea, [contenteditable=""], [contenteditable="true"], [role="button"], [role="slider"], [role="textbox"], [role="combobox"]')));
    if (isInteractive) return;

    // Preserve replay navigation from the page background when a settlement is open.
    const roundDialog = $("infoThisRoundModal");
    if (roundDialog && roundDialog.open) {
      if (e.key === "Enter" || e.key === " " || e.key.startsWith("Arrow") || e.key === "[" || e.key === "]" || e.key === "," || e.key === ".") {
        closeModal("infoThisRoundModal");
        if (e.key === "Enter") {
          e.preventDefault();
          return;
        }
      }
    }

    // Toggle shortcuts help modal with '?'
    if (isHelpKey) {
      e.preventDefault();
      if (shortcutsModal?.classList.contains("hidden")) openModal("shortcutsModal");
      else closeModal("shortcutsModal");
      return;
    }

    // If modal is open, don't execute board navigation shortcuts
    const isModalOpen = Array.from(document.querySelectorAll(".modal")).some(m => !m.classList.contains("hidden") && (m.tagName !== "DIALOG" || m.open)) || !!document.querySelector("dialog[open]");
    if (isModalOpen) return;

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
      s: openRoundScoresModal,
      S: openRoundScoresModal,

      " ": togglePlay,
    };

    if (actions[e.key]) {
      e.preventDefault();
      actions[e.key]();
    }
  });

  // Only the board captures ordinary wheel events; the rest of the page scrolls normally.
  window.addEventListener("wheel", (e) => {
    const replayScreen = $("tier2Screen");
    if (!state.reviewData || !replayScreen || replayScreen.classList.contains("hidden")) return;
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !e.deltaY) return;
    const target = e.target;
    if (!target?.closest?.("#gridMain")) return;
    if (target.closest('#roundModal, #shortcutsModal, #engineModal, #infoThisRoundModal, input, textarea, select, button, a[href], [contenteditable]:not([contenteditable="false"]), [role="button"], [role="slider"], [role="textbox"], [role="combobox"]')) return;
    const otherModalOpen = Array.from(document.querySelectorAll(".modal")).some(modal =>
      modal.id !== "infoThisRoundModal" && !modal.classList.contains("hidden") && (modal.tagName !== "DIALOG" || modal.open));
    if (otherModalOpen) return;

    const roundDialog = $("infoThisRoundModal");
    if (roundDialog && roundDialog.open) {
      const openedAt = Number(roundDialog.dataset.openedAt || 0);
      if (Date.now() - openedAt < 300) {
        e.preventDefault();
        return;
      }
      closeModal("infoThisRoundModal");
    }

    e.preventDefault();
    if (e.deltaY > 0) stepDelta(1);
    else if (e.deltaY < 0) stepDelta(-1);
  }, { passive: false });

  updateResponsiveZoom();
  window.addEventListener("resize", handleBoardResize);
}

function updateResponsiveZoom() {
  const root = document.documentElement;
  // Read the root layout viewport after resize, excluding classic scrollbars.
  // The body width may be affected by the previous board scale, so never use it.
  const viewportWidth = root.clientWidth > 0 ? Math.min(window.innerWidth, root.clientWidth) : window.innerWidth;
  const horizontalPadding = window.innerWidth <= 768 ? 8 : window.innerWidth <= 960 ? 16 : 24;
  const zoom = Math.min(1, Math.max(0, (viewportWidth - horizontalPadding) / 655));
  root.style.setProperty("--zoom", String(zoom));
}
let resizeRafId = null;
function handleBoardResize() {
  if (resizeRafId !== null) return;
  resizeRafId = requestAnimationFrame(() => {
    resizeRafId = null;
    // Resize can fire before the root's layout viewport has caught up with an
    // orientation change; measure it here rather than in the resize event.
    updateResponsiveZoom();
    const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
    const snap = kyoku?.steps?.[state.stepIdx];
    if (!snap) return;
    const targetActor = state.targetActor ?? 0;
    const isHeroDahai = snap.event?.type === "dahai" && snap.event.actor === targetActor;
    const decision = snap.decision_idx != null && !isHeroDahai ? kyoku.decisions?.[snap.decision_idx] : null;
    // Re-measure tiles after the zoom and layout change without calling setStep:
    // resize must not navigate or reopen a dismissed settlement dialog.
    renderBoard(kyoku, snap, decision);
  });
}
