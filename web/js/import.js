// ============================================================================
// Local Paipu Records
// ============================================================================
async function loadLocalCachedRecords() {
  const container = $("localRecordsList");
  const countEl = $("localRecordsCount");
  if (!container) return;
  if (isPagesProxyMode()) {
    if (countEl) countEl.textContent = "";
    container.innerHTML = `<div class="kd-local-empty">公开页面不展示服务器本地缓存；请粘贴雀魂牌谱链接进行检讨</div>`;
    return;
  }

  try {
    const data = await apiFetch("/api/records/local");
    const records = data.records || [];
    if (countEl) countEl.textContent = `共 ${records.length} 场`;

    if (records.length === 0) {
      container.innerHTML = `<div class="kd-local-empty">暂无本地缓存牌谱，请粘贴雀魂牌谱链接进行检讨</div>`;
      return;
    }

    container.innerHTML = records.map((rec) => {
      let dateStr = "未知时间";
      if (rec.start_time) {
        try {
          const d = new Date(rec.start_time * 1000);
          const y = d.getFullYear();
          const m = String(d.getMonth() + 1).padStart(2, "0");
          const day = String(d.getDate()).padStart(2, "0");
          const hh = String(d.getHours()).padStart(2, "0");
          const mm = String(d.getMinutes()).padStart(2, "0");
          dateStr = `${y}-${m}-${day} ${hh}:${mm}`;
        } catch (_) {}
      }
      const sizeKb = rec.file_size ? `${Math.round(rec.file_size / 1024)} KB` : "";
      const namesStr = (rec.names && rec.names.length) ? rec.names.join(" · ") : "四人对局";
      const modeLabel = rec.mode === 1 ? "四人东" : "四人南";

      return `
        <div class="kd-local-item" data-uuid="${escapeHtml(rec.uuid)}">
          <div class="kd-local-item-top">
            <div class="kd-local-meta-left">
              <span class="kd-local-badge">${escapeHtml(modeLabel)}</span>
              <span class="kd-local-time">${escapeHtml(dateStr)}</span>
            </div>
            <span class="kd-local-size">${sizeKb}</span>
          </div>
          <div class="kd-local-item-mid">
            <div class="kd-local-names" title="${escapeHtml(namesStr)}">${escapeHtml(namesStr)}</div>
            <button type="button" class="kd-local-btn">立即分析</button>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".kd-local-item").forEach((el) => {
      el.addEventListener("click", () => {
        const uuid = el.getAttribute("data-uuid");
        if (uuid) {
          startAnalysisFromInput(uuid);
        }
      });
    });
  } catch (err) {
    if (container) {
      container.innerHTML = `<div class="kd-local-empty">加载本地牌谱失败: ${escapeHtml(err.message)}</div>`;
    }
  }
}

