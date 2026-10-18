// 08-news —— 资讯工作台（全站首页）
//
// 数据来自 RSS 订阅，经 Cloudflare 中继取回：quant-web 所在 VPS 只有 IPv6 出站，
// 绝大多数资讯源只有 A 记录，直连必然失败（见 newsfetch.py 顶部说明）。
//
// AI 关键信息（2026-10-17 启用，此前为 P7b 预留位）：后端定时把资讯送 LLM 加工，
// 回填 ai_score（重要度 1-5）/ ai_reason（推荐理由）/ ai_tags（标签）/
// ai_codes（关联标的代码）/ related（是否与我的持仓相关，后端算）。
// 同一份加工数据同时喂圆桌席位提示词与论坛「市场概要」帖。
// 日期/时间由服务端算成北京时间下发，前端不做时区换算，避免跨时区错位。

var newsState = {
  // 默认「近 7 天」而非「今天」：订阅源普遍在早间才开始更新，凌晨打开「今天」必然为空，
  // 首页第一眼空着容易被误判成站点坏了。资讯流本身也是「最近发生了什么」更有用。
  range: "7d",
  category: "",
  q: "",
  starred: false,
  related: false,       // 只看与我的持仓相关（后端按 ai_codes ∩ 持仓代码算）
  items: [],
  categories: [],
  stats: {},
  panel: "",          // "" | "manage"
  // 筛选区折叠态：默认折叠单行；展开显示搜索/只看收藏。读 localStorage 持久化。
  filterOpen: (() => { try { return localStorage.getItem("newsFilterOpen") === "1"; } catch (_) { return false; } })(),
  // 最近一次 OPML 导入的报告。存进 state 而不是直接写 DOM —— 导入后要刷新上方
  // 订阅源表格（newsOpenManage 会整块重绘），报告放 state 里才能一起活下来。
  opmlReport: null,
};

const NEWS_RANGES = [
  ["today", "今天"], ["7d", "近 7 天"], ["30d", "近 30 天"], ["all", "全部"],
];

function newsIsAdmin() { return !!(me && me.is_admin); }

