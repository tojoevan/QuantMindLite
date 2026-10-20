// 06-pages —— 页级视图路由 / 交易仪表台 / 论坛（占位）
// P1：把原先散落在各模块顶层的导航绑定收口到 showView()，统一页级入口。
// P7：首页改为「资讯工作台」（RSS 订阅），登录与游客都落地到这里。

// ===== 视图路由 =====
let currentView = { name: null, arg: null };

// 默认落地页：资讯工作台（登录与游客一致；圆桌/论坛/仪表台为二级页）
function navDefault() { return "news"; }

// ===== URL 路由（hash 同步）=====
// 存在理由：此前视图状态只在内存里，刷新必然回 navDefault()，后退/收藏/分享也都不可用。
// 形态：#/news、#/roundtable、#/forum、#/forum/19（帖子详情）、#/dashboard、#/about、#/users、#/project/12
// 用 hash 而非真实路径：后端 StaticFiles(html=True) 只对 / 返回 index.html，
// 真实路径（/roundtable）会 404，需要同时改 FastAPI 与线上 nginx；hash 零后端改动，
// 且同样支持刷新/后退/收藏/分享。
const ROUTES = ["news", "roundtable", "forum", "dashboard", "chief", "about", "users", "project"];

// 需要登录的视图：游客落到登录门禁（保留原引导行为），但不改写 URL——
// 直链 #/roundtable 时保留 hash，登录成功后 enterApp 会按 URL 直接进圆桌。
function viewNeedsAuth(name) { return name === "roundtable" || name === "forum" || name === "chief"; }

// 视图 → hash（无视图时为空串）
function viewHash(v) {
  if (!v || !v.name) return "";
  return "#/" + v.name + (v.arg != null ? "/" + v.arg : "");
}
function viewKey(v) { return v && v.name ? v.name + "/" + (v.arg == null ? "" : v.arg) : ""; }

// 解析 location.hash → {name, arg}；空或非法返回 null
function parseHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "").trim();
  if (!raw) return null;
  const parts = raw.split("/").filter(Boolean);
  const name = decodeURIComponent(parts[0] || "");
  if (ROUTES.indexOf(name) < 0) return null;
  if (name === "project") {
    const id = parseInt(parts[1], 10);
    return Number.isFinite(id) ? { name, arg: id } : null;
  }
  if (name === "forum") {
    // 论坛详情进 hash：#/forum/<tid> —— 刷新/后退/收藏/分享都停在当前帖。
    // #/forum（无参）或参数非法 → 列表态（arg=null）
    if (parts[1] == null) return { name, arg: null };
    const id = parseInt(parts[1], 10);
    return Number.isFinite(id) ? { name, arg: id } : { name, arg: null };
  }
  return { name, arg: null };
}

// 用当前视图重置 URL（不新增历史）：用于脏 hash 纠正 / 非法跳转回退
function resetHash() {
  history.replaceState(null, "", location.pathname + location.search + viewHash(currentView));
}

// 把当前视图写进 URL：正常导航走 location.hash（进历史栈，可后退）；
// replace=true 用 replaceState（不新增历史，用于首次落地与 hash 纠正）。
// 视图未变（同页重复点击 / 非法跳转）时只纠正 URL，不污染历史。
function applyHash(prevKey, opts) {
  const nextKey = viewHash(currentView);
  if (location.hash === nextKey) return;
  if (opts.replace || nextKey === prevKey) resetHash();
  else location.hash = nextKey;
}

// 浏览器后退/前进、手动改 hash → 切到对应视图
function onHashChange() {
  const v = parseHash();
  if (!v) { resetHash(); return; }        // 无法识别的 hash：纠正回当前视图
  if (viewKey(v) === viewKey(currentView)) return;
  showView(v.name, v.arg);
}
window.addEventListener("hashchange", onHashChange);

// 首次落地的目标视图：优先 URL 里的，其次默认首页
function initialView() { return parseHash() || { name: navDefault(), arg: null }; }

// P6：侧栏已退场，全站单列全宽，不再需要按视图切换布局 class。
// （原 P4 的 FULLWIDTH_VIEWS / applyLayout 一并删除；移动端导航改走 .nav-drawer 抽屉。）

