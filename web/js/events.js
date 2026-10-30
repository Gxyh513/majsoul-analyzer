// ============================================================================
// Event Bindings
// ============================================================================
function bindEvents() {
  // Local File Dropzone & File Input
  initLocalFileDropzone();

  $("landingModelCard")?.addEventListener("click", async () => {
    openModal("engineModal");
    await refreshServerStatus();
  });

  // Tier 1 Submit
  $("btnHomeAnalyze")?.addEventListener("click", () => {
    const val = $("homePaipuInput")?.value.trim();
    startAnalysisFromInput(val);
  });
  $("btnDemoAnalyze")?.addEventListener("click", () => startAnalysisFromInput("demo"));
  $("homePaipuInput")?.addEventListener("input", () => $("homeInputError")?.classList.add("hidden"));
  $("homePaipuInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const val = $("homePaipuInput")?.value.trim();
      startAnalysisFromInput(val);
    }
  });

  // Tier 2 Back to Home
  $("btnBackToHome")?.addEventListener("click", () => {
    ++analysisViewVersion;
    setLoading(false);
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

  // Engine modal
  $("btnOpenEngineModal")?.addEventListener("click", () => {
    const statusMsg = $("engineModalStatusMsg");
    if (statusMsg) statusMsg.textContent = "";
    openModal("engineModal");
  });
  $("engineModalClose")?.addEventListener("click", () => closeModal("engineModal"));
  $("btnCancelEngineModal")?.addEventListener("click", () => closeModal("engineModal"));
  $("btnRetryModelDownload")?.addEventListener("click", async (e) => {
    const button = e.currentTarget;
    const statusMsg = $("engineModalStatusMsg");
    button.disabled = true;
    if (statusMsg) statusMsg.textContent = "正在请求下载模型...";
    try {
      await apiFetch("/api/engine/download", { method: "POST" });
      if (statusMsg) statusMsg.textContent = "已请求下载，请稍后重新打开查看状态。";
      await refreshServerStatus();
    } catch (err) {
      if (statusMsg) statusMsg.textContent = `下载请求失败：${err.message || "请求异常"}`;
    } finally {
      updateTopbarStatus(state.serverStatus || {});
    }
  });

  // Shortcuts Modal bindings
  $("btnOpenShortcutsModal")?.addEventListener("click", () => openModal("shortcutsModal"));
  $("shortcutsModalClose")?.addEventListener("click", () => closeModal("shortcutsModal"));

  // Close native dialogs through close(), and only hide the custom modals.
  document.querySelectorAll(".modal-backdrop").forEach((bd) => {
    bd.addEventListener("click", closeAllModals);
  });

  // Global Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.defaultPrevented) return;
    if (e.key === "Escape") {
      closeAllModals();
      return;
    }
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
window.addEventListener("resize", handleBoardResize);