// ---------- 页面外壳 ----------
async function renderNews() {
  const main = document.getElementById("main");
  main.innerHTML = `
    <div class="page news-page">
      ${isGuest ? `
      <div class="guest-banner">
        <div><b>游客模式</b> · 只读浏览资讯流</div>
        <div class="guest-banner-actions"><button class="btn ghost sm" id="news-login">登录 / 注册</button></div>
      </div>` : ""}

      <div class="page-head">
        <div>
          <h1>资讯工作台</h1>
          <p class="page-sub">RSS订阅${newsIsAdmin() ? " · 每日 AI 评分 20:50" : ""}<span id="news-last-fetched-sub"></span></p>
        </div>
        <div class="page-head-actions">
          ${newsIsAdmin() ? `<button class="btn primary sm" id="news-fetch">抓取最新</button>` : ``}
          ${newsIsAdmin() ? `<button class="btn ghost sm" id="news-score" title="对未加工的资讯立即跑一遍 AI 结构化加工（重要度/理由/标签/关联标的）">AI 加工</button>` : ``}
          ${newsIsAdmin() ? `<button class="btn ghost sm" id="news-manage-toggle">订阅管理</button>` : ``}
          <button class="btn ghost sm" id="news-reload">刷新</button>
        </div>
      </div>

      <div class="news-filter panel" id="news-filter">
        <div class="nf-head">
          <span class="nf-title">筛选</span>
          <span class="nf-summary" id="news-filter-summary"></span>
          <button class="nf-toggle" id="news-filter-toggle" type="button"
            title="${newsState.filterOpen ? "收起筛选" : "展开筛选（区间 / 分类 / 搜索 / 只看收藏 / 只看相关）"}">${newsState.filterOpen ? "▾" : "▸"}</button>
        </div>
        <div class="nf-body">
          <div class="nf-row">
            <span class="nf-ranges" id="news-ranges"></span>
            <span class="nf-sep"></span>
            <span class="nf-cats" id="news-cats"></span>
          </div>
          <div class="nf-row nf-row-2">
            <input id="news-q" class="nf-search" type="search" placeholder="搜索标题 / 摘要 / 来源…" />
            ${isGuest ? "" : `<label class="nf-star"><input type="checkbox" id="news-starred" /> 只看收藏</label>`}
            ${isGuest ? "" : `<label class="nf-star" title="只显示关联到我持仓标的的资讯"><input type="checkbox" id="news-related" /> 只看相关</label>`}
          </div>
        </div>
      </div>

      <div class="panel hidden" id="news-manage-panel"></div>
      <div id="news-list"><div class="panel"><span class="muted">加载中…</span></div></div>
      <div class="news-foot" id="news-foot"></div>
    </div>`;

  // 区间切换
  document.getElementById("news-ranges").onclick = (e) => {
    const b = e.target.closest("[data-range]");
    if (!b) return;
    newsState.range = b.getAttribute("data-range");
    newsReload();
  };
  // 分类切换
  document.getElementById("news-cats").onclick = (e) => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    const c = b.getAttribute("data-cat");
    newsState.category = (newsState.category === c) ? "" : c;
    newsReload();
  };
  // 搜索（防抖）
  const qi = document.getElementById("news-q");
  let qt = null;
  qi.oninput = () => {
    clearTimeout(qt);
    qt = setTimeout(() => { newsState.q = qi.value.trim(); newsReload(); }, 300);
  };
  // 筛选区折叠：默认整区折叠只留标题行，点 chevron 展开/收起全部筛选条件
  const ftoggle = document.getElementById("news-filter-toggle");
  const fpanel = document.getElementById("news-filter");
  const fhead = fpanel.querySelector(".nf-head");
  if (newsState.filterOpen) fpanel.classList.add("open");
  // 整行标题可点（含 chevron）：点标题行任意位置展开/收起整个筛选区
  const setFilterOpen = (open) => {
    newsState.filterOpen = open;
    try { localStorage.setItem("newsFilterOpen", open ? "1" : "0"); } catch (_) {}
    fpanel.classList.toggle("open", open);
    ftoggle.textContent = open ? "▾" : "▸";
    ftoggle.title = open ? "收起筛选" : "展开筛选（区间 / 分类 / 搜索 / 只看收藏 / 只看相关）";
  };
  fhead.onclick = () => setFilterOpen(!newsState.filterOpen);
  ftoggle.onclick = (e) => { e.stopPropagation(); setFilterOpen(!newsState.filterOpen); };
  const st = document.getElementById("news-starred");
  if (st) st.onchange = (e) => { newsState.starred = e.target.checked; newsReload(); };
  const rl2 = document.getElementById("news-related");
  if (rl2) rl2.onchange = (e) => { newsState.related = e.target.checked; newsReload(); };

  const rl = document.getElementById("news-reload");
  if (rl) rl.onclick = () => newsReload(true);
  const lg = document.getElementById("news-login");
  if (lg) lg.onclick = exitGuestToLogin;
  const ft = document.getElementById("news-fetch");
  if (ft) ft.onclick = newsFetchNow;
  const sc = document.getElementById("news-score");
  if (sc) sc.onclick = newsScoreNow;
  const mt = document.getElementById("news-manage-toggle");
  if (mt) mt.onclick = () => newsToggleManage();

  newsPaintRanges();
  if (newsState.panel === "manage") newsOpenManage();
  await newsReload();
}

function newsPaintRanges() {
  const box = document.getElementById("news-ranges");
  if (!box) return;
  box.innerHTML = NEWS_RANGES.map(([k, label]) =>
    `<button class="nf-chip ${newsState.range === k ? "on" : ""}" data-range="${k}">${label}</button>`
  ).join("");
}

// ---------- 数据 ----------
async function newsLoad() {
  const qs = new URLSearchParams({
    range: newsState.range,
    category: newsState.category || "",
    q: newsState.q || "",
    starred: newsState.starred ? "1" : "0",
  });
  if (newsState.related) qs.set("related", "1");
  const path = (isGuest ? "/api/guest/news/items?" : "/api/news/items?") + qs.toString();
  return isGuest ? await guestApi("GET", path) : await API("GET", path);
}

async function newsReload(silent) {
  const list = document.getElementById("news-list");
  if (!list) return;
  if (!silent) list.innerHTML = `<div class="panel"><span class="muted">加载中…</span></div>`;
  let d;
  try {
    d = await newsLoad();
  } catch (e) {
    list.innerHTML = `<div class="panel"><span class="muted">加载失败：${escapeHtml(e.message || String(e))}</span></div>`;
    return;
  }
  newsState.items = d.items || [];
  newsState.categories = d.categories || [];
  newsState.stats = d.stats || {};
  // 区间 chip 每次都要重画：只改 newsState.range 的话高亮会停在上一次的选择上
  newsPaintRanges();
  newsPaintCats();
  newsPaintFilterSummary();
  newsPaintList();
  newsPaintFoot();
}