// 导航高亮：个股详情属于「交易仪表台」体系，一并高亮仪表台入口
function markNavActive(name) {
  const key = name === "project" ? "dashboard" : name;
  document.querySelectorAll("[data-nav]").forEach(a => {
    a.classList.toggle("active", a.getAttribute("data-nav") === key);
  });
}

async function showView(name, arg, opts) {
  opts = opts || {};
  // 受限视图先过门禁：未通过就不进入该视图，避免 currentView / 导航高亮 / URL
  // 与实际展示的页面（登录门禁）三者不一致。
  // 冷启动直链受限页时当前还没有任何视图，先落地默认首页，否则 URL 被否决后页面留白。
  if (viewNeedsAuth(name) && (isGuest || !token)) {
    if (!currentView.name) showView(navDefault(), null, { replace: true });
    else resetHash();
    showAuth("login");
    return;
  }
  if (name === "users" && !(me && me.is_admin)) {
    if (!currentView.name) showView(navDefault(), null, { replace: true });
    else resetHash();
    showToast("仅管理员可访问设置");
    return;
  }

  const prevKey = viewKey(currentView);
  if (isMobile()) closeMenu();
  switch (name) {
    case "news":
      currentView = { name, arg: null }; markNavActive(name); await renderNews(); break;
    case "dashboard":
      currentView = { name, arg: null }; markNavActive(name); await renderDashboard(); break;
    case "roundtable":
      currentView = { name, arg: null }; markNavActive(name); await renderRoundtable(); break;
    case "forum":
      // arg = 主题 id：有值进帖子详情（#/forum/<id>），无值进列表（#/forum）
      currentView = { name, arg: arg ?? null }; markNavActive(name); await renderForum(arg ?? null); break;
    case "chief":
      currentView = { name, arg: null }; markNavActive(name); renderChief(); break;
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
  applyHash(prevKey, opts);   // 视图 → URL：刷新/后退/直链都停在当前页
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

// 置顶查询（pinned 在 /api/projects 上，overview 的 positions 不含该字段）
function pinOf(id) {
  const x = (projects || []).find(v => v.id === id);
  return x ? (x.pinned || 0) : 0;
}

// ===== 交易仪表台「标的清单」筛选 / 排序状态（跨刷新保留，刷新后按原条件重排）=====
let dashAllPositions = [];     // 全量标的（含分类），来自 overview.positions
let dashCatFilter = "";        // 按分类过滤（"" = 全部）
let dashSearch = "";           // 名称 / 代码关键字
let dashSortKey = "";          // 排序字段（"" = 默认，按置顶优先）
let dashSortDir = 1;           // 1 升序 / -1 降序

function dashTh(key, label, num) {
  const arrow = dashSortKey === key ? (dashSortDir > 0 ? " ▲" : " ▼") : "";
  return `<th data-sort="${key}" data-label="${label}" class="sortable${num ? " num" : ""}">${label}${arrow}</th>`;
}

function dashPaintHeaders(tbl) {
  if (!tbl) return;
  tbl.querySelectorAll("th.sortable").forEach(x => {
    const base = x.getAttribute("data-label") || x.textContent;
    x.textContent = base + (dashSortKey === x.getAttribute("data-sort")
      ? (dashSortDir > 0 ? " ▲" : " ▼") : "");
  });
}

// 按当前筛选/排序条件重算并填充标的清单 tbody（同时重绑行点击 / 置顶按钮）
function dashRenderPosBody() {
  const tbl = document.getElementById("dash-pos");
  if (!tbl) return;
  const tb = tbl.querySelector("tbody");
  const rows = dashAllPositions.filter(p => {
    if (dashCatFilter && (p.category || "") !== dashCatFilter) return false;
    if (dashSearch) {
      const q = dashSearch.toLowerCase();
      if (!((p.name || "").toLowerCase().includes(q) || (p.code || "").toLowerCase().includes(q))) return false;
    }
    return true;
  });
  if (dashSortKey) {
    const k = dashSortKey, dir = dashSortDir;
    rows.sort((a, b) => {
      const pa = pinOf(a.id), pb = pinOf(b.id);
      if (pa !== pb) return pb - pa;                       // 置顶始终在前
      let va = a[k], vb = b[k];
      if (typeof va === "string") { va = (va || "").toLowerCase(); vb = (vb || "").toLowerCase(); }
      if (va == null) va = (typeof vb === "number") ? -Infinity : "";
      if (vb == null) vb = (typeof va === "number") ? Infinity : "";
      return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
    });
  } else {
    rows.sort((a, b) => pinOf(b.id) - pinOf(a.id));        // 默认：置顶优先
  }
  tb.innerHTML = rows.map(p => `
    <tr data-open="${p.id}" class="rowlink" title="查看 ${escapeHtml(p.name || "")} 详情">
      <td><b>${escapeHtml(p.name || "")}</b>${isGuest ? "" : `
        <button class="pin-btn ${pinOf(p.id) ? "on" : ""}" type="button"
          title="${pinOf(p.id) ? "取消置顶" : "置顶"}" data-pin="${p.id}">📌</button>`}</td>
      <td>${p.category ? `<span class="cat-tag">${escapeHtml(p.category)}</span>` : `<span class="muted">—</span>`}</td>
      <td class="muted">${p.code ? escapeHtml(p.code) + marketTag(p.market) : escapeHtml(marketLabel(p.market))}</td>
      <td>${fmtNum(p.shares, 0)}</td>
      <td>${fmtNum(p.cost, 2)}</td>
      <td>${fmtNum(p.last_close, 2)}</td>
      <td>${fmtMoney(p.market_value)}</td>
      <td class="${signCls(p.unrealized)}">${fmtMoney(p.unrealized)}</td>
      <td class="${signCls(p.pct)}">${signPct(p.pct)}</td>
    </tr>`).join("") || `<tr><td colspan="9" class="muted">没有符合条件的标的</td></tr>`;
  const cnt = document.getElementById("dash-pos-count");
  if (cnt) cnt.textContent = `共 ${rows.length} / ${dashAllPositions.length} 条`;
  tb.querySelectorAll("tr[data-open]").forEach(tr => {
    tr.onclick = () => showView("project", parseInt(tr.getAttribute("data-open"), 10));
  });
  tb.querySelectorAll(".pin-btn").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); togglePin(parseInt(btn.getAttribute("data-pin"), 10)); };
  });
}

