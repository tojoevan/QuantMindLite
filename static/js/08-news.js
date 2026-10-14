// 08-news —— 资讯工作台（全站首页）
//
// 数据来自 RSS 订阅，经 Cloudflare 中继取回：quant-web 所在 VPS 只有 IPv6 出站，
// 绝大多数资讯源只有 A 记录，直连必然失败（见 newsfetch.py 顶部说明）。
//
// P7b 预留：接口里已带 ai_score / ai_reason / ai_tags，后端填上后本页自动显示，
// 前端无需再改（低分条目只显示标题 + 来源）。
// 日期/时间由服务端算成北京时间下发，前端不做时区换算，避免跨时区错位。

var newsState = {
  // 默认「近 7 天」而非「今天」：订阅源普遍在早间才开始更新，凌晨打开「今天」必然为空，
  // 首页第一眼空着容易被误判成站点坏了。资讯流本身也是「最近发生了什么」更有用。
  range: "7d",
  category: "",
  q: "",
  starred: false,
  items: [],
  categories: [],
  stats: {},
  panel: "",          // "" | "manage"
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
          <p class="page-sub">RSS 订阅 · 每日更新${newsIsAdmin() ? " · AI 关键信息（P7b 待接）" : ""}</p>
        </div>
        <div class="page-head-actions">
          ${newsIsAdmin() ? `<button class="btn primary sm" id="news-fetch">抓取最新</button>` : ``}
          ${newsIsAdmin() ? `<button class="btn ghost sm" id="news-manage-toggle">订阅管理</button>` : ``}
          <button class="btn ghost sm" id="news-reload">刷新</button>
        </div>
      </div>

      <div class="news-filter panel">
        <div class="nf-row">
          <span class="nf-ranges" id="news-ranges"></span>
          <span class="nf-sep"></span>
          <span class="nf-cats" id="news-cats"></span>
        </div>
        <div class="nf-row nf-row-2">
          <input id="news-q" class="nf-search" type="search" placeholder="搜索标题 / 摘要 / 来源…" />
          ${isGuest ? "" : `<label class="nf-star"><input type="checkbox" id="news-starred" /> 只看收藏</label>`}
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
  const st = document.getElementById("news-starred");
  if (st) st.onchange = (e) => { newsState.starred = e.target.checked; newsReload(); };

  const rl = document.getElementById("news-reload");
  if (rl) rl.onclick = () => newsReload(true);
  const lg = document.getElementById("news-login");
  if (lg) lg.onclick = exitGuestToLogin;
  const ft = document.getElementById("news-fetch");
  if (ft) ft.onclick = newsFetchNow;
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
    `<span class="news-score">AI ${it.ai_score}</span>`;
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
          ${it.category ? `<span class="news-cat">${escapeHtml(it.category)}</span>` : ""}
          ${score}
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
  if (!isGuest && s.relay_ready === false) parts.push(`<span class="news-foot-warn">中继未配置</span>`);
  foot.innerHTML = parts.join('<span class="nf-sep"></span>');
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
    const bad = (r.results || []).filter(x => !x.ok);
    showToast(`抓取完成：${r.feeds} 个源，新增 ${r.added} 条，更新 ${r.updated} 条` +
      (bad.length ? `；${bad.length} 个失败` : ""));
    await newsReload();
    if (newsState.panel === "manage") newsOpenManage();
  } catch (e) {
    showToast("抓取失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "抓取最新"; }
  }
}

// ---------- 订阅管理（管理员） ----------
function newsToggleManage() {
  newsState.panel = newsState.panel === "manage" ? "" : "manage";
  if (newsState.panel === "manage") newsOpenManage();
  else document.getElementById("news-manage-panel").classList.add("hidden");
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
        ? `<span class="nm-err" title="${escapeHtml(f.last_error)}">失败</span>`
        : (f.last_fetched ? escapeHtml(newsFmtAgo(f.last_fetched)) : `<span class="muted">未抓取</span>`)}</td>
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
        showToast(one.ok ? `抓取完成：新增 ${one.added} 条，更新 ${one.updated} 条` : ("抓取失败：" + (one.error || "未知原因")));
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