function newsPaintCats() {
  const box = document.getElementById("news-cats");
  if (!box) return;
  const cats = newsState.categories || [];
  const all = `<button class="nf-chip ${newsState.category ? "" : "on"}" data-cat="">全部类别</button>`;
  box.innerHTML = all + cats.map(c =>
    `<button class="nf-chip ${newsState.category === c.name ? "on" : ""}" data-cat="${escapeHtml(c.name)}">` +
    `${escapeHtml(c.name)}<span class="nf-n">${c.count}</span></button>`
  ).join("");
}

// 折叠态标题行右侧的当前筛选摘要（如「近 7 天 · 财经 · 搜索: x」）
function newsPaintFilterSummary() {
  const box = document.getElementById("news-filter-summary");
  if (!box) return;
  const parts = [];
  const rl = NEWS_RANGES.find(([k]) => k === newsState.range);
  parts.push(rl ? rl[1] : newsState.range);
  if (newsState.category) parts.push(newsState.category);
  if (newsState.q) parts.push(`搜索: ${newsState.q}`);
  if (newsState.starred) parts.push("只看收藏");
  if (newsState.related) parts.push("只看相关");
  box.textContent = parts.join(" · ");
}

function newsDayLabel(dateStr) {
  if (!dateStr) return "日期未知";
  const d = new Date(dateStr + "T00:00:00Z");
  const wd = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getUTCDay()] || "";
  const label = `${d.getUTCMonth() + 1} 月 ${d.getUTCDate()} 日 ${wd}`;
  // 与「今天 / 昨天」对比（按北京时间）
  const cnNow = new Date(Date.now() + 8 * 3600 * 1000);
  const t = cnNow.toISOString().slice(0, 10);
  const y = new Date(cnNow.getTime() - 86400000).toISOString().slice(0, 10);
  if (dateStr === t) return `今天 · ${label}`;
  if (dateStr === y) return `昨天 · ${label}`;
  return label;
}

function newsItemHtml(it, idx) {
  const long = (it.summary || "").length > 110;
  const score = (it.ai_score == null) ? "" :
    `<span class="news-score" title="AI 重要度 1-5，5 为影响面最大的重大事件">AI ${it.ai_score}</span>`;
  const rel = it.related
    ? `<span class="news-rel" title="AI 识别到与我的持仓标的相关">持仓相关</span>` : "";
  const reason = it.ai_reason
    ? `<div class="news-reason"><b>推荐理由</b>${escapeHtml(it.ai_reason)}</div>` : "";
  const tags = it.ai_tags
    ? `<div class="news-tags">${it.ai_tags.split(",").filter(Boolean)
        .map(t => `<span class="news-tag">${escapeHtml(t.trim())}</span>`).join("")}</div>` : "";
  const star = isGuest ? "" :
    `<button class="news-star ${it.star ? "on" : ""}" data-star="${it.id}"
       title="${it.star ? "取消收藏" : "收藏"}">${it.star ? "★" : "☆"}</button>`;
  return `
    <article class="news-item" data-id="${it.id}">
      <div class="news-idx">${String(idx).padStart(2, "0")}</div>
      <div class="news-body">
        <div class="news-meta">
          <span class="news-time">${escapeHtml(it.time || "—")}</span>
          ${it.source_name ? `<span class="news-source">${escapeHtml(it.source_name)}</span>` : ""}
          ${it.category ? it.category.split(",").filter(Boolean).map(c => `<span class="news-cat">${escapeHtml(c.trim())}</span>`).join("") : ""}
          ${score}
          ${rel}
        </div>
        <h3 class="news-title"><a href="${escapeHtml(it.link || "#")}" target="_blank" rel="noopener">${escapeHtml(it.title || "(无标题)")}</a></h3>
        ${it.summary ? `<p class="news-summary ${long ? "clamp" : ""}">${escapeHtml(it.summary)}</p>` : ""}
        ${long ? `<button class="news-more" type="button">展开</button>` : ""}
        ${reason}
        ${tags}
      </div>
      ${star}
    </article>`;
}

