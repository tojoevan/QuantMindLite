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

// 策略/项目说明：默认只显示提示，点击后才展开编辑框（避免面板被输入框占据）
function renderNoteView() {
  const wrap = document.getElementById("note-wrap");
  if (!wrap) return;
  const p = projects.find(x => x.id === currentId);
  const txt = (p && p.strategy || "").trim();
  // 游客模式：仅展示说明，不可编辑
  if (isGuest) {
    wrap.innerHTML = txt
      ? `<div class="note-display">${escapeHtml(p.strategy).replace(/\n/g, "<br>")}</div>`
      : `<div class="muted">（该项目暂无策略说明）</div>`;
    return;
  }
  const hint = "策略说明 / 项目说明（可选）：例如回踩20日线买入，跌破即止损…";
  if (txt) {
    wrap.innerHTML = `<div class="note-display" id="note-display"><span>${escapeHtml(p.strategy).replace(/\n/g, "<br>")}</span><button class="link-edit" id="btn-edit-note">✎ 编辑</button><span class="note-hint">${hint}</span></div>`;
  } else {
    wrap.innerHTML = `<div class="note-trigger" id="note-display"><button class="link-edit" id="btn-edit-note">✎ 添加说明</button><span class="note-hint">${hint}</span></div>`;
  }
  document.getElementById("note-display").onclick = enterNoteEdit;
}

function enterNoteEdit() {
  const p = projects.find(x => x.id === currentId);
  const wrap = document.getElementById("note-wrap");
  if (!wrap) return;
  const val = escapeHtml(p && p.strategy || "");
  wrap.innerHTML = `
    <textarea id="proj-strategy" class="strat-note" rows="2" placeholder="策略说明 / 项目说明（可选）：例如回踩20日线买入，跌破即止损…">${val}</textarea>
    <div class="row" style="margin-top:8px">
      <button id="btn-save-note" class="btn-mini">保存说明</button>
      <button id="btn-cancel-note" class="btn-mini ghost">取消</button>
      <span class="muted" id="note-hint"></span>
    </div>`;
  const ta = document.getElementById("proj-strategy");
  if (ta) ta.focus();
  document.getElementById("btn-save-note").onclick = async () => {
    await saveStrategyNote();
    renderNoteView();
  };
  document.getElementById("btn-cancel-note").onclick = () => renderNoteView();
}

async function loadProjects() {
  STRATEGIES = await API("GET", "/api/strategies").catch(() => ({}));
  projects = await API("GET", "/api/projects");
  renderProjectList();
  if (!projects.length) {
    // 当前用户无项目：清掉失效 currentId，主区给出引导
    currentId = null;
    document.getElementById("main").innerHTML = `
      <div class="panel"><h3>欢迎</h3>
      <p class="muted">暂无项目，请在左侧「新建项目」中创建第一个项目（如 600519.SH）。</p></div>`;
    return;
  }
  // currentId 已不在新列表中（如游客模式遗留/项目被删）则回退到第一个
  if (!projects.some(p => p.id === currentId)) currentId = null;
  if (!currentId) selectProject(projects[0].id);
}

function renderProjectList() {
  const ul = document.getElementById("project-list");
  ul.innerHTML = "";
  // 置顶项目排前（后端已按 pinned 排序，这里再排一次保证渲染顺序一致）
  const sorted = [...projects].sort((a, b) => (b.pinned || 0) - (a.pinned || 0));
  sorted.forEach(p => {
    const li = document.createElement("li");
    li.className = p.id === currentId ? "active" : "";
    li.innerHTML = `
      <div class="proj-line" title="${escapeHtml(p.name)}${p.code ? " " + escapeHtml(p.code) : ""}">
        <span class="proj-name">${escapeHtml(p.name)}</span>
        <span class="code">${escapeHtml(p.code || p.market)}</span>
      </div>
      <button class="pin-btn ${p.pinned ? "on" : ""}" type="button"
        title="${p.pinned ? "取消置顶" : "置顶"}" data-pin="${p.id}">📌</button>`;
    li.onclick = (e) => {
      if (e.target.closest(".pin-btn")) return;   // 点置顶按钮不触发切换项目
      selectProject(p.id);
      if (isMobile()) closeMenu();
    };
    ul.appendChild(li);
  });
  ul.querySelectorAll(".pin-btn").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); togglePin(parseInt(btn.getAttribute("data-pin"), 10)); };
  });
}

// 置顶 / 取消置顶（持久化到后端，列表置顶项排前）
async function togglePin(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  try {
    const r = await API("PATCH", `/api/projects/${id}`, { pinned: p.pinned ? 0 : 1 });
    p.pinned = r.pinned || 0;
    renderProjectList();
    showToast(p.pinned ? "已置顶" : "已取消置顶");
  } catch (err) { showToast("操作失败：" + err.message); }
}

async function selectProject(id) {
  currentId = id;
  renderProjectList();
  const p = projects.find(x => x.id === id);
  if (isGuest) { await renderGuestProject(p); return; }
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h3>${p.name} <span class="muted">${p.code || ""} · ${p.market}</span></h3>
      <div id="note-wrap"></div>
    </div>
    <div class="panel">
      <h3>最新操作建议 <button id="btn-predict" class="btn-mini">生成建议</button></h3>
      <div id="suggestion" class="sug"><span class="muted">尚未生成</span></div>
    </div>
    <div class="panel">
      <h3>策略设置 <button id="btn-save-strategy" class="btn-mini">保存策略</button></h3>
      <div id="strategy-body"></div>
    </div>
    <div class="panel"><h3>预测线 vs 实际线
      <span class="range-pick" id="range-pick">
        <button data-d="0">全部</button>
        <button data-d="30">30天</button>
        <button data-d="60" class="on">60天</button>
        <button data-d="90">90天</button>
        <button data-d="180">180天</button>
        <button data-d="365">365天</button>
      </span>
    </h3><div id="priceChart" class="chart"></div></div>
    <div class="panel"><h3>盈亏曲线</h3><div id="pnlChart" class="chart"></div></div>
    <div class="panel">
      <h3>持仓 <button id="btn-pos-save" class="btn-mini">保存仓位</button></h3>
      <div class="param-grid" style="margin-bottom:8px">
        <label class="param"><span>持仓数量</span><input id="pos-shares" type="number" step="0.0001" placeholder="0" value="0" /></label>
        <label class="param"><span>成本价</span><input id="pos-cost" type="number" step="0.01" placeholder="0" value="0" /></label>
      </div>
      <div class="muted">输入你当前实际持有的数量与成本价；后续“反馈”的盈亏将以此成本起算。</div>
      <div id="position" class="muted">—</div>
    </div>
    <div class="panel">
      <h3>实际行情（实际线）</h3>
      <div class="row">
        <button id="btn-fetch" class="btn-mini">🔄 强制刷新行情</button>
        <select id="fetch-days" class="mini-select">
          <option value="60" selected>近60日</option>
          <option value="120">近120日</option>
          <option value="250">近250日</option>
        </select>
        <span class="muted" id="fetch-hint">按项目代码自动同步</span>
      </div>
      <form id="price-form" class="inline" style="margin-top:10px">
        <input id="px-date" type="date" value="${today()}" />
        <input id="px-close" type="number" step="0.01" placeholder="收盘价" />
        <button type="submit">手动添加</button>
      </form>
      <div class="table-wrap" style="margin-top:10px">
        <table id="price-table"><thead><tr><th>日期</th><th>收盘价</th></tr></thead><tbody></tbody></table>
      </div>
    </div>
    <div class="panel">
      <h3>反馈实际操作</h3>
      <form id="fb-form" class="inline">
        <input id="fb-date" type="date" value="${today()}" />
        <select id="fb-action"><option value="BUY">买入</option><option value="SELL">卖出</option><option value="HOLD">持有</option></select>
        <input id="fb-price" type="number" step="0.01" placeholder="成交价" />
        <input id="fb-qty" type="number" step="0.0001" placeholder="数量" value="0" />
        <input id="fb-note" placeholder="备注(可选)" />
        <button type="submit">提交反馈</button>
      </form>
    </div>
    <div class="panel">
      <h3>反馈记录</h3>
      <div class="table-wrap">
        <table id="fb-table"><thead><tr><th>日期</th><th>动作</th><th>价</th><th>量</th><th>实现盈亏</th><th>备注</th></tr></thead><tbody></tbody></table>
      </div>
    </div>
    <div class="panel">
      <button class="del" id="btn-del">删除该项目</button>
    </div>
  `;
  document.getElementById("btn-predict").onclick = generateSuggestion;
  document.getElementById("price-form").onsubmit = addPrice;
  document.getElementById("btn-fetch").onclick = fetchMarket;
  document.getElementById("fb-form").onsubmit = submitFeedback;
  document.getElementById("btn-del").onclick = () => deleteProject(id);
  document.getElementById("btn-pos-save").onclick = savePosition;
  renderNoteView();
  await renderStrategyPanel(p);
  await loadConsensusForHover();   // 预取各策略逐日信号，供悬浮提示显示（无需一致性卡片）
  await autoFetch(id);             // 进入项目时自动拉取最新行情（限频：今日已拉取则跳过）
  await refreshProjectView();
  setupRangePick();                // 绑定图表时间范围选择（全部/30/60/90/180/365天）
}

// 渲染「最新操作建议」（登录与游客共用）
function fillSuggestion(data) {
  const sug = document.getElementById("suggestion");
  if (!sug) return;
  if (data.predictions.length) {
    const s = data.predictions[data.predictions.length - 1];
    sug.innerHTML = `
      <span class="signal ${s.signal}">${s.signal}</span>
      <div>
        <div>预测价：${s.predicted_price ?? "—"}（区间 ${s.predicted_low ?? "—"} ~ ${s.predicted_high ?? "—"}）</div>
        <div class="muted">置信度 ${s.confidence} · 模型 ${s.model}</div>
        <div class="muted">${s.note || ""}</div>
      </div>`;
  } else {
    sug.innerHTML = `<span class="muted">尚未生成</span>`;
  }
}

// 渲染实际行情表（最近 limit 条，倒序）
function fillPriceTable(data, limit = 30) {
  const ptb = document.querySelector("#price-table tbody");
  if (!ptb) return;
  ptb.innerHTML = "";
  data.market_prices.slice(-limit).reverse().forEach(m => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${m.date}</td><td>${m.close}</td>`;
    ptb.appendChild(tr);
  });
}

