// ============================================================================
// Navigation Jumps
// ============================================================================
function jumpPrevKyoku() {
  if (state.kyokuIdx > 0) selectKyoku(state.kyokuIdx - 1, true);
}

function jumpNextKyoku() {
  const maxIdx = (state.reviewData?.kyokus?.length || 1) - 1;
  if (state.kyokuIdx < maxIdx) selectKyoku(state.kyokuIdx + 1, true);
}

function jumpPrevDecision() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.steps?.length) return;
  const decs = kyoku.decisions || [];
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < state.stepIdx) {
      setStep(decs[i].step_idx);
      return;
    }
  }
  if (state.stepIdx > 0) {
    setStep(0);
    return;
  }
  if (state.kyokuIdx > 0) {
    jumpPrevKyoku();
    const prevKyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
    if (prevKyoku?.decisions?.length) {
      setStep(prevKyoku.decisions[prevKyoku.decisions.length - 1].step_idx);
    }
  } else {
    showToast("已是全场开局了", "info");
  }
}

function jumpNextDecision() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku?.steps?.length) return;
  for (const d of kyoku.decisions || []) {
    if (d.step_idx > state.stepIdx) {
      setStep(d.step_idx);
      return;
    }
  }
  // If there are no more decisions for Hero, advance to final round settlement step
  // Multi-ron has consecutive hora steps. Include every settlement event.
  const targetEndStep = kyoku.steps.length - 1;
  if (state.stepIdx < targetEndStep) {
    setStep(targetEndStep);
    return;
  }
  // Already at settlement or end of kyoku: Automatically jump to next kyoku!
  const maxIdx = (state.reviewData?.kyokus?.length || 1) - 1;
  if (state.kyokuIdx < maxIdx) {
    jumpNextKyoku();
  } else {
    showToast("已经是全场终局了", "info");
  }
}

function jumpPrevBlunder() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return;
  const decs = kyoku.decisions || [];
  for (let i = decs.length - 1; i >= 0; i--) {
    if (decs[i].step_idx < state.stepIdx && decs[i].is_equal === false) {
      setStep(decs[i].step_idx);
      return;
    }
  }
  for (let k = state.kyokuIdx - 1; k >= 0; k--) {
    const prevK = state.reviewData.kyokus[k];
    const prevDecs = prevK?.decisions || [];
    for (let i = prevDecs.length - 1; i >= 0; i--) {
      if (prevDecs[i].is_equal === false) {
        selectKyoku(k);
        setStep(prevDecs[i].step_idx);
        return;
      }
    }
  }
  showToast("前面没有更多错误决策了", "info");
}

function jumpNextBlunder() {
  const kyoku = state.reviewData?.kyokus?.[state.kyokuIdx];
  if (!kyoku) return;
  for (const d of kyoku.decisions || []) {
    if (d.step_idx > state.stepIdx && d.is_equal === false) {
      setStep(d.step_idx);
      return;
    }
  }
  for (let k = state.kyokuIdx + 1; k < state.reviewData.kyokus.length; k++) {
    const nextK = state.reviewData.kyokus[k];
    for (const d of nextK?.decisions || []) {
      if (d.is_equal === false) {
        selectKyoku(k);
        setStep(d.step_idx);
        return;
      }
    }
  }
  showToast("本局及后续没有更多错误手了", "info");
}
