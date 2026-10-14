// 01-core —— 请求封装 / 主题 / 全局状态 / 通用工具
// 由原 app.js 按顶层边界原样拆分而来（经典脚本，按序加载，共享全局作用域）
const API = (method, path, body) => {
  const opt = { method, headers: {} };
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  return fetch(path, opt).then(r => {
    if (r.status === 401) { doLogout(); enterGuest(); throw new Error("登录已失效，请重新登录"); }
    if (r.status === 403) { throw new Error("无权限（需要管理员）"); }
    if (r.status === 204) return null;
    if (!r.ok) {
      return r.text().then(t => {
        let m = r.status + " " + r.statusText;
        try { const j = JSON.parse(t); if (j.detail) m = j.detail; } catch (e) {}
        throw new Error(m);
      });
    }
    return r.json();
  });
};

const isMobile = () => window.matchMedia("(max-width: 768px)").matches;

// 品种（与后端 datasource.MARKET_LABELS 一一对应）：决定代码怎么写、走哪个数据源
const MARKET_LABELS = { A: "股票", etf: "场内基金", fund: "场外基金", bond: "债券" };
const MARKET_HINTS = {
  A: "如 600519.SH / 000002.SZ",
  etf: "如 510300（沪深300ETF）/ 159915（创业板ETF）/ 161725（LOF）",
  fund: "如 012969 / 206006（场外基金，6 位数字）",
  bond: "如 113056（可转债）/ 127045（深市转债）/ 019547（国债）"
};
const marketLabel = m => MARKET_LABELS[m] || m || "";
// 品种徽章：股票是默认值，不再重复标注，避免每行都挂一个多余标签
const marketTag = m => (m && m !== "A")
  ? `<span class="mk-tag">${escapeHtml(marketLabel(m))}</span>` : "";

// 游客模式专用请求：不带 token，且不触发登出（401/403 直接抛错）
const guestApi = (method, path, body) => {
  const opt = { method, headers: {} };
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  return fetch(path, opt).then(r => {
    if (r.status === 204) return null;
    if (!r.ok) {
      return r.text().then(t => {
        let m = r.status + " " + r.statusText;
        try { const j = JSON.parse(t); if (j.detail) m = j.detail; } catch (e) {}
        throw new Error(m);
      });
    }
    return r.json();
  });
};

// ===== 主题（浅色 / 深色）=====
function applyTheme() {
  const t = localStorage.getItem("qw_theme") || "dark";
  if (t === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  const btn = document.getElementById("btn-theme");
  if (btn) btn.textContent = (t === "light") ? "☀️" : "🌙";
}
function toggleTheme() {
  const cur = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  localStorage.setItem("qw_theme", next);
  applyTheme();
  if (lastChartData) renderCharts(lastChartData); // 图表随主题重新着色
}
// ECharts 配色随主题切换
function chartTheme() {
  const light = document.documentElement.dataset.theme === "light";
  return {
    axis: light ? "#57606a" : "#8b949e",
    mark: light ? "#ffffff" : "#0d1117",
    gold: light ? "#b58900" : "#d4c14a",
    goldSoft: light ? "rgba(181,137,0,0.5)" : "rgba(212,193,74,0.5)",
    pnl: light ? "#e5534b" : "#ff6b6b",
    actual: light ? "#1f6feb" : "#6ba4ff",
  };
}
applyTheme();

let projects = [];
let currentId = null;
let priceChart = null;
let pnlChart = null;
let consensusData = null;   // 各策略逐日信号（进入项目时自动预取，供悬浮提示显示，不再有卡片/标记）
let lastChartData = null;   // 缓存最近一次图表数据，供一致性叠加时复用

let token = localStorage.getItem("qw_token") || "";
let me = JSON.parse(localStorage.getItem("qw_user") || "null");

let STRATEGIES = {};
let isGuest = false;    // 游客模式：只读、无需登录
let viewDays = 60;      // 图表时间范围：默认 60 天，否则取最近 N 个交易日（全部=0 / 30/60/90/180/365）

const today = () => new Date().toISOString().slice(0, 10);

const escapeHtml = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");