async function refreshProjectView() {
  const data = await API("GET", `/api/projects/${currentId}/chart`);
  // 建议
  fillSuggestion(data);
  // 行情表（最近30条，倒序）
  fillPriceTable(data, 30);
  // 持仓
  const pos = document.getElementById("position");
  if (pos) {
    const ps = document.getElementById("pos-shares");
    const pc = document.getElementById("pos-cost");
    if (ps) ps.value = data.position.seed_shares ?? 0;
    if (pc) pc.value = data.position.seed_cost ?? 0;
    const mv = data.position.market_value ?? 0;
    const up = data.position.unrealized ?? 0;
    pos.innerHTML = `持仓 <b>${data.position.shares}</b> 股 · 成本 <b>${data.position.avg}</b> · 市值 <b>${mv}</b> · 浮动盈亏 <span class="${up >= 0 ? "pos" : "neg"}">${up}</span> · 累计实现盈亏 <span class="${data.position.realized >= 0 ? "pos" : "neg"}">${data.position.realized}</span>`;
  }
  // 反馈表
  const tb = document.querySelector("#fb-table tbody");
  tb.innerHTML = "";
  data.feedbacks.forEach(f => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${f.date}</td><td>${f.action}</td><td>${f.price}</td><td>${f.qty}</td><td class="${f.realized_pnl >= 0 ? "pos" : "neg"}">${f.realized_pnl}</td><td class="muted">${f.note || ""}</td>`;
    tb.appendChild(tr);
  });
  renderCharts(data);
}

// 按时间范围裁剪图表数据：仅保留最近 viewDays 个交易日（0 = 全部）
function sliceChartData(data) {
  if (!viewDays || viewDays <= 0 || data.market_prices.length <= viewDays) return data;
  const mp = data.market_prices.slice(-viewDays);
  const firstDate = mp[0].date;
  const sc = data.strategy_curve.filter(p => p.date >= firstDate);
  const pnl = data.pnl_curve.slice(-viewDays);
  return { ...data, market_prices: mp, strategy_curve: sc, pnl_curve: pnl };
}

let echartsRescueTried = false;

// 图表库不可用时的可见提示（避免静默空白，用户以为页面坏了）
function showChartFallback() {
  ["priceChart", "pnlChart"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = '<div class="chart-fallback">图表库（ECharts）加载失败，曲线暂无法显示。请刷新页面重试；若持续失败请检查网络是否能访问本站静态资源。</div>';
  });
}

function renderCharts(data) {
  lastChartData = data;
  // 本地 vendor/echarts.min.js 若未加载成功，先尝试回退 CDN 再渲染一次，仍失败则给出提示
  if (typeof window.echarts === "undefined") {
    if (!echartsRescueTried) {
      echartsRescueTried = true;
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js";
      s.onload = () => renderCharts(data);
      s.onerror = () => showChartFallback();
      document.head.appendChild(s);
      return;
    }
    showChartFallback();
    return;
  }
  const ct = chartTheme();
  const d = sliceChartData(data);
  const mpDates = d.market_prices.map(p => p.date);
  const mpVals = d.market_prices.map(p => p.close);
  // 策略预测价曲线（贯穿历史 + 外推未来 5 个交易日）
  const scDates = d.strategy_curve.map(p => p.date);
  const scPred = d.strategy_curve.map(p => p.predicted_price);
  const scLow = d.strategy_curve.map(p => p.predicted_low);
  const scHigh = d.strategy_curve.map(p => p.predicted_high);
  const allDates = [...new Set([...mpDates, ...scDates])].sort();

  // —— 历史策略建议标记：用「策略每日信号」(rolling_predict) 标到价格图上，方便回看历史买/卖/持有点 ——
  // 来源是策略对历史上每一交易日的判断（含 BUY/SELL/HOLD），天然对齐真实交易日，不会出现 undefined。
  const closeByDate = {};
  d.market_prices.forEach(m => { closeByDate[m.date] = m.close; });
  const sigByDate = {};
  d.strategy_curve.forEach(pt => { if (pt && pt.signal && closeByDate[pt.date] != null) sigByDate[pt.date] = pt; });
  const sigColor = s => s === "BUY" ? "#f85149" : s === "SELL" ? "#388bfd" : "#d29922";
  const predMarkers = d.strategy_curve
    .filter(pt => pt && pt.signal && closeByDate[pt.date] != null)
    .map(pt => {
      const isHold = pt.signal === "HOLD";
      const base = closeByDate[pt.date];
      const y = isHold ? base * 1.009 : (pt.signal === "BUY" ? base * 1.004 : base * 0.996); // 买卖/持有点略微偏离收盘价，避免被实线遮住
      return {
        value: [pt.date, y],
        signal: pt.signal,
        predicted_price: pt.predicted_price,
        predicted_low: pt.predicted_low,
        predicted_high: pt.predicted_high,
        symbol: isHold ? "diamond" : "triangle",
        symbolRotate: (!isHold && pt.signal === "SELL") ? 180 : 0,
        symbolSize: isHold ? 9 : 15,
        itemStyle: { color: sigColor(pt.signal), borderColor: ct.mark, borderWidth: 1, opacity: isHold ? 0.6 : 1 },
        label: { show: false },
      };
    });

  // —— 各策略逐日信号（用于悬浮提示）：每个交易日都记录，悬停即可看各策略当天建议（含无建议日）——
  const cColor = s => s === "BUY" ? "#f85149" : s === "SELL" ? "#388bfd" : "#3fb950";
  const consensusByDate = {};
  if (consensusData && consensusData.signals) {
    consensusData.signals.forEach(s => {
      consensusByDate[s.date] = s;
    });
  }

  // 若容器 DOM 已被重建（切换项目/重建主区），需重新初始化实例，否则 setOption 打在已脱离文档的画布上不可见
  const priceEl = document.getElementById("priceChart");
  if (!priceChart || priceChart.getDom() !== priceEl) {
    if (priceChart && priceChart.getDom()) try { priceChart.dispose(); } catch (e) {}
    priceChart = echarts.init(priceEl);
  }
  priceChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: function (ps) {
        const date = ps[0] ? ps[0].axisValue : "";
        const fmt = v => (v == null ? "—" : v);
        let html = `<div style="font-weight:600;margin-bottom:4px">${date}</div>`;
        const sig = sigByDate[date];
        if (sig) {
          html += `<div style="margin:3px 0"><span style="color:${sigColor(sig.signal)}">● ${sig.signal}</span> 策略历史建议 · 预测价 ${fmt(sig.predicted_price)}（${fmt(sig.predicted_low)}~${fmt(sig.predicted_high)}）</div>`;
        }
        const csig = consensusByDate[date];
        if (csig) {
          // 列出当天每个策略的操作建议（无建议的策略不显示，有几条列几条）
          const per = csig.per || {};
          const parts = [];
          (consensusData.strategies || []).forEach(st => {
            const sgn = per[st];
            if (sgn) parts.push(`<span style="color:${cColor(sgn)}">${(STRATEGIES[st] || {}).name || st}: ${sgn}</span>`);
          });
          if (parts.length) {
            html += `<div style="margin:3px 0;font-size:12px">各策略：${parts.join(" / ")}</div>`;
          }
        }
        for (const p of ps) {
          if (p.seriesName === "历史建议") continue;
          const v = Array.isArray(p.value) ? p.value[1] : p.value;
          if (v != null) html += `<div style="margin:3px 0">${p.marker}${p.seriesName}: ${v}</div>`;
        }
        return html;
      }
    },
    legend: { textStyle: { color: ct.axis }, data: ["实际行情", "策略预测价", "预测上界", "预测下界", "历史建议"] },
    xAxis: { type: "category", data: allDates, axisLabel: { color: ct.axis } },
    yAxis: { scale: true, axisLabel: { color: ct.axis } },
    series: [
      {
        name: "实际行情", type: "line", showSymbol: false,
        data: allDates.map(d => { const i = mpDates.indexOf(d); return i >= 0 ? mpVals[i] : null; }),
        itemStyle: { color: ct.actual }, lineStyle: { width: 1.6 }
      },
      {
        name: "策略预测价", type: "line", showSymbol: false, connectNulls: true,
        data: allDates.map(d => { const i = scDates.indexOf(d); return i >= 0 ? scPred[i] : null; }),
        lineStyle: { type: "dashed", width: 1.8, color: ct.gold }, itemStyle: { color: ct.gold }
      },
      {
        name: "预测上界", type: "line", showSymbol: false, connectNulls: true,
        data: allDates.map(d => { const i = scDates.indexOf(d); return i >= 0 ? scHigh[i] : null; }),
        lineStyle: { type: "dotted", width: 1, color: ct.goldSoft },
        itemStyle: { color: ct.goldSoft }
      },
      {
        name: "预测下界", type: "line", showSymbol: false, connectNulls: true,
        data: allDates.map(d => { const i = scDates.indexOf(d); return i >= 0 ? scLow[i] : null; }),
        lineStyle: { type: "dotted", width: 1, color: ct.goldSoft },
        itemStyle: { color: ct.goldSoft }
      },
      {
        name: "历史建议", type: "scatter", data: predMarkers, z: 10,
        emphasis: { focus: "series" }
      }
    ]
  }, true);

  const pnlEl = document.getElementById("pnlChart");
  if (!pnlChart || pnlChart.getDom() !== pnlEl) {
    if (pnlChart && pnlChart.getDom()) try { pnlChart.dispose(); } catch (e) {}
    pnlChart = echarts.init(pnlEl);
  }
  pnlChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: (v) => (v == null ? "-" : v) },
    legend: { textStyle: { color: ct.axis }, data: ["盈亏", "持仓收益率"] },
    xAxis: { type: "category", data: d.pnl_curve.map(p => p.date), axisLabel: { color: ct.axis } },
    yAxis: [
      { type: "value", name: "盈亏(元)", axisLabel: { color: ct.axis } },
      { type: "value", name: "持仓收益率%", axisLabel: { color: ct.axis, formatter: "{value}%" }, splitLine: { show: false } }
    ],
    series: [
      { name: "盈亏", type: "line", areaStyle: {}, data: d.pnl_curve.map(p => p.pnl),
        itemStyle: { color: ct.pnl },
        markLine: { silent: true, data: [{ yAxis: 0 }], lineStyle: { color: ct.axis } } },
      { name: "持仓收益率", type: "line", yAxisIndex: 1, connectNulls: false,
        data: d.pnl_curve.map(p => (p.pct == null ? null : p.pct)),
        itemStyle: { color: ct.gold }, lineStyle: { width: 1.5, color: ct.gold, type: "dashed" },
        emphasis: { focus: "series" } }
    ]
  });
  window.addEventListener("resize", () => { priceChart && priceChart.resize(); pnlChart && pnlChart.resize(); });
}

async function generateSuggestion() {
  await API("POST", `/api/projects/${currentId}/predict`, {});
  await refreshProjectView();
}

async function renderStrategyPanel(p) {
  const st = p.strategy_type || "momentum";
  const meta = STRATEGIES[st] || {};
  let cfg = {};
  try { cfg = JSON.parse(p.strategy_config || "{}") || {}; } catch (e) { cfg = {}; }
  const body = document.getElementById("strategy-body");
  let html = `<div class="row"><select id="str-type">` +
    Object.entries(STRATEGIES).map(([k, v]) => `<option value="${k}" ${k === st ? "selected" : ""}>${v.name}</option>`).join("") +
    `</select></div>`;
  if (meta.params) {
    html += `<div class="param-grid">`;
    for (const [key, pv] of Object.entries(meta.params)) {
      const val = (cfg[key] !== undefined && cfg[key] !== null) ? cfg[key] : pv.default;
      html += `<label class="param"><span>${pv.label}</span>` +
        `<input id="str-${key}" type="number" step="${pv.step}" min="${pv.min}" max="${pv.max}" value="${val}"></label>`;
    }
    html += `</div>`;
  }
  html += `<div class="muted">${meta.desc || ""}</div>`;
  body.innerHTML = html;
  document.getElementById("str-type").onchange = async () => {
    const nt = document.getElementById("str-type").value;
    const nm = STRATEGIES[nt] || {};
    const cfg2 = {};
    if (nm.params) for (const [k, pv] of Object.entries(nm.params)) cfg2[k] = pv.default;
    await renderStrategyPanel({ ...p, strategy_type: nt, strategy_config: JSON.stringify(cfg2) });
  };
  document.getElementById("btn-save-strategy").onclick = saveStrategy;
}

async function saveStrategy() {
  const st = document.getElementById("str-type").value;
  const meta = STRATEGIES[st] || {};
  const cfg = {};
  if (meta.params) {
    for (const key of Object.keys(meta.params)) {
      const el = document.getElementById("str-" + key);
      if (el) cfg[key] = parseFloat(el.value);
    }
  }
  // 保存策略配置
  await API("PATCH", `/api/projects/${currentId}`, {
    strategy_type: st, strategy_config: JSON.stringify(cfg),
  });
  // 同步本地缓存，便于后续流程读取最新配置
  const idx = projects.findIndex(x => x.id === currentId);
  if (idx >= 0) projects[idx] = { ...projects[idx], strategy_type: st, strategy_config: JSON.stringify(cfg) };
  // 用新策略重新生成建议，并就地刷新图表/建议区（不重建整个页面，避免图表实例脱离文档）
  await generateSuggestion();
  showToast("策略已保存，图表已自动刷新");
}

// 预取所有策略的逐日信号，供图表悬浮提示显示「各策略当天建议」（无需一致性卡片）
async function loadConsensusForHover() {
  const strategies = Object.keys(STRATEGIES).join(",");
  if (!strategies) return;
  const base = isGuest ? `/api/guest/projects/${currentId}/consensus` : `/api/projects/${currentId}/consensus`;
  const fn = isGuest ? guestApi : API;
  try {
    consensusData = await fn("GET",
      `${base}?strategies=${encodeURIComponent(strategies)}&threshold=1`);
  } catch (e) {
    consensusData = null; // 失败则悬浮不显示各策略，不影响其它功能
  }
}

// 图表时间范围选择（全部 / 30 / 60 / 90 / 180 / 365 天）
// 注意：每次进入项目/重绘面板时 HTML 里写死的 class="on" 只是默认值，
// 必须按全局 viewDays（跨股票保留的选择）同步实际高亮，否则切股票后高亮错位。
function setupRangePick() {
  const rp = document.getElementById("range-pick");
  if (!rp) return;
  rp.querySelectorAll("button").forEach(b => {
    const d = parseInt(b.getAttribute("data-d"), 10) || 0;
    b.classList.toggle("on", d === viewDays);   // 按上次选择恢复高亮
    b.onclick = () => {
      viewDays = d;
      rp.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      if (lastChartData) renderCharts(lastChartData);
    };
  });
}

function showToast(msg) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2200);
}

async function addPrice(e) {
  e.preventDefault();
  await API("POST", `/api/projects/${currentId}/prices`, {
    date: document.getElementById("px-date").value,
    close: parseFloat(document.getElementById("px-close").value)
  });
  document.getElementById("px-close").value = "";
  await refreshProjectView();
}

// 进入项目时自动拉取最新行情（限频：每天最多实际调用一次数据源；已是最新则跳过）
async function autoFetch(id) {
  try {
    const r = await API("POST", `/api/projects/${id}/fetch`, { days: 60, force: false });
    // 同步后项目名可能更新为股票名称，刷新侧栏标题
    if (r.name) {
      const proj = projects.find(x => x.id === id);
      if (proj && proj.name !== r.name) { proj.name = r.name; renderProjectList(); }
    }
  } catch (e) {
    // 自动拉取失败静默处理，不阻断用户查看已有数据
  }
}

async function fetchMarket() {
  const btn = document.getElementById("btn-fetch");
  const hint = document.getElementById("fetch-hint");
  btn.disabled = true; btn.textContent = "拉取中…";
  hint.textContent = "正在从行情源同步…";
  try {
    const days = parseInt(document.getElementById("fetch-days").value, 10);
    const r = await API("POST", `/api/projects/${currentId}/fetch`, { days, force: true });
    hint.textContent = `已同步 ${r.count} 个交易日（${r.start} ~ ${r.end}）`;
    alert(`已拉取 ${r.count} 个交易日（${r.start} ~ ${r.end}），最新收盘 ${r.latest_close}，来源 ${r.source}`);
    // 拉取后自动生成一条操作建议
    await API("POST", `/api/projects/${currentId}/predict`, {});
    // 同步后项目名已更新为股票名称，刷新列表与当前项目视图以显示新名称
    const proj = projects.find(x => x.id === currentId);
    if (proj && r.name) proj.name = r.name;
    await selectProject(currentId);
  } catch (e) {
    hint.textContent = "拉取失败：" + e.message;
    alert("拉取失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "🔄 强制刷新行情";
  }
}

async function submitFeedback(e) {
  e.preventDefault();
  await API("POST", `/api/projects/${currentId}/feedback`, {
    date: document.getElementById("fb-date").value,
    action: document.getElementById("fb-action").value,
    price: parseFloat(document.getElementById("fb-price").value),
    qty: parseFloat(document.getElementById("fb-qty").value) || 0,
    note: document.getElementById("fb-note").value
  });
  document.getElementById("fb-form").reset();
  document.getElementById("fb-date").value = today();
  await refreshProjectView();
}

async function deleteProject(id) {
  if (!confirm("确认删除该项目及其所有数据？")) return;
  await API("DELETE", `/api/projects/${id}`);
  currentId = null;
  await loadProjects();
}

async function savePosition() {
  await API("PATCH", `/api/projects/${currentId}`, {
    position_shares: parseFloat(document.getElementById("pos-shares").value) || 0,
    position_cost: parseFloat(document.getElementById("pos-cost").value) || 0,
  });
  await refreshProjectView();
  alert("仓位已保存");
}

async function saveStrategyNote() {
  const ta = document.getElementById("proj-strategy");
  const v = ta ? ta.value : "";
  try {
    await API("PATCH", `/api/projects/${currentId}`, { strategy: v });
    const idx = projects.findIndex(x => x.id === currentId);
    if (idx >= 0) projects[idx] = { ...projects[idx], strategy: v };
    const hint = document.getElementById("note-hint");
    if (hint) { hint.textContent = "已保存 ✓"; setTimeout(() => { hint.textContent = ""; }, 1500); }
    showToast("策略说明已保存");
  } catch (e) {
    const hint = document.getElementById("note-hint");
    if (hint) hint.textContent = "保存失败：" + (e.message || e);
  }
}

document.getElementById("project-form").onsubmit = async (e) => {
  e.preventDefault();
  await API("POST", "/api/projects", {
    name: document.getElementById("p-name").value,
    code: document.getElementById("p-code").value,
    market: document.getElementById("p-market").value,
    bias: document.getElementById("p-bias").value,
    strategy_type: document.getElementById("p-strategy-type").value,
    strategy: document.getElementById("p-strategy").value
  });
  e.target.reset();
  await loadProjects();
};

// ===== 登录门禁 / 多用户 =====
function boot() {
  if (!token) { enterGuest(); return; }   // 未登录默认进入游客模式，不弹登录窗
  API("GET", "/api/auth/me").then(u => {
    me = u; localStorage.setItem("qw_user", JSON.stringify(u));
    enterApp();
  }).catch(() => { doLogout(); enterGuest(); });
}

function enterApp() {
  hideAuth();
  document.querySelector(".sidebar")?.classList.remove("hidden");
  renderUserChip();
  renderSidebarForRole();
  loadProjects();
}

function showAuth(view) {
  document.body.classList.remove("guest-mode");   // 离开游客模式（移动端恢复顶部文字链接）
  document.getElementById("user-chip").classList.add("hidden");
  document.getElementById("nav-users").classList.add("hidden");
  document.getElementById("auth-screen").classList.remove("hidden");
  setAuthView(view || "login");
}
function hideAuth() { document.getElementById("auth-screen").classList.add("hidden"); }

function setAuthView(view) {
  document.getElementById("form-login").classList.toggle("hidden", view !== "login");
  document.getElementById("form-register").classList.toggle("hidden", view !== "register");
  document.getElementById("form-forgot").classList.toggle("hidden", view !== "forgot");
  document.getElementById("tab-login").classList.toggle("active", view === "login");
  document.getElementById("tab-register").classList.toggle("active", view === "register");
  const msg = document.getElementById("auth-msg");
  msg.textContent = ""; msg.style.color = "";
}

function onAuthSuccess(r) {
  token = r.token;
  me = { id: r.id || null, email: r.email, is_admin: !!r.is_admin };
  localStorage.setItem("qw_token", token);
  localStorage.setItem("qw_user", JSON.stringify(me));
  enterApp();
}

function doLogout() {
  if (token) API("POST", "/api/auth/logout").catch(() => {});
  token = ""; me = null;
  localStorage.removeItem("qw_token");
  localStorage.removeItem("qw_user");
}

function renderUserChip() {
  const chip = document.getElementById("user-chip");
  if (!me) { chip.classList.add("hidden"); return; }
  chip.classList.remove("hidden");
  chip.innerHTML = `<span class="uc-email">${escapeHtml(me.email)}</span>`
    + (me.is_admin ? `<span class="uc-badge">管理员</span>` : ``)
    + `<button id="btn-logout" class="btn-mini ghost">退出</button>`;
  document.getElementById("btn-logout").onclick = () => { doLogout(); enterGuest(); };
}

function renderSidebarForRole() {
  const admin = !!(me && me.is_admin);
  const nav = document.getElementById("nav-users");
  if (nav) nav.classList.toggle("hidden", !admin);
  const snav = document.getElementById("side-users");
  if (snav) snav.classList.toggle("hidden", !admin);
  // 圆桌入口：所有登录用户可见（生成/管理仅管理员可操作）
  const rt1 = document.getElementById("btn-roundtable");
  if (rt1) rt1.classList.remove("hidden");
  const rt2 = document.getElementById("side-roundtable");
  if (rt2) rt2.classList.remove("hidden");
}

// ===== 游客模式（只读 · 无需登录 · 仅展示 000002.SZ）=====
// 退出游客模式、回到登录门禁
function exitGuestToLogin() {
  isGuest = false;
  currentId = null;   // 游客项目 id 对登录态无效，强制登录后重新选择
  // 清空游客横幅 / 拉取状态 / 侧栏游客项目等残留，避免登录页遮罩下透出旧内容
  document.getElementById("main").innerHTML = "";
  document.getElementById("project-list").innerHTML = "";
  document.querySelector(".sidebar")?.classList.remove("hidden");
  renderSidebarForRole();
  showAuth("login");
}

// 进入游客模式：拉取 000002.SZ 公开项目（只读，无法增删/编辑）
async function enterGuest() {
  isGuest = true;
  token = "";
  document.body.classList.add("guest-mode");   // 移动端游客无抽屉，保留顶部文字链接
  hideAuth();
  document.getElementById("user-chip").classList.add("hidden");
  document.getElementById("nav-users")?.classList.add("hidden");
  document.getElementById("btn-roundtable")?.classList.add("hidden");
  document.getElementById("side-roundtable")?.classList.add("hidden");
  document.querySelector(".sidebar").classList.add("hidden");
  try {
    const list = await guestApi("GET", "/api/guest/projects");
    if (!list || !list.length) {
      document.getElementById("main").innerHTML = `
        <div class="panel"><h3>游客模式 · 只读</h3>
        <p class="muted">暂无可公开查看的 000002.SZ 项目。请先以管理员身份登录，创建代码为 000002.SZ 的项目并拉取行情。</p>
        <button class="btn-mini" id="g-login">登录 / 注册</button></div>`;
      document.getElementById("g-login").onclick = exitGuestToLogin;
      return;
    }
    projects = list;
    selectProject(list[0].id);
  } catch (e) {
    document.getElementById("main").innerHTML = `
      <div class="panel"><h3>游客模式</h3>
      <p class="muted">加载失败：${escapeHtml(e.message)}</p>
      <button class="btn-mini" id="g-login">登录 / 注册</button></div>`;
    document.getElementById("g-login").onclick = exitGuestToLogin;
  }
}

// 游客只读视图：仅图表 + 最新建议 + 行情表，无增删/编辑控件
async function renderGuestProject(p) {
  document.getElementById("main").innerHTML = `
    <div class="guest-banner">
      <div><b>游客模式</b> · 只读浏览 · 仅展示 ${escapeHtml(p.code || "")}</div>
      <div class="guest-banner-actions">
        <button class="btn-mini ghost" id="g-refresh">🔄 拉取最新行情</button>
        <button class="btn-mini ghost" id="g-login2">登录 / 注册</button>
      </div>
    </div>
    <div id="g-refresh-status" class="guest-refresh-status muted">正在检查最新行情…</div>
    <div class="panel">
      <h3>${p.name} <span class="muted">${p.code || ""} · ${p.market}</span></h3>
      <div id="note-wrap"></div>
    </div>
    <div class="panel"><h3>最新操作建议</h3><div id="suggestion" class="sug"><span class="muted">尚未生成</span></div></div>
    <div class="panel"><h3>预测线 vs 实际线
      <span class="range-pick" id="range-pick">
        <button data-d="0">全部</button>
        <button data-d="30">30天</button>
        <button data-d="60" class="on">60天</button>
        <button data-d="90">90天</button>
        <button data-d="180">180天</button>
        <button data-d="365">365天</button>
      </span>
    </h3><div id="priceChart" class="chart"></div></div>
    <div class="panel"><h3>盈亏曲线</h3><div id="pnlChart" class="chart"></div></div>
    <div class="panel">
      <h3>实际行情（实际线）</h3>
      <div class="table-wrap" style="margin-top:10px">
        <table id="price-table"><thead><tr><th>日期</th><th>收盘价</th></tr></thead><tbody></tbody></table>
      </div>
    </div>`;
  document.getElementById("g-login2").onclick = exitGuestToLogin;
  document.getElementById("g-refresh").onclick = guestRefresh;
  renderNoteView();
  await loadConsensusForHover();
  await refreshGuestView();
  setupRangePick();
  guestRefresh();   // 进入游客模式即自动检查/拉取最新行情（后端限频：每天仅实际拉取一次）
}

async function refreshGuestView() {
  if (!isGuest) return;   // 期间已退出游客模式（如点了登录），不再渲染
  const data = await guestApi("GET", `/api/guest/projects/${currentId}/chart`);
  fillSuggestion(data);
  fillPriceTable(data, 30);
  renderCharts(data);
}

// 游客模式：拉取最新行情（后端限频：每天仅实际调用一次数据源；当天数据已存在则提示无需重复）
async function guestRefresh() {
  const status = document.getElementById("g-refresh-status");
  const btn = document.getElementById("g-refresh");
  if (status) { status.textContent = "正在检查最新行情…"; status.className = "guest-refresh-status muted"; }
  if (btn) btn.disabled = true;
  try {
    const r = await guestApi("GET", `/api/guest/projects/${currentId}/refresh`);
    if (status) {
      status.textContent = r.message || "已完成行情检查";
      status.className = "guest-refresh-status " + (r.error ? "err" : (r.updated ? "ok" : "muted"));
    }
    if (r.updated) {
      await refreshGuestView();   // 重新拉取图表（含最新行情）
      showToast("行情已更新，图表已刷新");
    }
    // 同步后项目名已更新为股票名称，刷新侧栏与当前游客面板标题
    if (r.name) {
      const gp = projects.find(x => x.id === currentId);
      if (gp) gp.name = r.name;
      renderProjectList();
      const title = document.querySelector("#main .panel h3");
      if (title) title.innerHTML = `${r.name} <span class="muted">${gp ? (gp.code || "") : ""} · ${gp ? (gp.market || "A") : "A"}</span>`;
    }
  } catch (e) {
    if (status) { status.textContent = "行情检查失败：" + e.message; status.className = "guest-refresh-status err"; }
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 登录 / 注册 / 找回密码 表单绑定
document.getElementById("tab-login").onclick = () => setAuthView("login");
document.getElementById("tab-register").onclick = () => setAuthView("register");
document.getElementById("link-forgot").onclick = () => setAuthView("forgot");
document.getElementById("link-back").onclick = () => setAuthView("login");
document.getElementById("btn-guest").onclick = enterGuest;

document.getElementById("form-login").onsubmit = async (e) => {
  e.preventDefault();
  const msg = document.getElementById("auth-msg");
  try {
    const r = await API("POST", "/api/auth/login", {
      email: document.getElementById("login-email").value,
      password: document.getElementById("login-pw").value,
    });
    onAuthSuccess(r);
  } catch (err) { msg.style.color = "#f85149"; msg.textContent = "登录失败：" + err.message; }
};

document.getElementById("form-register").onsubmit = async (e) => {
  e.preventDefault();
  const msg = document.getElementById("auth-msg");
  const pw = document.getElementById("reg-pw").value;
  const pw2 = document.getElementById("reg-pw2").value;
  if (pw !== pw2) { msg.style.color = "#f85149"; msg.textContent = "两次输入的密码不一致"; return; }
  try {
    const r = await API("POST", "/api/auth/register", { email: document.getElementById("reg-email").value, password: pw });
    onAuthSuccess(r);
  } catch (err) { msg.style.color = "#f85149"; msg.textContent = "注册失败：" + err.message; }
};

document.getElementById("form-forgot").onsubmit = async (e) => {
  e.preventDefault();
  const msg = document.getElementById("auth-msg");
  try {
    await API("POST", "/api/auth/forgot-password", { email: document.getElementById("forgot-email").value });
    msg.style.color = "#3fb950";
    msg.textContent = "已提交，请等待管理员重置密码（重置后由管理员通过邮件告知新密码）。";
  } catch (err) { msg.style.color = "#f85149"; msg.textContent = "提交失败：" + err.message; }
};

boot();

// ===== 移动端抽屉菜单 =====
const menuBtn = document.getElementById("menu-btn");
const sidebar = document.querySelector(".sidebar");
const backdrop = document.getElementById("backdrop");
function openMenu() {
  if (sidebar.classList.contains("hidden")) return;   // 侧栏不可见时忽略（如游客模式），避免只弹灰色遮罩
  sidebar.classList.add("open"); backdrop.classList.add("show");
}
function closeMenu() { sidebar.classList.remove("open"); backdrop.classList.remove("show"); }
menuBtn.addEventListener("click", openMenu);
backdrop.addEventListener("click", closeMenu);

// 左上角标题：返回首页（游客→游客项目视图；登录→首个项目默认视图）
function goHome() {
  if (isGuest) {
    if (projects && projects.length) renderGuestProject(projects[0]);
    else exitGuestToLogin();
  } else if (projects && projects.length) {
    selectProject(projects[0].id);
  } else {
    loadProjects();
  }
}
document.getElementById("brand-link").addEventListener("click", goHome);

// 从「关于」页返回先前视图（游客模式无侧栏，需提供返回入口）
function backFromAbout() {
  if (isGuest) {
    if (projects && projects.length) renderGuestProject(projects[0]);
    else exitGuestToLogin();
  } else if (currentId) {
    selectProject(currentId);
  } else {
    loadProjects();
  }
}

// ===== 关于：项目框架与设计（右侧页面，非弹窗） =====
function renderAbout() {
  document.getElementById("main").innerHTML = `
    <div class="about-page">
      <button class="btn-mini ghost" id="about-back">← 返回</button>
      <h1>关于 · 慢量化操作台</h1>
      <p class="about-sub">项目框架、设计思路与相关内容</p>

      <h2>定位</h2>
      <p>面向 A 股的「慢量化」个人操作台：系统基于策略给出操作建议，由你手动执行并回填，再据此继续预测 —— 人机协作、不自动下单。支持多项目并行，从模拟仓位逐步过渡到实仓跟踪。</p>

      <h2>技术框架</h2>
      <ul>
        <li><b>前端</b>：原生 HTML/JS + ECharts（CDN），无构建步骤。</li>
        <li><b>后端</b>：FastAPI + SQLite（独立库，不依赖其它服务数据库）。</li>
        <li><b>部署</b>：IPv6 VPS，uvicorn 仅监听 127.0.0.1:8090，systemd 托管，nginx 反向代理对外；静态文件直出，改完即生效。</li>
      </ul>

      <h2>QuantMind 框架</h2>
      <ul>
        <li>设计理念源自开源框架 <b>QuantMind</b>（<a href="https://gitee.com/qusong0627/quantmind" target="_blank" rel="noopener">gitee.com/qusong0627/quantmind</a>，基于微软 Qlib 的 A 股量化预测框架）。</li>
        <li>借鉴其「预测 → 执行 → 反馈」的闭环思想，本项目自建轻量 Web 操作台；当前运行<b>不依赖 QuantMind / Qlib 代码</b>，预测引擎 <code>run_strategy()</code> 为纯函数，默认使用四套可解释的技术指标策略。</li>
        <li>引擎设计上可替换：如需接入 Qlib ML 预测，可新增实现并替换 <code>predictor.run_strategy()</code>；系统保持<b>不自动下单</b>，一切决策由人完成。</li>
      </ul>

      <h2>项目架构设计</h2>
      <ul>
        <li><b>前端（SPA）</b>：登录/游客双入口，项目面板、图表、策略设置、用户管理；主题持久化。</li>
        <li><b>API 层（FastAPI）</b>：鉴权（pbkdf2 + Bearer 会话）→ 项目/行情/预测/反馈/图表/游客接口。</li>
        <li><b>服务层</b>：<code>datasource.py</code> 行情多源回退（腾讯 → 新浪 → akshare → 东方财富）；<code>predictor.py</code> 四策略滚动预测。</li>
        <li><b>数据层</b>：独立 SQLite，项目 / 行情 / 预测 / 反馈 / 用户 / 会话，多用户按 owner 隔离。</li>
        <li><b>核心闭环</b>：建议 → 反馈 → 再预测；多项目并行、限频拉取、游客只读。</li>
      </ul>

      <h2>数据层</h2>
      <ul>
        <li>行情接入多源回退：腾讯 gtimg → 新浪 → akshare → 东方财富，单源限流自动切换。</li>
        <li>仅支持 A 股（含前复权处理）。</li>
      </ul>

      <h2>策略引擎</h2>
      <ul>
        <li>四套可配置策略：<b>动量 momentum</b> / <b>均值回归 meanreversion</b> / <b>通道突破 breakout</b> / <b>基线演示 baseline</b>。</li>
        <li>参数可在「策略设置」面板在线调整并即时重算预测线。</li>
      </ul>

      <h2>预测与图表</h2>
      <ul>
        <li>「滚动预测」对历史每个交易日用截至当日数据计算策略信号，并外推未来 5 个交易日。</li>
        <li>金色虚线 = 策略预测价 + 上下界区间带；蓝线 = 实际行情。</li>
        <li>历史信号标记：红▲买 / 蓝▼卖 / 绿◆HOLD；悬停任意一天可查看各策略当日建议。</li>
      </ul>

      <h2>决策闭环</h2>
      <ul>
        <li>建议 → 你反馈实际买卖 → 系统重新预测；可录入当前持仓（数量/成本），自动回放盈亏。</li>
      </ul>

      <h2>安全</h2>
      <ul>
        <li>已启用多用户登录：用户名为邮箱地址，首个注册用户自动成为管理员。</li>
        <li>普通用户仅能访问自己的项目；管理员额外拥有「用户管理」页面，可处理用户的“忘记密码”申请并重置为随机密码。</li>
      </ul>

      <p class="about-foot">版本 v9 · 仅本地/自部署用途 · <a href="https://github.com/tojoevan/QuantMindLite" target="_blank" rel="noopener">GitHub 仓库</a></p>
    </div>`;
  const back = document.getElementById("about-back");
  if (back) back.onclick = backFromAbout;
}
document.getElementById("btn-about").onclick = () => { renderAbout(); if (isMobile()) closeMenu(); };
document.getElementById("nav-users").onclick = () => { if (me && me.is_admin) { renderUsers(); if (isMobile()) closeMenu(); } };
// 移动端抽屉内的顶部链接（桌面隐藏）
document.getElementById("side-about").onclick = () => { renderAbout(); if (isMobile()) closeMenu(); };
document.getElementById("side-users").onclick = () => { if (me && me.is_admin) { renderUsers(); if (isMobile()) closeMenu(); } };
document.getElementById("btn-theme").onclick = toggleTheme;

// ===== 管理员：用户管理（密码重置） =====
async function renderUsers(flash) {
  if (!(me && me.is_admin)) { showAuth("login"); return; }
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h3>用户管理 <span class="muted">仅管理员可见</span></h3>
      <div id="users-content"><span class="muted">加载中…</span></div>
    </div>`;
  try {
    const [reqs, users] = await Promise.all([
      API("GET", "/api/auth/admin/reset-requests").catch(() => []),
      API("GET", "/api/auth/admin/users").catch(() => []),
    ]);
    const reqHtml = reqs.length
      ? reqs.map(r => `
        <div class="user-row">
          <div><b>${escapeHtml(r.email)}</b><span class="muted"> · 申请于 ${escapeHtml(r.requested_at || "")}</span></div>
          <button class="btn-mini" data-reset="${r.user_id}">重置并生成密码</button>
        </div>`).join("")
      : `<div class="muted">暂无待处理申请</div>`;
    const userHtml = users.map(u => `
      <div class="user-row">
        <div>
          <b>${escapeHtml(u.email)}</b>
          ${u.is_admin ? '<span class="uc-badge">管理员</span>' : ''}
          ${u.disabled ? '<span class="muted">已禁用</span>' : ''}
        </div>
        <div class="row-actions">
          <button class="btn-mini" data-reset="${u.id}">重置密码</button>
          ${u.id === me.id ? '' : `<button class="btn-mini danger" data-del="${u.id}">删除</button>`}
        </div>
      </div>`).join("");
    document.getElementById("users-content").innerHTML = `
      <h4>待重置申请</h4>
      <div class="user-list">${reqHtml}</div>
      <h4>全部用户</h4>
      <div class="user-list">${userHtml}</div>
      <div class="muted" style="margin-top:10px">点击「重置并生成密码」后，系统将该用户密码重置为随机密码并显示在下方，请通过邮件把新密码发送给对应用户。</div>
      <div class="muted" style="margin-top:6px">「删除」会<b>不可恢复</b>地移除该用户及其全部项目、行情、预测与反馈数据，并注销其会话；当前登录账户与唯一管理员不可删除。</div>
      <div id="reset-result" class="reset-result hidden"></div>`;

    if (flash) {
      const box = document.getElementById("reset-result");
      box.classList.remove("hidden");
      box.innerHTML = `已为 <b>${escapeHtml(flash.email)}</b> 重置密码：<code class="pw">${escapeHtml(flash.new_password)}</code>
        <button id="copy-pw" class="btn-mini">复制密码</button>
        <div class="muted">请通过邮件将该密码发送给用户，并告知其尽快登录修改。</div>`;
      const cp = document.getElementById("copy-pw");
      if (cp) cp.onclick = () => navigator.clipboard.writeText(flash.new_password).then(() => showToast("已复制密码")).catch(() => {});
    }

    document.querySelectorAll("[data-reset]").forEach(btn => {
      btn.onclick = async () => {
        const uid = parseInt(btn.getAttribute("data-reset"), 10);
        try {
          const r = await API("POST", "/api/auth/admin/reset-password", { user_id: uid });
          renderUsers(r);  // 刷新：该申请已标记为已处理
        } catch (err) { showToast("重置失败：" + err.message); }
      };
    });

    document.querySelectorAll("[data-del]").forEach(btn => {
      btn.onclick = async () => {
        const uid = parseInt(btn.getAttribute("data-del"), 10);
        const row = btn.closest(".user-row");
        const email = row ? row.querySelector("b")?.textContent : "该用户";
        if (!confirm(`确定删除用户「${email}」吗？\n该操作不可恢复：将一并删除其所有项目、行情、预测与反馈数据，并注销其登录会话。`)) return;
        try {
          const r = await API("DELETE", `/api/auth/admin/users/${uid}`);
          showToast(`已删除用户 ${r.deleted}`);
          renderUsers();  // 刷新列表
        } catch (err) { showToast("删除失败：" + err.message); }
      };
    });
  } catch (err) {
    document.getElementById("users-content").innerHTML = `<div class="muted">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

// ===== 圆桌讨论（多 AI 席位 · 双通道：自动席位配 Key 直调 LLM / 手动席位复制提示词外部咨询后回填） =====
const RT_PROVIDERS = {
  deepseek: { base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  zhipu: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-air" },
  moonshot: { base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
};
// 手动席位可选配置的「AI 角色」（预设下拉 + 自定义），仅作身份标签与提示词注入
const RT_ROLES = ["豆包", "DeepSeek", "元宝", "Kimi", "通义千问", "ChatGPT", "Claude", "Gemini", "文心一言", "其他"];
let rtState = { date: null, snap: null, seats: [], posts: {}, settings: null, history: null, admin: false };
const rtAdmin = () => !!(me && me.is_admin);

function backFromRoundtable() {
  if (isGuest) { exitGuestToLogin(); return; }
  if (currentId) selectProject(currentId);
  else loadProjects();
}

async function renderRoundtable() {
  if (isGuest || !token) { showAuth("login"); return; }
  rtState.admin = rtAdmin();
  document.getElementById("main").innerHTML = `
    <div class="rt-page">
      <div class="rt-head">
        <button class="btn-mini ghost" id="rt-back">← 返回</button>
        <div>
          <h1>圆桌讨论</h1>
          <p class="rt-sub">多个 AI 席位基于今日大盘与你的持仓独立给出建议 · <span id="rt-date">加载中…</span></p>
        </div>
        <div class="rt-head-actions">
          <button class="btn-mini" id="rt-generate-all">生成今日讨论</button>
          <button class="btn-mini ghost" id="rt-manage-toggle">席位管理</button>
          <button class="btn-mini ghost" id="rt-settings-toggle">AI 设置</button>
        </div>
      </div>
      <div class="panel hidden" id="rt-manage-panel"><h3>席位管理 <span class="muted">增删席位、编辑人格与通道</span></h3><div id="rt-manage-body"><span class="muted">加载中…</span></div></div>
      <div class="panel hidden" id="rt-settings-panel"><h3>AI 设置 <span class="muted">API Key 仅存服务器本地数据库，页面不回显</span></h3><div id="rt-settings-body"><span class="muted">加载中…</span></div></div>
      <div class="panel"><h3>今日大盘</h3><div class="rt-indices" id="rt-indices"><span class="muted">加载中…</span></div></div>
      <div class="panel"><h3>持仓总览 <span class="muted" id="rt-holdings-count"></span></h3>
        <div class="table-wrap"><table id="rt-holdings"><thead><tr><th>标的</th><th>最新价</th><th>市值(元)</th><th>收益率</th><th>今日</th><th>系统建议</th></tr></thead><tbody></tbody></table></div>
      </div>
      <div class="panel" id="rt-history-panel">
        <h3>历史标的与操作记录 <span class="muted" id="rt-history-count"></span><button class="btn-mini ghost" id="rt-history-toggle">收起</button></h3>
        <div id="rt-history-body">
          <div class="rt-hist-sub">历史标的 <span class="muted" id="rt-hist-sym-count"></span></div>
          <div class="table-wrap"><table id="rt-hist-symbols"><thead><tr><th>标的</th><th>状态</th><th>持仓(份)</th><th>成本</th><th>操作数</th><th>累计实现盈亏(元)</th><th>最近操作</th></tr></thead><tbody></tbody></table></div>
          <div class="rt-hist-sub">仓位操作记录 <span class="muted" id="rt-hist-ops-count"></span></div>
          <div class="table-wrap rt-scroll"><table id="rt-hist-ops"><thead><tr><th>日期</th><th>标的</th><th>操作</th><th>价格</th><th>数量</th><th>实现盈亏(元)</th><th>备注</th></tr></thead><tbody></tbody></table></div>
        </div>
      </div>
      <div class="panel">
        <h3>AI 席位 <span class="muted">自动席位：后端直调 LLM · 手动席位：复制提示词去外部咨询后回填</span></h3>
        <div class="rt-seats" id="rt-seats"><span class="muted">加载中…</span></div>
      </div>
    </div>`;
  document.getElementById("rt-back").onclick = backFromRoundtable;
  document.getElementById("rt-generate-all").onclick = () => rtGenerate(null);
  document.getElementById("rt-manage-toggle").onclick = () => {
    if (!rtState.admin) { showToast("仅管理员可管理席位"); return; }
    document.getElementById("rt-manage-panel").classList.toggle("hidden");
  };
  document.getElementById("rt-settings-toggle").onclick = () => {
    if (!rtState.admin) { showToast("仅管理员可修改 AI 设置"); return; }
    document.getElementById("rt-settings-panel").classList.toggle("hidden");
  };
  document.getElementById("rt-history-toggle").onclick = () => {
    const body = document.getElementById("rt-history-body");
    const btn = document.getElementById("rt-history-toggle");
    const hidden = body.classList.toggle("hidden");
    btn.textContent = hidden ? "展开" : "收起";
  };
  await rtLoadAll();
}

async function rtLoadAll() {
  try {
    const [seats, posts, snap, hist] = await Promise.all([
      API("GET", "/api/roundtable/seats"),
      API("GET", "/api/roundtable/posts"),
      API("GET", "/api/roundtable/snapshot"),
      API("GET", "/api/roundtable/history").catch(() => null),
    ]);
    rtState.seats = seats || [];
    rtState.snap = snap;
    rtState.date = snap ? snap.date : null;
    rtState.history = hist;
    rtState.posts = {};
    (posts || []).forEach(p => { rtState.posts[p.seat_id] = p; });
    if (rtState.admin) {
      rtState.settings = await API("GET", "/api/roundtable/settings").catch(() => null);
    }
  } catch (e) {
    showToast("圆桌数据加载失败：" + e.message);
    return;
  }
  rtRenderAll();
}

function rtRenderAll() {
  const d = document.getElementById("rt-date");
  if (d) d.textContent = rtState.date || "";
  rtRenderIndices();
  rtRenderHoldings();
  rtRenderHistory();
  rtRenderSeats();
  if (rtState.admin) { rtRenderManage(); rtRenderSettings(); }
}

function rtChgCls(v) { return v == null ? "flat" : (v > 0 ? "pos" : (v < 0 ? "neg" : "flat")); }
function rtFmtChg(v) { return v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`; }

function rtRenderIndices() {
  const wrap = document.getElementById("rt-indices");
  if (!wrap) return;
  const idx = (rtState.snap && rtState.snap.indices) || [];
  if (!idx.length) { wrap.innerHTML = `<span class="muted">大盘数据获取失败（不影响持仓点评）</span>`; return; }
  wrap.innerHTML = idx.map(i => `
    <div class="rt-index">
      <div class="name">${escapeHtml(i.name)}</div>
      <div class="val">${i.close == null ? "—" : i.close}</div>
      <div class="chg ${rtChgCls(i.chg_pct)}">${rtFmtChg(i.chg_pct)}${i.date ? ` <span class="muted">${escapeHtml(i.date)}</span>` : ""}</div>
    </div>`).join("");
}

function rtRenderHoldings() {
  const tb = document.querySelector("#rt-holdings tbody");
  if (!tb) return;
  const hs = (rtState.snap && rtState.snap.holdings) || [];
  const cnt = document.getElementById("rt-holdings-count");
  if (cnt) cnt.textContent = hs.length ? `共 ${hs.length} 个标的` : "";
  if (!hs.length) {
    tb.innerHTML = `<tr><td colspan="6" class="muted">暂无持仓项目，圆桌将只点评大盘。</td></tr>`;
    return;
  }
  tb.innerHTML = hs.map(h => `
    <tr>
      <td>${escapeHtml(h.name)}${h.code ? ` <span class="muted">${escapeHtml(h.code)}</span>` : ""}${h.is_fund ? `<span class="rt-badge">基金</span>` : ""}</td>
      <td>${h.latest_close == null ? "—" : h.latest_close}<div class="muted">${escapeHtml(h.latest_date || "")}</div></td>
      <td>${h.market_value == null ? "—" : h.market_value}</td>
      <td class="${rtChgCls(h.return_pct)}">${h.return_pct == null ? "—" : rtFmtChg(h.return_pct)}</td>
      <td class="${rtChgCls(h.day_chg_pct)}">${rtFmtChg(h.day_chg_pct)}</td>
      <td>${h.latest_signal ? escapeHtml(h.latest_signal) : "—"}</td>
    </tr>`).join("");
}

const RT_HIST_STATUS = {
  holding:   { label: "持仓中", cls: "state-done" },
  cleared:   { label: "已清仓", cls: "state-cleared" },
  untracked: { label: "未建仓", cls: "state-pending" },
};
const RT_HIST_ACTION = {
  BUY:  { label: "买入", cls: "pos" },
  SELL: { label: "卖出", cls: "neg" },
  HOLD: { label: "持有", cls: "flat" },
};

function rtRenderHistory() {
  const symTb = document.querySelector("#rt-hist-symbols tbody");
  const opsTb = document.querySelector("#rt-hist-ops tbody");
  if (!symTb || !opsTb) return;
  const h = rtState.history;
  const cnt = document.getElementById("rt-history-count");
  if (!h) {
    if (cnt) cnt.textContent = "加载失败";
    symTb.innerHTML = `<tr><td colspan="7" class="muted">历史数据加载失败（不影响今日讨论）</td></tr>`;
    opsTb.innerHTML = "";
    return;
  }
  const syms = h.symbols || [], ops = h.operations || [];
  const panel = document.getElementById("rt-history-panel");
  if (!syms.length && !ops.length) {
    if (panel) panel.classList.add("hidden");   // 无历史可展示时整面板隐藏
    return;
  }
  if (panel) panel.classList.remove("hidden");
  if (cnt) cnt.textContent = `${syms.length} 个标的 · ${ops.length} 条操作`;
  const sc = document.getElementById("rt-hist-sym-count");
  if (sc) sc.textContent = syms.length ? `共 ${syms.length} 个` : "";
  const oc = document.getElementById("rt-hist-ops-count");
  if (oc) oc.textContent = ops.length ? `共 ${ops.length} 条` : "";
  if (!syms.length) {
    symTb.innerHTML = `<tr><td colspan="7" class="muted">暂无项目</td></tr>`;
  } else {
    symTb.innerHTML = syms.map(s => {
      const st = RT_HIST_STATUS[s.status] || RT_HIST_STATUS.untracked;
      return `<tr>
        <td>${escapeHtml(s.name)}${s.code ? ` <span class="muted">${escapeHtml(s.code)}</span>` : ""}${s.is_fund ? `<span class="rt-badge">基金</span>` : ""}</td>
        <td><span class="rt-badge ${st.cls}">${st.label}</span></td>
        <td>${s.shares > 0 ? s.shares : "—"}</td>
        <td>${s.cost > 0 ? s.cost : "—"}</td>
        <td>${s.op_count || "—"}</td>
        <td class="${rtChgCls(s.realized_pnl)}">${s.op_count ? s.realized_pnl : "—"}</td>
        <td>${escapeHtml(s.last_date || "—")}</td>
      </tr>`;
    }).join("");
  }
  if (!ops.length) {
    opsTb.innerHTML = `<tr><td colspan="7" class="muted">暂无操作记录——在项目页「反馈」里录入的买卖会自动汇总到这里。</td></tr>`;
  } else {
    opsTb.innerHTML = ops.map(o => {
      const a = RT_HIST_ACTION[o.action] || { label: o.action, cls: "flat" };
      return `<tr>
        <td>${escapeHtml(o.date)}</td>
        <td>${escapeHtml(o.name)}${o.code ? ` <span class="muted">${escapeHtml(o.code)}</span>` : ""}</td>
        <td class="${a.cls}">${a.label}</td>
        <td>${o.price}</td>
        <td>${o.qty}</td>
        <td class="${rtChgCls(o.realized_pnl)}">${o.realized_pnl}</td>
        <td class="muted">${escapeHtml(o.note || "")}</td>
      </tr>`;
    }).join("");
  }
}

function rtRenderSeats() {
  const wrap = document.getElementById("rt-seats");
  if (!wrap) return;
  const seats = (rtState.seats || []).filter(s => s.enabled);
  if (!seats.length) { wrap.innerHTML = `<span class="muted">暂无可用席位，请在「席位管理」中添加。</span>`; return; }
  wrap.innerHTML = "";
  seats.forEach(s => {
    const post = rtState.posts[s.id];
    const isAuto = s.mode === "auto";
    const card = document.createElement("div");
    card.className = "rt-seat";
    const stateBadge = post
      ? `<span class="rt-badge state-done">${post.source === "auto" ? "已生成" : "已录入"}</span>`
      : `<span class="rt-badge state-pending">待生成</span>`;
    let actions = "";
    if (isAuto) {
      actions = `<button class="btn-mini" data-gen="${s.id}">${post ? "重新生成" : "生成"}</button>`;
      if (!s.has_key) actions += `<span class="rt-inline-note">未配置 Key</span>`;
    } else {
      actions = `<button class="btn-mini" data-copy="${s.id}">复制提示词</button>`
        + `<button class="btn-mini ghost" data-paste="${s.id}">粘贴反馈</button>`;
    }
    const meta = post
      ? `${post.source === "auto" ? `模型 ${escapeHtml(post.model || "—")} · ` : ""}更新于 ${escapeHtml((post.updated_at || "").replace("T", " ").slice(0, 19))}`
      : "";
    card.innerHTML = `
      <div class="rt-seat-head">
        <div class="rt-seat-title">
          <span class="rt-seat-name">${escapeHtml(s.name)}</span>
          <span class="rt-badge ${isAuto ? "mode-auto" : ""}">${isAuto ? "自动" : "手动"}</span>
          ${s.role ? `<span class="rt-role">${escapeHtml(s.role)}</span>` : ""}
          ${stateBadge}
        </div>
        <div class="rt-seat-actions">${actions}</div>
      </div>
      <div class="rt-seat-body ${post ? "" : "empty"}">${post ? escapeHtml(post.content) : "尚未生成：点击右上角按钮开始"}</div>
      ${meta ? `<div class="rt-seat-meta">${meta}</div>` : ""}`;
    wrap.appendChild(card);
  });
  wrap.querySelectorAll("[data-copy]").forEach(b => b.onclick = () => rtCopyPrompt(parseInt(b.getAttribute("data-copy"), 10)));
  wrap.querySelectorAll("[data-paste]").forEach(b => b.onclick = () => rtOpenPaste(parseInt(b.getAttribute("data-paste"), 10)));
  wrap.querySelectorAll("[data-gen]").forEach(b => b.onclick = () => rtGenerate([parseInt(b.getAttribute("data-gen"), 10)]));
}

async function rtRefreshPosts() {
  const posts = await API("GET", "/api/roundtable/posts");
  rtState.posts = {};
  (posts || []).forEach(p => { rtState.posts[p.seat_id] = p; });
  rtRenderSeats();
}

async function rtReloadSeats() {
  rtState.seats = await API("GET", "/api/roundtable/seats");
  await rtRefreshPosts();
  rtRenderManage();
}

async function rtReloadSettings() {
  rtState.settings = await API("GET", "/api/roundtable/settings").catch(() => null);
  rtState.seats = await API("GET", "/api/roundtable/seats");
  rtRenderSettings();
  rtRenderSeats();
}

// ---- 手动通道：复制提示词（剪贴板不可用时退化为弹窗展示） ----
async function rtCopyPrompt(seatId) {
  const btn = document.querySelector(`[data-copy="${seatId}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  const seat = rtState.seats.find(x => x.id === seatId);
  const toRole = (seat && seat.role) ? `，去 ${seat.role} 网页` : "，去外部 AI 网页";
  try {
    const r = await API("GET", `/api/roundtable/prompt?seat_id=${seatId}`);
    try {
      await navigator.clipboard.writeText(r.prompt);
      showToast("提示词已复制" + toRole + "粘贴咨询吧");
    } catch (clipErr) {
      rtShowTextModal(`提示词 · ${r.seat_name}（${r.date}）`, r.prompt,
        "剪贴板不可用，请手动全选复制以下内容", false);
    }
  } catch (e) {
    showToast("提示词生成失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "复制提示词"; }
  }
}

// ---- 手动通道：粘贴外部 AI 的反馈回填 ----
function rtOpenPaste(seatId) {
  const s = rtState.seats.find(x => x.id === seatId);
  rtShowTextModal(`粘贴反馈 · ${s ? s.name : ""}`, "",
    "将外部 AI（如 ChatGPT / Claude 网页版）对该席位提示词的回复完整粘贴到下方，保存后展示在席位卡片中（再次粘贴可覆盖）。",
    true, async () => {
      const ta = document.getElementById("rt-modal-text");
      const content = (ta && ta.value || "").trim();
      if (!content) { showToast("内容不能为空"); return false; }
      await API("POST", "/api/roundtable/posts", { seat_id: seatId, content });
      return true;
    });
}

// ---- 通用文本弹窗（提示词展示 / 反馈粘贴） ----
function rtShowTextModal(title, text, hint, editable, onSave) {
  const old = document.getElementById("rt-modal-root");
  if (old) old.remove();
  const root = document.createElement("div");
  root.className = "rt-modal";
  root.id = "rt-modal-root";
  root.innerHTML = `
    <div class="rt-modal-card">
      <h3>${escapeHtml(title)}</h3>
      ${hint ? `<p class="rt-inline-note">${escapeHtml(hint)}</p>` : ""}
      <textarea id="rt-modal-text" rows="16" ${editable ? "" : "readonly"}>${escapeHtml(text)}</textarea>
      <div class="rt-modal-actions">
        ${onSave ? `<button class="btn-mini" id="rt-modal-ok">保存反馈</button>` : ""}
        ${!editable ? `<button class="btn-mini ghost" id="rt-modal-select">全选</button>` : ""}
        <button class="btn-mini ghost" id="rt-modal-close">${onSave ? "取消" : "关闭"}</button>
      </div>
    </div>`;
  document.body.appendChild(root);
  document.getElementById("rt-modal-close").onclick = () => root.remove();
  root.onclick = (e) => { if (e.target === root) root.remove(); };
  const ta = document.getElementById("rt-modal-text");
  const selBtn = document.getElementById("rt-modal-select");
  if (selBtn) selBtn.onclick = () => { ta.focus(); ta.select(); };
  if (ta && editable) ta.focus();
  if (onSave) {
    document.getElementById("rt-modal-ok").onclick = async () => {
      const btn = document.getElementById("rt-modal-ok");
      btn.disabled = true; btn.textContent = "保存中…";
      try {
        const ok = await onSave();
        if (ok !== false) {
          root.remove();
          showToast("已保存到席位");
          await rtRefreshPosts();
        } else {
          btn.disabled = false; btn.textContent = "保存反馈";
        }
      } catch (e) {
        showToast("保存失败：" + e.message);
        btn.disabled = false; btn.textContent = "保存反馈";
      }
    };
  }
}

// ---- 自动通道：调用 LLM 生成 ----
async function rtGenerate(seatIds) {
  const btnAll = document.getElementById("rt-generate-all");
  const targetBtn = seatIds && seatIds.length === 1
    ? document.querySelector(`[data-gen="${seatIds[0]}"]`) : null;
  const btn = targetBtn || btnAll;
  const btnLabel = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  try {
    const r = await API("POST", "/api/roundtable/generate", seatIds ? { seat_ids: seatIds } : {});
    const results = r.results || [];
    if (!results.length) {
      showToast(r.message || "没有可自动生成的席位（需将席位设为「自动」并配置 Key）");
    } else {
      const okCount = results.filter(x => x.ok).length;
      const fails = results.filter(x => !x.ok);
      if (okCount && !fails.length) showToast(`已生成 ${okCount} 条观点`);
      else if (okCount) showToast(`生成 ${okCount} 条，失败 ${fails.length} 条：${fails[0].error || ""}`);
      else showToast("生成失败：" + (fails[0] ? fails[0].error : "未知错误"));
    }
    await rtRefreshPosts();
  } catch (e) {
    showToast("生成失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btnLabel; }
  }
}

// ---- 席位管理 ----
function rtRenderManage() {
  const body = document.getElementById("rt-manage-body");
  if (!body) return;
  const seats = rtState.seats || [];
  const rows = seats.map(s => `
    <div class="rt-manage-row">
      <div class="row">
        <label class="rt-field" style="width:130px">席位名<input type="text" data-f="name" value="${escapeHtml(s.name)}"></label>
        <label class="rt-field" style="width:110px">通道
          <select data-f="mode">
            <option value="manual" ${s.mode === "manual" ? "selected" : ""}>手动</option>
            <option value="auto" ${s.mode === "auto" ? "selected" : ""}>自动</option>
          </select>
        </label>
        <label class="rt-field grow">人格 / 风格提示词<textarea rows="2" data-f="persona">${escapeHtml(s.persona)}</textarea></label>
        <label class="rt-field" style="width:120px">AI 角色<input type="text" data-f="role" value="${escapeHtml(s.role)}" list="rt-role-list" placeholder="豆包/元宝…"></label>
      </div>
      <div class="row">
        <label class="rt-field" style="width:150px">提供方
          <select data-f="provider">
            ${Object.keys(RT_PROVIDERS).map(p => `<option value="${p}" ${s.provider === p ? "selected" : ""}>${p}</option>`).join("")}
          </select>
        </label>
        <label class="rt-field grow">模型（留空用默认）<input type="text" data-f="model" value="${escapeHtml(s.model)}" placeholder="${escapeHtml((RT_PROVIDERS[s.provider] || {}).model || "")}"></label>
        <label class="chk"><input type="checkbox" data-f="enabled" ${s.enabled ? "checked" : ""}>启用</label>
        <button class="btn-mini" data-save="${s.id}">保存</button>
        <button class="btn-mini danger" data-del-seat="${s.id}">删除</button>
        ${s.mode === "auto" && !s.has_key ? `<span class="rt-inline-note">未配置 Key</span>` : ""}
      </div>
    </div>`).join("");
  body.innerHTML = `
    <datalist id="rt-role-list">${RT_ROLES.map(r => `<option value="${r}"></option>`).join("")}</datalist>
    <h4>席位列表</h4>${rows || '<div class="muted">暂无席位</div>'}
    <h4>新增席位</h4>
    <form id="rt-seat-add" class="rt-manage-row">
      <div class="row">
        <label class="rt-field" style="width:130px">席位名<input type="text" name="name" required placeholder="如：价值派"></label>
        <label class="rt-field" style="width:110px">通道
          <select name="mode"><option value="manual">手动</option><option value="auto">自动</option></select>
        </label>
        <label class="rt-field grow">人格 / 风格提示词<textarea name="persona" rows="2" placeholder="该席位的分析风格与偏好…"></textarea></label>
        <label class="rt-field" style="width:120px">AI 角色<input type="text" name="role" list="rt-role-list" placeholder="豆包/元宝…"></label>
      </div>
      <div class="row">
        <label class="rt-field" style="width:150px">提供方
          <select name="provider">${Object.keys(RT_PROVIDERS).map(p => `<option value="${p}">${p}</option>`).join("")}</select>
        </label>
        <label class="rt-field grow">模型（留空用默认）<input type="text" name="model"></label>
        <button class="btn-mini" type="submit">添加席位</button>
      </div>
    </form>`;
  body.querySelectorAll("[data-save]").forEach(b => b.onclick = async () => {
    const row = b.closest(".rt-manage-row");
    const id = parseInt(b.getAttribute("data-save"), 10);
    const q = (f) => row.querySelector(`[data-f="${f}"]`);
    const payload = {
      name: q("name").value.trim() || "未命名席位",
      persona: q("persona").value,
      mode: q("mode").value,
      provider: q("provider").value,
      model: q("model").value.trim(),
      role: q("role").value.trim(),
      enabled: q("enabled").checked ? 1 : 0,
    };
    try {
      await API("PATCH", `/api/roundtable/seats/${id}`, payload);
      showToast("席位已保存");
      await rtReloadSeats();
    } catch (e) { showToast("保存失败：" + e.message); }
  });
  body.querySelectorAll("[data-del-seat]").forEach(b => b.onclick = async () => {
    const id = parseInt(b.getAttribute("data-del-seat"), 10);
    if (!confirm("确认删除该席位及其全部历史观点？")) return;
    try {
      await API("DELETE", `/api/roundtable/seats/${id}`);
      showToast("席位已删除");
      await rtReloadSeats();
    } catch (e) { showToast("删除失败：" + e.message); }
  });
  document.getElementById("rt-seat-add").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const q = (n) => f.querySelector(`[name="${n}"]`);
    try {
      await API("POST", "/api/roundtable/seats", {
        name: q("name").value.trim() || "新席位",
        persona: q("persona").value,
        mode: q("mode").value,
        provider: q("provider").value,
        model: q("model").value.trim(),
        role: q("role").value.trim(),
      });
      f.reset();
      showToast("席位已添加");
      await rtReloadSeats();
    } catch (err) { showToast("添加失败：" + err.message); }
  };
}

// ---- AI 设置（API Key，脱敏保存） ----
function rtRenderSettings() {
  const body = document.getElementById("rt-settings-body");
  if (!body) return;
  const cur = (rtState.settings && rtState.settings.providers) || {};
  body.innerHTML = Object.keys(RT_PROVIDERS).map(p => {
    const c = cur[p] || {};
    return `
    <div class="rt-settings-row">
      <div class="row">
        <span class="rt-seat-name" style="width:110px">${p}</span>
        <label class="rt-field grow">base_url<input type="text" data-f="base_url" value="${escapeHtml(c.base_url || RT_PROVIDERS[p].base_url)}"></label>
        <label class="rt-field grow">model<input type="text" data-f="model" value="${escapeHtml(c.model || RT_PROVIDERS[p].model)}"></label>
      </div>
      <div class="row">
        <label class="rt-field grow">API Key ${c.configured ? "（已配置 · 留空保存=不修改）" : "（未配置）"}
          <input type="password" data-f="api_key" autocomplete="new-password" placeholder="${c.configured ? "已配置，输入新值可覆盖" : "sk-..."}">
        </label>
        <button class="btn-mini" data-save-settings="${p}">保存</button>
        ${c.configured ? `<button class="btn-mini danger" data-clear-settings="${p}">清除 Key</button>` : ""}
      </div>
    </div>`;
  }).join("") + `<p class="rt-inline-note">OpenAI 兼容协议：任意支持 /chat/completions 的服务均可填入使用。Key 仅存储于服务器本地数据库，页面任何情况不回显；清除后自动席位将无法生成。</p>`;
  body.querySelectorAll("[data-save-settings]").forEach(b => b.onclick = async () => {
    const row = b.closest(".rt-settings-row");
    const provider = b.getAttribute("data-save-settings");
    const q = (f) => row.querySelector(`[data-f="${f}"]`);
    const payload = { provider };
    const bu = q("base_url").value.trim();
    const md = q("model").value.trim();
    const ak = q("api_key").value.trim();
    if (bu) payload.base_url = bu;
    if (md) payload.model = md;
    if (ak) payload.api_key = ak;   // 空 = 不修改已配置 Key
    try {
      await API("PUT", "/api/roundtable/settings", payload);
      showToast(`${provider} 设置已保存`);
      await rtReloadSettings();
    } catch (e) { showToast("保存失败：" + e.message); }
  });
  body.querySelectorAll("[data-clear-settings]").forEach(b => b.onclick = async () => {
    const provider = b.getAttribute("data-clear-settings");
    if (!confirm(`确认清除 ${provider} 的 API Key？清除后自动席位将无法生成。`)) return;
    try {
      await API("PUT", "/api/roundtable/settings", { provider, api_key: "" });
      showToast("Key 已清除");
      await rtReloadSettings();
    } catch (e) { showToast("清除失败：" + e.message); }
  });
}

document.getElementById("btn-roundtable").onclick = () => { renderRoundtable(); if (isMobile()) closeMenu(); };
document.getElementById("side-roundtable").onclick = () => { renderRoundtable(); if (isMobile()) closeMenu(); };

