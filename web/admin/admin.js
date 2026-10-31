"use strict";

const $ = id => document.getElementById(id);
const ADMIN_KEY_STORAGE = "majsoul_admin_key";
function storedAdminKey() { try { return sessionStorage.getItem(ADMIN_KEY_STORAGE) || ""; } catch (_) { return ""; } }
function saveAdminKey(key) { try { sessionStorage.setItem(ADMIN_KEY_STORAGE, key); return true; } catch (_) { return false; } }
function clearAdminKey() { try { sessionStorage.removeItem(ADMIN_KEY_STORAGE); } catch (_) {} }
let loggedIn = false;
let poller = null;
function say(message, error = false) {
  $("notice").textContent = message || "";
  $("notice").style.color = error ? "#ff9393" : "#7bdbac";
}
function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}
async function request(path, method = "GET", data, adminKey = storedAdminKey()) {
  const response = await fetch("/api/admin" + path, {
    method,
    credentials: "omit",
    headers: {"Accept":"application/json", ...(adminKey ? {"X-Admin-Key":adminKey} : {}), ...(data === undefined ? {} : {"Content-Type":"application/json"})},
    body: data === undefined ? undefined : JSON.stringify(data),
    cache: "no-store",
    redirect: "error"
  });
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) {
    if (response.status === 401 && path !== "/login") showLogin();
    throw new Error(body.detail || "请求失败（" + response.status + "）");
  }
  return body;
}
function showLogin() {
  clearAdminKey();
  loggedIn = false;
  $("dashboard").hidden = true;
  $("loginPanel").hidden = false;
  if (poller) { clearInterval(poller); poller = null; }
}
function showDashboard() {
  loggedIn = true;
  $("dashboard").hidden = false;
  $("loginPanel").hidden = true;
  if (!poller) poller = setInterval(() => { if (loggedIn && document.visibilityState === "visible") refresh().catch(() => {}); }, 20000);
}
function date(ts) { return ts ? new Date(ts * 1000).toLocaleString("zh-CN") : "—"; }
function statusClass(status) { return status === "空闲" ? "ok" : status === "正在使用" ? "busy" : "warn"; }
function render(data) {
  const values = [
    ["总账号", data.total], ["空闲可用", data.idle],
    ["正在抓谱", data.busy], ["540 冷却", data.cooldown], ["等待任务", data.waiting]
  ];
  $("metrics").innerHTML = values.map(([label,value]) =>
    '<div class="metric"><small>' + label + '</small><strong>' + Number(value || 0) + '</strong></div>'
  ).join("");
  const rows = data.accounts || [];
  $("accountRows").innerHTML = rows.length ? rows.map(acc => {
    const id = Number(acc.id);
    const total = Number(acc.success_count || 0) + Number(acc.failure_count || 0);
    const pct = total ? Math.round(100 * acc.success_count / total) + "%" : "—";
    const status = escapeHTML(acc.status);
    const details = (acc.status === "540冷却" ? ' <span class="sub">至 ' + date(acc.cooldown_until) + '</span>' : "") +
      '<span class="sub">上次检测 ' + date(acc.last_checked_at) + '</span>' +
      (acc.last_error ? '<span class="sub">最近错误：' + escapeHTML(acc.last_error) + '</span>' : "");
    return '<tr><td><span class="title">' + escapeHTML(acc.nickname || acc.account) +
      '</span><span class="sub">' + escapeHTML(acc.account) + '</span></td>' +
      '<td><span class="status ' + statusClass(acc.status) + '">' + status + '</span>' + details + '</td>' +
      '<td>已配置<span class="sub">更新 ' + date(acc.token_updated_at) + '</span></td>' +
      '<td>' + acc.success_count + ' / ' + acc.failure_count + '<span class="sub">成功率 ' + pct + '</span></td>' +
      '<td>' + date(acc.last_success_at) + '</td>' +
      '<td>' + (acc.avg_fetch_ms == null ? "—" : (acc.avg_fetch_ms / 1000).toFixed(2) + " 秒") + '</td>' +
      '<td><div class="actions"><button class="mini" data-action="edit" data-id="' + id + '">编辑</button>' +
      '<button class="mini" data-action="toggle" data-id="' + id + '">' + (acc.enabled ? "禁用" : "启用") + '</button>' +
      '<button class="mini danger" data-action="delete" data-id="' + id + '">删除</button></div></td></tr>';
  }).join("") : '<tr><td colspan="7">暂无账号。请添加账号或批量导入。</td></tr>';
  window.adminAccountItems = rows;
}
async function refresh() {
  const data = await request("/status");
  if (!loggedIn) showDashboard();
  render(data);
}
$("loginForm").addEventListener("submit", async event => {
  event.preventDefault();
  const key = $("adminKey").value;
  clearAdminKey();
  try {
    await request("/login", "POST", {}, key);
    if (!saveAdminKey(key)) throw new Error("浏览器无法保存当前标签页的管理员密钥");
    $("adminKey").value = "";
    showDashboard();
    await refresh();
    say("登录成功");
  } catch (error) {
    clearAdminKey();
    showLogin();
    say(error.message, true);
  }
});
$("logoutBtn").addEventListener("click", async () => {
  try { await request("/logout", "POST"); } catch (_) {}
  showLogin(); say("已退出管理员后台");
});
$("reloadBtn").addEventListener("click", () => refresh().catch(err => say(err.message, true)));
$("addForm").addEventListener("submit", async event => {
  event.preventDefault();
  const data = {
    account:$("newAccount").value.trim(),
    password:$("newPassword").value,
    access_token:$("newToken").value.trim(),
    nickname:$("newNickname").value.trim()
  };
  try {
    await request("/accounts", "POST", data);
    $("addForm").reset();
    await refresh();
    say("账号已添加");
  } catch (err) { say(err.message, true); }
});
function parseBulk(input) {
  input = input.trim();
  if (input.startsWith("[")) {
    const data = JSON.parse(input);
    if (!Array.isArray(data)) throw new Error("必须是 JSON 数组");
    return data;
  }
  return input.split(/\r?\n/).filter(line => line.trim()).map(line => {
    const [account, password, access_token, ...rest] = line.split("|");
    if (!account || !password || !access_token) throw new Error("每行必须包含账号、密码、Token");
    return { account:account.trim(), password, access_token:access_token.trim(), nickname:rest.join("|").trim() };
  });
}
$("bulkForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const accounts = parseBulk($("bulkInput").value);
    const result = await request("/accounts/import", "POST", {accounts});
    $("bulkInput").value = "";
    await refresh();
    say("成功导入 " + result.added + " 个，跳过重复 " + result.duplicates + " 个");
  } catch (err) { say(err.message, true); }
});
$("accountRows").addEventListener("click", async event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const account = (window.adminAccountItems || []).find(x => x.id === Number(button.dataset.id));
  if (!account) return;
  const path = "/accounts/" + account.id;
  try {
    if (button.dataset.action === "delete") {
      if (!confirm("确认删除账号 " + account.account + "？")) return;
      await request(path, "DELETE");
    } else if (button.dataset.action === "toggle") {
      await request(path, "PATCH", {enabled: !account.enabled});
    } else if (button.dataset.action === "edit") {
      const nickname = prompt("备注（取消则不修改）", account.nickname || "");
      if (nickname === null) return;
      const password = prompt("新密码（留空则不修改）", "");
      if (password === null) return;
      const access_token = prompt("新 Token（留空则不修改）", "");
      if (access_token === null) return;
      const edit = {nickname};
      if (password.trim()) edit.password = password;
      if (access_token.trim()) edit.access_token = access_token.trim();
      await request(path, "PATCH", edit);
    }
    await refresh();
    say("操作成功");
  } catch (err) { say(err.message, true); }
});
refresh().catch(() => showLogin());
