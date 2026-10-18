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

// ---- Markdown 渲染（LLM 输出 / 论坛帖子统一入口）----
// 背景：席位观点、观点分析、论坛存档帖大多是 LLM 生成的 markdown（## 小节、- 列表、
// 表格），以前按纯文本 pre-wrap 展示，"##" 直接露在屏幕上。这里收拢成唯一入口：
//   marked（本地 vendor，GFM）→ HTML；DOMPurify 兜底净化 → 容器加 .md-body 类。
// 净化不是走过场：prompt 里注入了抓取的资讯正文，LLM 输出可能夹带原始 HTML/脚本，
// 论坛帖子也允许任意登录用户发言，渲染前必须过白名单。
// breaks:true——LLM 习惯用单换行分段，与旧版 pre-wrap 的观感一致，不会把几行粘成一段。
if (typeof window.marked !== "undefined" && window.marked.setOptions) {
  window.marked.setOptions({ breaks: true, gfm: true });
}
if (typeof window.DOMPurify !== "undefined" && window.DOMPurify.addHook) {
  // 外链统一新窗口打开（markdown 里的资讯引用链接），只加属性不动内容
  window.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.getAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener");
    }
  });
}

// markdown 文本 → 安全 HTML。库缺失/异常时降级为转义文本 + <br>（绝不死链也不露原始 HTML）。
const mdRender = (text) => {
  const src = String(text == null ? "" : text);
  if (!src.trim()) return "";
  let html;
  try {
    if (typeof window.marked === "undefined" || !window.marked.parse) {
      return escapeHtml(src).replace(/\n/g, "<br>");
    }
    html = window.marked.parse(src);
  } catch (e) {
    return escapeHtml(src).replace(/\n/g, "<br>");
  }
  try {
    if (typeof window.DOMPurify !== "undefined" && window.DOMPurify.sanitize) {
      html = window.DOMPurify.sanitize(html);
    }
  } catch (e) { /* 净化失败时退回未净化 HTML 的概率极低，但不因此让正文消失 */ }
  // marked 的 <table> 无外层包裹：窄屏下宽表格会撑破卡片。补一层 .table-wrap（项目既有
  // 的横向滚动容器）。DOMPurify 之后再包——它只可能动 table 内部，不会拆这层 div。
  return String(html).replace(/<table>/g, '<div class="table-wrap"><table>')
                     .replace(/<\/table>/g, "</table></div>");
};

// 把元素填为 markdown 渲染结果并打上 .md-body 标记（样式与行为挂钩在这个类上）
const mdFill = (el, text) => {
  if (!el) return;
  el.classList.add("md-body");
  el.innerHTML = mdRender(text);
};