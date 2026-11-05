// ============================================================================
// Event Bindings
// ============================================================================
function bindEvents() {
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

  $("btnCancelAnalysis")?.addEventListener("click", () => cancelAnalysisWait());

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

  // Engine modal
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

  // Close native dialogs through close(), and only hide the custom modals.
  document.querySelectorAll(".modal-backdrop").forEach((bd) => {
    bd.addEventListener("click", closeAllModals);
  });

  // Waiting can be cancelled even before the replay modules finish loading.
  window.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.key !== "Escape") return;
    if (activeViewController) {
      e.preventDefault();
      cancelAnalysisWait();
    } else {
      closeAllModals();
    }
  });
}

// Shared modal primitives stay available on the landing screen.
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