function newsPaintList() {
  const list = document.getElementById("news-list");
  if (!list) return;
  const items = newsState.items || [];
  if (!items.length) {
    let hint;
    if (newsState.starred) hint = "还没有收藏的条目。取消「只看收藏」看全部，点条目右侧 ☆ 即可收藏。";
    else if (newsState.related) hint = "当前范围内没有与持仓相关的资讯。取消「只看相关」看全部（相关性靠 AI 加工识别，刚抓取的条目要等加工后才标出来）。";
    else if (newsState.q || newsState.category) hint = "当前筛选条件下没有条目，换个条件或切到「全部类别」。";
    else if (newsState.range === "today") hint = "今天还没有新条目（订阅源通常在早间开始更新），切到「近 7 天」看最近的。";
    else if (isGuest) hint = "暂无资讯。登录后可管理订阅源并抓取。";
    else hint = "暂无资讯。点右上角「抓取最新」从订阅源拉取。";
    list.innerHTML = `<div class="panel"><span class="muted">${hint}</span></div>`;
    return;
  }
  // 按日期分组（items 已按时间倒序）
  const groups = [];
  items.forEach(it => {
    const key = it.date || "";
    let g = groups[groups.length - 1];
    if (!g || g.date !== key) { g = { date: key, items: [] }; groups.push(g); }
    g.items.push(it);
  });
  list.innerHTML = groups.map(g => `
    <section class="news-day">
      <div class="news-day-head">
        <span class="news-day-title">${escapeHtml(newsDayLabel(g.date))}</span>
        <span class="news-day-n">${g.items.length} 条</span>
      </div>
      ${g.items.map((it, i) => newsItemHtml(it, i + 1)).join("")}
    </section>`).join("");

  // 展开 / 收起摘要
  list.querySelectorAll(".news-more").forEach(btn => {
    btn.onclick = () => {
      const p = btn.previousElementSibling;
      if (!p) return;
      const open = p.classList.toggle("clamp") === false;
      btn.textContent = open ? "收起" : "展开";
    };
  });
  // 收藏
  list.querySelectorAll("[data-star]").forEach(btn => {
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        const r = await API("POST", `/api/news/items/${btn.getAttribute("data-star")}/star`);
        const it = newsState.items.find(x => x.id === r.id);
        if (it) it.star = r.star;
        btn.classList.toggle("on", !!r.star);
        btn.textContent = r.star ? "★" : "☆";
        if (newsState.starred && !r.star) newsPaintList();
      } catch (e) {
        showToast("收藏失败：" + e.message);
      } finally {
        btn.disabled = false;
      }
    };
  });
}

function newsPaintFoot() {
  const foot = document.getElementById("news-foot");
  if (!foot) return;
  const s = newsState.stats || {};
  const parts = [];
  parts.push(`订阅 ${s.feeds || 0} 个源`);
  parts.push(`今日 ${s.today || 0} 条`);
  if (s.last_fetched) parts.push(`最近抓取 ${newsFmtAgo(s.last_fetched)}`);
  if (s.feed_errors) parts.push(`<span class="news-foot-warn">${s.feed_errors} 个源抓取失败</span>`);
  if (s.feed_empties) parts.push(`<span class="news-foot-warn">${s.feed_empties} 个源无内容</span>`);
  if (!isGuest && s.relay_ready === false) parts.push(`<span class="news-foot-warn">中继未配置</span>`);
  foot.innerHTML = parts.join('<span class="nf-sep"></span>');

  // 页头副标题同步显示最近抓取时间（复用同一份 stats）
  const sub = document.getElementById("news-last-fetched-sub");
  if (sub) sub.textContent = s.last_fetched ? " · 最近抓取 " + newsFmtAgo(s.last_fetched) : "";
}

