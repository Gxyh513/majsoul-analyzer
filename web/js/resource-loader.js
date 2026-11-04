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
      "/css/board.css?v=20261104-demand-replay-1",
      "/css/decision.css?v=20261104-css-parallel-1",
      "/css/modal.css?v=20261104-demand-replay-1",
      "/css/responsive.css?v=20261105-touch-opt-1",
    ];
    const scripts = [
      ["/js/summary-renderer.js?v=20261105-render-opt-1", () => typeof renderSummaryDashboard === "function"],
      ["/js/replay-controls.js?v=20261105-abort-1", () => typeof bindReplayEvents === "function"],
      ["/js/board-renderer.js?v=20261105-render-opt-1", () => typeof renderBoard === "function"],
      ["/js/decision-renderer.js?v=20261105-render-opt-1", () => typeof renderDecisionCard === "function"],
      ["/js/replay-navigation.js?v=20261101-refresh-home-1", () => typeof jumpNextDecision === "function"],
      ["/js/modal-manager.js?v=20261104-demand-replay-1", () => typeof openRoundScoresModal === "function"],
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
