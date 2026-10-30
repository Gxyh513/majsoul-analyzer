// ============================================================================
// Initialization
// ============================================================================
window.addEventListener("DOMContentLoaded", async () => {
  updateResponsiveZoom();
  document.documentElement.removeAttribute("data-theme");
  try {
    localStorage.removeItem("majsoul_theme_pref");
    localStorage.removeItem("majsoul_back_pref");
  } catch (_) {}
  applyHidePlayerId();
  updateSoundButton();
  const initSpeedSel = $("playSpeedSelect");
  if (initSpeedSel) initSpeedSel.value = String(state.playSpeed || "1");
  bindEvents();
  await refreshServerStatus();

  // Check URL query for paipu parameter
  const urlParams = new URLSearchParams(window.location.search);
  const paipuParam = urlParams.get("paipu");
  if (paipuParam) {
    const input = $("homePaipuInput");
    if (input) input.value = paipuParam;
    await startAnalysisFromInput(paipuParam, "akagi", null);
  } else {
    const input = $("homePaipuInput");
    if (input) input.value = "";
    showTier(1);
  }
});
