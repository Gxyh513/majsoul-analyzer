// ============================================================================
// Initialization
// ============================================================================
window.addEventListener("DOMContentLoaded", async () => {
  document.documentElement.removeAttribute("data-theme");
  try {
    localStorage.removeItem("majsoul_theme_pref");
    localStorage.removeItem("majsoul_back_pref");
  } catch (_) {}
  applyHidePlayerId();
  bindEvents();

  // Reloading an analysis page always returns to the landing screen. An old
  // ?paipu= link must never trigger a network request or a new review.
  // Keep unrelated query parameters (for example backend selection) intact.
  const url = new URL(window.location.href);
  if (url.searchParams.has("paipu")) {
    url.searchParams.delete("paipu");
    window.history.replaceState(null, "", url.toString());
  }
  showTier(1);
  await refreshServerStatus();
});
