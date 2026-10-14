// 06-pages —— 页级视图路由 / 交易仪表台 / 论坛（占位）
// P1：把原先散落在各模块顶层的导航绑定收口到 showView()，统一页级入口。
// 登录用户默认落地「圆桌」（讨论优先），游客落地「交易仪表台」（只读）。

// ===== 视图路由 =====
let currentView = { name: null, arg: null };
let prevView = { name: null, arg: null };

// 默认落地页：登录 → 圆桌；游客 → 交易仪表台
function navDefault() { return isGuest ? "dashboard" : "roundtable"; }

function markNavActive(name) {
  document.querySelectorAll("[data-nav]").forEach(a => {
    a.classList.toggle("active", a.getAttribute("data-nav") === name);
  });
}

async function showView(name, arg) {
  if (isMobile()) closeMenu();
  if (name === "about") prevView = currentView;   // 关于页提供「返回上一视图」
  switch (name) {
    case "dashboard":
      currentView = { name, arg: null }; markNavActive(name); await renderDashboard(); break;
    case "roundtable":
      currentView = { name, arg: null }; markNavActive(name); await renderRoundtable(); break;
    case "forum":
      currentView = { name, arg: null }; markNavActive(name); await renderForum(); break;
    case "about":
      currentView = { name, arg: null }; markNavActive(name); renderAbout(); break;
    case "users":
      if (me && me.is_admin) { currentView = { name, arg: null }; markNavActive(name); await renderUsers(); }
      break;
    case "project":
      currentView = { name, arg: arg ?? null }; markNavActive(name); await selectProject(arg); break;
    default:
      break;
  }
}

// 从「关于」返回上一视图（无历史则回默认落地页）
function backToPrev() {
  const p = prevView && prevView.name && prevView.name !== "about" ? prevView : null;
  if (p) showView(p.name, p.arg);
  else showView(navDefault());
}

// ===== 数字格式化（仪表台共用）=====
function fmtMoney(v) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtNum(v, d) {
  if (v == null || (typeof v === "number" && isNaN(v))) return "—";
  return Number(v).toFixed(d == null ? 2 : d);
}
// 涨红跌蓝（与全局 .pos/.neg 一致）；0 / null 走 muted
function signCls(v) { return (v == null || v === 0) ? "muted" : (v > 0 ? "pos" : "neg"); }
function signPct(v) { return v == null ? "—" : (v > 0 ? "+" : "") + v + "%"; }