// 后端给的是 UTC ISO（带 Z），这里换算成「x 分钟前」（北京时间）
function newsFmtAgo(iso) {
  const t = Date.parse(iso);
  if (!t) return "—";
  const min = Math.floor((Date.now() - t) / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return min + " 分钟前";
  const h = Math.floor(min / 60);
  if (h < 24) return h + " 小时前";
  return Math.floor(h / 24) + " 天前";
}

// ---------- 抓取 ----------
async function newsFetchNow() {
  const btn = document.getElementById("news-fetch");
  if (btn) { btn.disabled = true; btn.textContent = "抓取中…"; }
  try {
    const r = await API("POST", "/api/news/fetch", { limit: 60 });
    const rs = r.results || [];
    const empt = rs.filter(x => x.empty).length;
    const fail = rs.filter(x => !x.ok && !x.empty).length;
    showToast(`抓取完成：${r.feeds} 个源，新增 ${r.added} 条，更新 ${r.updated} 条` +
      (empt ? `；${empt} 个空源无内容` : "") + (fail ? `；${fail} 个失败` : ""));
    await newsReload();
    if (newsState.panel === "manage") newsOpenManage();
  } catch (e) {
    showToast("抓取失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "抓取最新"; }
  }
}

// ---------- AI 加工（管理员）：对未加工资讯立即跑一遍结构化评分 ----------
// 与 cron 的 scripts/news_score.py 同一逻辑；这里是「刚抓完想立刻看到徽标」的手动入口。
// 加工结果同时服务：本页徽标 / 圆桌席位提示词注入 / 论坛市场概要帖。
async function newsScoreNow() {
  const btn = document.getElementById("news-score");
  if (btn) { btn.disabled = true; btn.textContent = "加工中…"; }
  try {
    const r = await API("POST", "/api/news/score", { limit: 40 });
    showToast(r.message || "AI 加工完成");
    await newsReload(true);
  } catch (e) {
    showToast("AI 加工失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "AI 加工"; }
  }
}

// ---------- 订阅管理（管理员） ----------
function newsToggleManage() {
  newsState.panel = newsState.panel === "manage" ? "" : "manage";
  if (newsState.panel === "manage") newsOpenManage();
  else document.getElementById("news-manage-panel").classList.add("hidden");
}

// OPML 导入结果渲染。重点是把「加进来了但没内容」的源摆到台面上 ——
// OPML 只是一份地址清单，对源是否还活着一无所知（新浪财经那份 OPML 里 9 个源全是空的）。
function newsOpmlReport(r) {
  if (r && r.error) {
    return `<div class="nm-opml-result"><div class="nm-opml-sum nm-opml-warn">导入失败：${escapeHtml(r.error)}</div></div>`;
  }
  const list = r.fetched || [];
  const tag = (x) => x.ok
    ? `<span class="nm-badge ok">已抓到 ${x.added} 条</span>`
    : (x.status === "empty" ? `<span class="nm-badge warn">空源</span>`
      : (x.status === "untested" ? `<span class="nm-badge">未实抓</span>`
        : `<span class="nm-badge bad">失败</span>`));
  const rows = list.map(x => `<li>${tag(x)}
      <span class="nm-opml-name">${escapeHtml(x.title || x.url)}</span>
      ${x.message ? `<span class="muted">${escapeHtml(x.message)}</span>` : ""}</li>`).join("");
  const skipped = (r.skipped || []).length
    ? `<div class="muted nm-opml-skip">跳过 ${r.skipped.length} 个：` +
      r.skipped.map(s => `${escapeHtml(s.title || s.url)}（${escapeHtml(s.reason)}）`).join("、") + `</div>`
    : "";
  const okN = list.filter(x => x.ok).length;
  const emptyN = list.filter(x => x.status === "empty").length;
  return `<div class="nm-opml-result">
    <div class="nm-opml-sum">解析 ${r.parsed} 个源，新增 ${r.added} 个` +
    (okN ? `，${okN} 个有内容` : "") +
    (emptyN ? `，<span class="nm-opml-warn">${emptyN} 个是空源（可访问但没有任何条目）</span>` : "") +
    `</div>${rows ? `<ul class="nm-opml-list">${rows}</ul>` : ""}${skipped}</div>`;
}

async function newsOpenManage() {
  const box = document.getElementById("news-manage-panel");
  if (!box) return;
  box.classList.remove("hidden");
  box.innerHTML = `<h3>订阅管理 <span class="muted">加载中…</span></h3>`;
  let feeds = [], settings = {};
  try {
    feeds = await API("GET", "/api/news/feeds");
    settings = await API("GET", "/api/news/settings");
  } catch (e) {
    box.innerHTML = `<h3>订阅管理</h3><span class="muted">加载失败：${escapeHtml(e.message)}</span>`;
    return;
  }
  const rows = feeds.map(f => `
    <tr>
      <td><b>${escapeHtml(f.title || "(未命名)")}</b>
        ${f.category ? `<span class="news-cat">${escapeHtml(f.category)}</span>` : ""}</td>
      <td class="muted nm-url" title="${escapeHtml(f.url)}">${escapeHtml(f.url)}</td>
      <td>${f.item_count}</td>
      <td>${f.enabled ? `<span class="nm-ok">启用</span>` : `<span class="muted">停用</span>`}</td>
      <td>${f.last_status === "error"
        ? `<span class="nm-badge bad" title="${escapeHtml(f.last_error)}">失败</span>`
        : (f.last_status === "empty"
          ? `<span class="nm-badge warn" title="${escapeHtml(f.last_error || "源可访问但不含任何条目")}">空源</span>`
          : (f.last_fetched ? escapeHtml(newsFmtAgo(f.last_fetched)) : `<span class="muted">未抓取</span>`))}</td>
      <td class="nm-actions">
        <button class="btn-mini ghost" data-ffetch="${f.id}">抓取</button>
        <button class="btn-mini ghost" data-ftoggle="${f.id}" data-on="${f.enabled ? 1 : 0}">${f.enabled ? "停用" : "启用"}</button>
        <button class="btn-mini ghost" data-fdel="${f.id}">删除</button>
      </td>
    </tr>`).join("") || `<tr><td colspan="6" class="muted">还没有订阅源，先添加一个。</td></tr>`;

  box.innerHTML = `
    <h3>订阅管理 <span class="muted">增删源、启停、单独抓取；抓取经中继进行</span></h3>
    <div class="table-wrap">
      <table class="table nm-table">
        <thead><tr><th>名称</th><th>Feed 地址</th><th>条目</th><th>状态</th><th>最近抓取</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <form id="nm-add" class="nm-add">
      <input name="url" placeholder="Feed 地址（https://…/feed.xml）" required />
      <input name="title" placeholder="名称（可留空，抓取后自动填）" />
      <input name="category" placeholder="分类（如 AI 动态 / 科技 / 政策）" />
      <button class="btn primary sm" type="submit">＋ 添加订阅源</button>
    </form>
    <form id="nm-opml" class="nm-opml">
      <div class="nm-opml-head"><b>批量导入 OPML</b>
        <span class="muted">订阅清单文件（RSS 阅读器导出）；导入后会立刻实抓一次，当场标出没有内容的源</span></div>
      <textarea name="content" rows="3" placeholder="把 OPML 内容粘贴到这里，或用下面「选择文件」"></textarea>
      <div class="nm-opml-bar">
        <input type="file" id="nm-opml-file" accept=".opml,.xml,text/xml,application/xml" />
        <input name="category" placeholder="统一分类（可选，如 财经）" />
        <button class="btn ghost sm" type="submit">⇪ 解析并导入</button>
        <button class="btn ghost sm" type="button" id="nm-opml-export"
          title="把当前订阅清单存成 OPML 文件（换机/备份用；只含启用的源）">⇩ 导出 OPML</button>
      </div>
    </form>
    <div id="nm-opml-report">${newsState.opmlReport ? newsOpmlReport(newsState.opmlReport) : ""}</div>
    <h4 class="nm-h4">中继设置 <span class="muted">本机只有 IPv6 出站，不走中继将无法访问绝大多数资讯源</span></h4>
    <form id="nm-relay" class="nm-add">
      <input name="relay_url" placeholder="中继地址（如 https://relay.kapibala.icu）" value="${escapeHtml(settings.relay_url || "")}" />
      <input name="relay_key" type="password" placeholder="${settings.has_key ? "已配置（留空不改）" : "中继密钥"}" />
      <button class="btn ghost sm" type="submit">保存中继设置</button>
    </form>`;

  box.querySelectorAll("[data-ffetch]").forEach(b => {
    b.onclick = async () => {
      b.disabled = true; b.textContent = "…";
      try {
        const r = await API("POST", "/api/news/fetch", { feed_id: parseInt(b.getAttribute("data-ffetch"), 10) });
        const one = (r.results || [])[0] || {};
        showToast(one.ok
          ? `抓取完成：新增 ${one.added} 条，更新 ${one.updated} 条`
          : (one.empty
            ? ("空源：" + (one.error || "源可访问但不含任何条目"))
            : ("抓取失败：" + (one.error || "未知原因"))));
      } catch (e) { showToast("抓取失败：" + e.message); }
      await newsReload(true);
      newsOpenManage();
    };
  });
  box.querySelectorAll("[data-ftoggle]").forEach(b => {
    b.onclick = async () => {
      const on = parseInt(b.getAttribute("data-on"), 10) ? 0 : 1;
      try {
        await API("PATCH", `/api/news/feeds/${b.getAttribute("data-ftoggle")}`, { enabled: on });
      } catch (e) { showToast("操作失败：" + e.message); }
      await newsReload(true);
      newsOpenManage();
    };
  });
  box.querySelectorAll("[data-fdel]").forEach(b => {
    b.onclick = async () => {
      const name = b.closest("tr").querySelector("b").textContent;
      if (!confirm(`删除订阅源「${name}」？该源已入库的条目会一并删除，且不可恢复。`)) return;
      try {
        await API("DELETE", `/api/news/feeds/${b.getAttribute("data-fdel")}`);
        showToast("已删除");
      } catch (e) { showToast("删除失败：" + e.message); }
      await newsReload(true);
      newsOpenManage();
    };
  });
  box.querySelector("#nm-add").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await API("POST", "/api/news/feeds", {
        url: f.url.value.trim(),
        title: f.title.value.trim(),
        category: f.category.value.trim(),
      });
      showToast("已添加，点「抓取」拉取内容");
      await newsReload(true);
      newsOpenManage();
    } catch (err) { showToast("添加失败：" + err.message); }
  };
  const opmlFile = box.querySelector("#nm-opml-file");
  if (opmlFile) opmlFile.onchange = () => {
    const file = (opmlFile.files || [])[0];
    if (!file) return;
    const rd = new FileReader();
    rd.onload = () => {
      const ta = box.querySelector('#nm-opml textarea[name="content"]');
      if (ta) ta.value = String(rd.result || "");
      showToast(`已读取 ${file.name}（${Math.max(1, Math.round(file.size / 1024))} KB），点「解析并导入」提交`);
    };
    rd.onerror = () => showToast("读取文件失败");
    rd.readAsText(file, "utf-8");
  };
  const opmlExport = box.querySelector("#nm-opml-export");
  if (opmlExport) opmlExport.onclick = async () => {
    opmlExport.disabled = true;
    try {
      // 导出是 XML 附件，不能走 API()（它只吃 JSON）——原生 fetch + blob 下载
      const r = await fetch("/api/news/feeds/export-opml", {
        headers: { "Authorization": "Bearer " + token },
      });
      if (!r.ok) {
        let m = r.status + " " + r.statusText;
        try { const j = JSON.parse(await r.text()); if (j.detail) m = j.detail; } catch (e) {}
        throw new Error(m);
      }
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "quant-news-feeds.opml";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);
      showToast("已导出 OPML（只含启用的源）");
    } catch (e) { showToast("导出失败：" + e.message); }
    opmlExport.disabled = false;
  };
  box.querySelector("#nm-opml").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const content = f.content.value.trim();
    if (!content) { showToast("请先粘贴 OPML 内容，或用「选择文件」"); return; }
    const btn = f.querySelector('button[type="submit"]');
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "导入中…";
    try {
      const r = await API("POST", "/api/news/feeds/import-opml", {
        content: content, category: f.category.value.trim(), fetch: true,
      });
      newsState.opmlReport = r;
      const emptyN = (r.fetched || []).filter(x => x.status === "empty").length;
      showToast(`解析 ${r.parsed} 个源，新增 ${r.added} 个` +
        (emptyN ? `；其中 ${emptyN} 个是空源` : ""));
    } catch (err) {
      // 错误也塞进 state：下面 newsOpenManage 会整块重绘，直接写 DOM 会被冲掉
      newsState.opmlReport = { error: err.message };
      showToast("导入失败：" + err.message);
    } finally {
      btn.disabled = false; btn.textContent = label;
    }
    await newsReload(true);
    newsOpenManage();
  };
  box.querySelector("#nm-relay").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = { relay_url: f.relay_url.value.trim() };
    if (f.relay_key.value.trim()) body.relay_key = f.relay_key.value.trim();
    try {
      await API("PUT", "/api/news/settings", body);
      showToast("中继设置已保存");
      await newsReload(true);
      newsOpenManage();
    } catch (err) { showToast("保存失败：" + err.message); }
  };
}