// 一键自动分类：取持仓行业画像为标的补分类标签（已分类默认不覆盖）
async function dashAutoCat() {
  if (!confirm("自动根据持仓行业画像为所有未分类标的补上分类标签？已分类的不会被覆盖。")) return;
  const btn = document.getElementById("dash-autocat");
  if (btn) { btn.disabled = true; btn.textContent = "分类中…"; }
  try {
    const r = await API("POST", "/api/projects/auto-categorize", {});
    showToast(`已自动分类 ${r.updated} 个标的，跳过 ${r.skipped} 个`);
    await renderDashboard();
  } catch (e) {
    showToast("自动分类失败：" + (e && e.message ? e.message : e));
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "自动分类"; }
  }
}

// 绑定筛选条与排序表头（仪表台整体重渲染后调用一次）
function dashBindPosControls() {
  const tbl = document.getElementById("dash-pos");
  const catSel = document.getElementById("dash-cat-filter");
  if (catSel) catSel.onchange = () => { dashCatFilter = catSel.value; dashRenderPosBody(); };
  const search = document.getElementById("dash-search");
  if (search) search.oninput = () => { dashSearch = search.value.trim(); dashRenderPosBody(); };
  const reset = document.getElementById("dash-reset");
  if (reset) reset.onclick = () => {
    dashCatFilter = ""; dashSearch = ""; dashSortKey = ""; dashSortDir = 1;
    if (catSel) catSel.value = "";
    if (search) search.value = "";
    dashPaintHeaders(tbl); dashRenderPosBody();
  };
  const autocat = document.getElementById("dash-autocat");
  if (autocat) autocat.onclick = dashAutoCat;
  if (!tbl) return;
  tbl.querySelectorAll("th.sortable").forEach(th => {
    th.onclick = () => {
      const k = th.getAttribute("data-sort");
      if (dashSortKey === k) dashSortDir = -dashSortDir;
      else { dashSortKey = k; dashSortDir = 1; }
      dashPaintHeaders(tbl);
      dashRenderPosBody();
    };
  });
}

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

  // 置顶项排前（pinned 在 /api/projects 上，overview 的 positions 不含该字段）
  const posList = [...(d.positions || [])];
  dashAllPositions = posList;
  // 分类去重（供筛选下拉用，保留原有顺序）
  const uniqCats = [];
  posList.forEach(p => { const c = p.category || ""; if (c && !uniqCats.includes(c)) uniqCats.push(c); });

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

  const newBtn = isGuest ? "" : `<button class="btn primary sm" id="dash-new">＋ 新建标的</button>`;
  main.innerHTML = `
    <div class="page">
      ${banner}
      <div class="page-head">
        <div><h1>交易仪表台</h1><p class="page-sub">持仓总览 · 盈亏汇总 · 最近交易记录</p></div>
        <div class="page-head-actions">${newBtn}<button class="btn ghost sm" id="dash-reload">刷新</button></div>
      </div>

      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">总市值</div><div class="kpi-value">${fmtMoney(t.market_value)}</div></div>
        <div class="kpi"><div class="kpi-label">浮动盈亏</div><div class="kpi-value ${signCls(t.unrealized)}">${fmtMoney(t.unrealized)}</div></div>
        <div class="kpi"><div class="kpi-label">累计实现盈亏</div><div class="kpi-value ${signCls(t.realized)}">${fmtMoney(t.realized)}</div></div>
        <div class="kpi"><div class="kpi-label">持仓标的</div><div class="kpi-value">${t.positions ?? 0}<span class="kpi-unit"> / ${t.projects ?? 0}</span></div></div>
      </div>

      <div class="panel">
        <h3>标的清单 <span class="muted">点击任意一行查看详情</span></h3>
        <div class="dash-filter">
          <select id="dash-cat-filter" class="mini-select" title="按分类筛选">
            <option value="">全部分类</option>
            ${uniqCats.map(c => `<option value="${escapeHtml(c)}"${dashCatFilter === c ? " selected" : ""}>${escapeHtml(c)}</option>`).join("")}
          </select>
          <input id="dash-search" class="dash-search" placeholder="搜索名称 / 代码" value="${escapeHtml(dashSearch)}" />
          <span class="muted" id="dash-pos-count"></span>
          <button class="btn ghost sm" id="dash-reset" type="button">重置</button>
          ${isGuest ? "" : `<button class="btn ghost sm" id="dash-autocat" type="button" title="根据持仓行业画像自动补分类标签">自动分类</button>`}
        </div>
        <div class="table-wrap">
          <table class="table" id="dash-pos">
            <thead><tr>
              ${dashTh("name", "标的")}
              ${dashTh("category", "分类")}
              ${dashTh("code", "代码")}
              ${dashTh("shares", "持仓", 1)}
              ${dashTh("cost", "成本", 1)}
              ${dashTh("last_close", "现价", 1)}
              ${dashTh("market_value", "市值", 1)}
              ${dashTh("unrealized", "浮动盈亏", 1)}
              ${dashTh("pct", "收益率", 1)}
            </tr></thead>
            <tbody></tbody>
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
  const nw = document.getElementById("dash-new");
  if (nw) nw.onclick = openProjectModal;          // 侧栏删除后，新建入口挪到仪表台页头
  const lg = document.getElementById("dash-login");
  if (lg) lg.onclick = exitGuestToLogin;
  dashBindPosControls();                          // 筛选 / 排序表头绑定
  dashRenderPosBody();                            // 按当前筛选+排序填充表格
}

// ===== 论坛（P3 已落地，实现在 07-forum.js：列表 / 详情 / 发帖 / 回复）=====

// ===== 统一导航绑定（收口原先散落在 04/05 顶层的点击绑定）=====
function bindNav() {
  const entries = [
    ["btn-news", "news"], ["drawer-news", "news"],
    ["btn-roundtable", "roundtable"], ["drawer-roundtable", "roundtable"],
    ["btn-forum", "forum"], ["drawer-forum", "forum"],
    ["btn-dashboard", "dashboard"], ["drawer-dashboard", "dashboard"],
    ["btn-chief", "chief"], ["drawer-chief", "chief"],
    ["btn-about", "about"], ["drawer-about", "about"],
  ];
  entries.forEach(([id, view]) => {
    const el = document.getElementById(id);
    if (el) el.onclick = () => showView(view);
  });
  // 用户管理：仅管理员
  ["nav-users", "drawer-users"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.onclick = () => { if (me && me.is_admin) showView("users"); };
  });
}
bindNav();