// ===== 交易仪表台 =====
async function renderDashboard() {
  const main = document.getElementById("main");
  main.innerHTML = `
    <div class="page">
      <div class="page-head">
        <div><h1>交易仪表台</h1><p class="page-sub">持仓总览 · 盈亏汇总 · 最近交易记录</p></div>
      </div>
      <div class="panel"><span class="muted">加载中…</span></div>
    </div>`;

  let d;
  try {
    d = isGuest
      ? await guestApi("GET", "/api/guest/overview")
      : await API("GET", "/api/overview");
  } catch (e) {
    main.innerHTML = `
      <div class="page">
        <div class="page-head"><div><h1>交易仪表台</h1><p class="page-sub">持仓总览 · 盈亏汇总 · 最近交易记录</p></div></div>
        <div class="panel"><span class="muted">加载失败：${escapeHtml(e.message || String(e))}</span></div>
      </div>`;
    return;
  }

  const t = d.totals || {};
  const banner = isGuest ? `
    <div class="guest-banner">
      <div><b>游客模式</b> · 只读浏览 · 仅展示公开标的</div>
      <div class="guest-banner-actions"><button class="btn ghost sm" id="dash-login">登录 / 注册</button></div>
    </div>` : "";

  const posRows = (d.positions || []).map(p => `
    <tr data-open="${p.id}" class="rowlink" title="查看 ${escapeHtml(p.name || "")} 详情">
      <td><b>${escapeHtml(p.name || "")}</b></td>
      <td class="muted">${escapeHtml(p.code || p.market || "")}</td>
      <td>${fmtNum(p.shares, 0)}</td>
      <td>${fmtNum(p.cost, 2)}</td>
      <td>${fmtNum(p.last_close, 2)}</td>
      <td>${fmtMoney(p.market_value)}</td>
      <td class="${signCls(p.unrealized)}">${fmtMoney(p.unrealized)}</td>
      <td class="${signCls(p.pct)}">${signPct(p.pct)}</td>
    </tr>`).join("") || `<tr><td colspan="8" class="muted">暂无标的</td></tr>`;

  const tradeRows = (d.trades || []).map(x => {
    const label = x.action === "BUY" ? "买入" : (x.action === "SELL" ? "卖出" : "持有");
    const cls = x.action === "BUY" ? "buy" : (x.action === "SELL" ? "sell" : "");
    return `
    <tr>
      <td>${escapeHtml(x.date || "")}</td>
      <td>${escapeHtml(x.name || "")}</td>
      <td><span class="tag ${cls}">${label}</span></td>
      <td>${fmtNum(x.price, 2)}</td>
      <td>${fmtNum(x.qty, 0)}</td>
      <td class="${signCls(x.realized_pnl)}">${fmtMoney(x.realized_pnl)}</td>
      <td class="muted">${escapeHtml(x.note || "")}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">暂无交易记录</td></tr>`;

  main.innerHTML = `
    <div class="page">
      ${banner}
      <div class="page-head">
        <div><h1>交易仪表台</h1><p class="page-sub">持仓总览 · 盈亏汇总 · 最近交易记录</p></div>
        <div class="page-head-actions"><button class="btn ghost sm" id="dash-reload">刷新</button></div>
      </div>

      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">总市值</div><div class="kpi-value">${fmtMoney(t.market_value)}</div></div>
        <div class="kpi"><div class="kpi-label">浮动盈亏</div><div class="kpi-value ${signCls(t.unrealized)}">${fmtMoney(t.unrealized)}</div></div>
        <div class="kpi"><div class="kpi-label">累计实现盈亏</div><div class="kpi-value ${signCls(t.realized)}">${fmtMoney(t.realized)}</div></div>
        <div class="kpi"><div class="kpi-label">持仓标的</div><div class="kpi-value">${t.positions ?? 0}<span class="kpi-unit"> / ${t.projects ?? 0}</span></div></div>
      </div>

      <div class="panel">
        <h3>持仓总览 <span class="muted">点击任意一行查看该标的详情</span></h3>
        <div class="table-wrap">
          <table class="table" id="dash-pos">
            <thead><tr><th>标的</th><th>代码</th><th>持仓</th><th>成本</th><th>现价</th><th>市值</th><th>浮动盈亏</th><th>收益率</th></tr></thead>
            <tbody>${posRows}</tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <h3>最近交易记录 <span class="muted">最多 60 条</span></h3>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>日期</th><th>标的</th><th>动作</th><th>成交价</th><th>数量</th><th>实现盈亏</th><th>备注</th></tr></thead>
            <tbody>${tradeRows}</tbody>
          </table>
        </div>
      </div>
    </div>`;

  const rl = document.getElementById("dash-reload");
  if (rl) rl.onclick = () => renderDashboard();
  const lg = document.getElementById("dash-login");
  if (lg) lg.onclick = exitGuestToLogin;
  main.querySelectorAll("#dash-pos tbody tr[data-open]").forEach(tr => {
    tr.onclick = () => showView("project", parseInt(tr.getAttribute("data-open"), 10));
  });
}

// ===== 论坛（P3 已落地，实现在 07-forum.js：列表 / 详情 / 发帖 / 回复）=====

// ===== 统一导航绑定（收口原先散落在 04/05 顶层的点击绑定）=====
function bindNav() {
  const entries = [
    ["btn-roundtable", "roundtable"], ["side-roundtable", "roundtable"],
    ["btn-forum", "forum"], ["side-forum", "forum"],
    ["btn-dashboard", "dashboard"], ["side-dashboard", "dashboard"],
    ["btn-about", "about"], ["side-about", "about"],
  ];
  entries.forEach(([id, view]) => {
    const el = document.getElementById(id);
    if (el) el.onclick = () => showView(view);
  });
  // 用户管理：仅管理员
  ["nav-users", "side-users"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.onclick = () => { if (me && me.is_admin) showView("users"); };
  });
}
bindNav();
