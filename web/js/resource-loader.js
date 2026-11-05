// Single-flight resource loads, with retryable failures and stable stylesheet order.
const clientResourceLoads = new Map();
let replayAssetsLoader = null;

function loadClientResource(url, kind, isReady = null, ordered = false) {
  const previous = clientResourceLoads.get(url);
  if (previous?.promise) return previous.promise;

  const element = document.createElement(kind === "style" ? "link" : "script");
  if (kind === "style") {
    element.rel = "stylesheet";
    element.href = url;
  } else {
    // Only the replay group is ordered. The independent stream reader must not
    // wait behind replay downloads before it can send the analysis request.
    element.async = !ordered;
    element.src = url;
  }
  const resource = { element, promise: null };
  const clearHandlers = () => { element.onload = element.onerror = null; };
  resource.promise = new Promise((resolve, reject) => {
    element.onload = () => {
      try {
        if (isReady && !isReady()) throw new Error("页面模块加载异常，请重试");
        resolve();
      } catch (error) {
        reject(error);
      }
    };
    element.onerror = () => reject(new Error(
      `${kind === "style" ? "回放样式" : "页面模块"}加载失败，请检查网络后重试`
    ));
    // Keeping a failed link's position matters when an earlier stylesheet is
    // retried after responsive overrides have already loaded successfully.
    if (previous?.element.isConnected) previous.element.replaceWith(element);
    else document.head.appendChild(element);
  }).then(() => {
    clearHandlers();
  }, (error) => {
    clearHandlers();
    resource.promise = null;
    throw error;
  });
  clientResourceLoads.set(url, resource);
  return resource.promise;
}

function loadReplayAssets() {
  if (!replayAssetsLoader) {
    const styles = [
      "/css/board.css?v=e5743d9e3a78",
      "/css/decision.css?v=ac60719195ba",
      "/css/modal.css?v=9acfaeb269ab",
      "/css/responsive.css?v=fb6a7d50c226",
    ];
    const scripts = [
      ["/js/summary-renderer.js?v=91f7e4703197", () => typeof renderSummaryDashboard === "function"],
      ["/js/replay-controls.js?v=9906cf8d0837", () => typeof bindReplayEvents === "function"],
      ["/js/board-renderer.js?v=87539caa2cc3", () => typeof renderBoard === "function"],
      ["/js/decision-renderer.js?v=e08c0aa27669", () => typeof renderDecisionCard === "function"],
      ["/js/replay-navigation.js?v=73a5c7d89c27", () => typeof jumpNextDecision === "function"],
      ["/js/modal-manager.js?v=d40b9c01bf6e", () => typeof openRoundScoresModal === "function"],
    ];
    replayAssetsLoader = Promise.all([
      ...styles.map((url) => loadClientResource(url, "style")),
      ...scripts.map(([url, isReady]) => loadClientResource(url, "script", isReady, true)),
    ]).then(() => {
      bindReplayEvents();
    }).catch((error) => {
      replayAssetsLoader = null;
      throw error;
    });
  }
  return replayAssetsLoader;
}
