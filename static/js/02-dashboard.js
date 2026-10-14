// 02-dashboard —— 项目列表 / 详情面板 / 图表 / 策略 / 行情 / 反馈

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

async function loadProjects(autoSelect = true) {
  STRATEGIES = await API("GET", "/api/strategies").catch(() => ({}));
  projects = await API("GET", "/api/projects");
  // P6：侧栏已删除——标的列表改由交易仪表台「标的清单」渲染，这里不再重绘侧栏
  // currentId 已不在新列表中（如游客模式遗留/项目被删）则清空
  if (!projects.some(p => p.id === currentId)) currentId = null;
  // autoSelect=false：只刷新列表，由调用方决定落地视图（如登录后落地圆桌）
  if (!autoSelect) return;
  if (!projects.length) {
    // 当前用户无标的：主区给出引导
    document.getElementById("main").innerHTML = `
      <div class="panel"><h3>欢迎</h3>
      <p class="muted">还没有任何标的。切到「交易仪表台」，点右上角「＋ 新建标的」创建第一个（如 600519.SH）。</p></div>`;
    return;
  }
  if (!currentId) selectProject(projects[0].id);
}

// 置顶 / 取消置顶：持久化到后端，交易仪表台「标的清单」按置顶优先排序
// （原侧栏项目列表的置顶按钮 P6 已迁入仪表台表格，见 06-pages.js renderDashboard）
async function togglePin(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  try {
    const r = await API("PATCH", `/api/projects/${id}`, { pinned: p.pinned ? 0 : 1 });
    p.pinned = r.pinned || 0;
    if (currentView && currentView.name === "dashboard") await renderDashboard();   // 重排表格
    showToast(p.pinned ? "已置顶" : "已取消置顶");
  } catch (err) { showToast("操作失败：" + err.message); }
}

async function selectProject(id) {
  const p = projects.find(x => x.id === id);
  if (!p) {
    // 直链 / 后退到 #/project/12，但该标的已不存在或当前身份无权访问：
    // 回仪表台并纠正 URL（否则页面会因 p.name 取空而报错）
    showToast("标的不存在或无权访问");
    showView("dashboard", null, { replace: true });
    return;
  }
  currentId = id;
  if (isGuest) { await renderGuestProject(p); return; }
  document.getElementById("main").innerHTML = `
    <div class="page-crumb">
      <button class="btn ghost sm" id="back-dash">← 交易仪表台</button>
      <span class="muted">个股详情</span>
    </div>
    <div class="panel">
      <h3>${p.name} <span class="muted">${p.code || ""} · ${escapeHtml(marketLabel(p.market))}</span></h3>
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
      <div class="muted">当前持仓数量与成本价；提交「反馈实际操作」（买入/卖出）后，此处会自动同步为最新持仓。</div>
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
        <span class="muted" id="fetch-hint">${escapeHtml(marketLabel(p.market))} · ${
          p.market === "fund" ? "按日净值同步（合并写入，不删历史）" : "按代码自动同步"}</span>
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
      <div class="muted fb-hint" id="fb-hint"></div>
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
  document.getElementById("back-dash").onclick = () => showView("dashboard");
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
// 登录态额外给「回填实际成交」入口：建议区在页面顶部、反馈表单在第 7 个面板（要滚过两个图表），
// 原先看完建议时看不见回填入口，这里补一条直达路径 + 未回填提醒。
function fillSuggestion(data) {
  const sug = document.getElementById("suggestion");
  if (!sug) return;
  const preds = data.predictions || [];
  const fbs = data.feedbacks || [];
  const s = preds.length ? preds[preds.length - 1] : null;
  // 回填入口登录态常显：即便该项目还没生成过建议，也要能手动补录成交
  const todo = (s && !fbs.some(f => f.date >= s.date))
    ? `<span class="sug-todo">${s.date} 的建议尚未回填</span>` : "";
  const action = isGuest ? "" : `
      <div class="sug-actions">
        ${todo}
        <button class="btn primary sm" id="sug-backfill">回填实际成交</button>
      </div>`;
  const body = s
    ? `<span class="signal ${s.signal}">${s.signal}</span>
      <div>
        <div>预测价：${s.predicted_price ?? "—"}（区间 ${s.predicted_low ?? "—"} ~ ${s.predicted_high ?? "—"}）</div>
        <div class="muted">置信度 ${s.confidence} · 模型 ${s.model}</div>
        <div class="muted">${s.note || ""}</div>
      </div>`
    : `<span class="muted">尚未生成</span>`;
  sug.innerHTML = body + action;
  const btn = document.getElementById("sug-backfill");
  if (btn) btn.onclick = backfillFeedback;
}

// 反馈数量的本地记忆键（按项目区分：不同标的的数量差异大）
function qtyMemoKey(pid) { return "qw_last_qty_" + pid; }

// 建议区「回填实际成交」→ 滚动到反馈表单并预填参考成交价。
// 不覆盖日期：成交日由用户决定（可能是今天，也可能是补记），表单已默认今天。
function backfillFeedback() {
  const form = document.getElementById("fb-form");
  if (!form) return;
  const panel = form.closest(".panel") || form;
  const data = lastChartData || {};
  const mps = data.market_prices || [];
  const lastMp = mps[mps.length - 1];
  const priceEl = document.getElementById("fb-price");
  const qtyEl = document.getElementById("fb-qty");
  if (priceEl && !priceEl.value && lastMp) priceEl.value = lastMp.close;   // 参考价：最新收盘
  if (qtyEl && (!qtyEl.value || qtyEl.value === "0")) {
    const memo = localStorage.getItem(qtyMemoKey(currentId));
    if (memo) qtyEl.value = memo;
  }
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
  panel.classList.remove("flash");
  void panel.offsetWidth;   // 强制重排，保证高亮动画可重复触发
  panel.classList.add("flash");
  setTimeout(() => panel.classList.remove("flash"), 1500);
  if (priceEl) setTimeout(() => priceEl.focus({ preventScroll: true }), 450);
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
  // 反馈表单：成交价以最新收盘价作 placeholder 参考；有持仓却无流水时点明持仓来源
  const lastMp = data.market_prices[data.market_prices.length - 1];
  const priceEl = document.getElementById("fb-price");
  if (priceEl) priceEl.placeholder = lastMp ? `成交价（最新收盘 ${lastMp.close}）` : "成交价";
  const fbHint = document.getElementById("fb-hint");
  if (fbHint) {
    const hasPos = (data.position.shares || 0) > 0;
    const noFb = (data.feedbacks || []).length === 0;
    fbHint.textContent = (hasPos && noFb)
      ? `当前持仓 ${data.position.shares} 股来自「保存仓位」手动设置；回填买卖成交后，持仓与盈亏曲线将按流水自动维护。`
      : "";
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
    // 同步后标的名称可能更新为股票名称
    if (r.name) {
      const proj = projects.find(x => x.id === id);
      if (proj && proj.name !== r.name) {
        proj.name = r.name;
        if (currentView && currentView.name === "dashboard") await renderDashboard();
      }
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
  const qtyEl = document.getElementById("fb-qty");
  const qty = parseFloat(qtyEl.value) || 0;
  try {
    await API("POST", `/api/projects/${currentId}/feedback`, {
      date: document.getElementById("fb-date").value,
      action: document.getElementById("fb-action").value,
      price: parseFloat(document.getElementById("fb-price").value),
      qty,
      note: document.getElementById("fb-note").value
    });
    if (qty > 0) localStorage.setItem(qtyMemoKey(currentId), String(qty));   // 记住数量，便于连续录入
    document.getElementById("fb-form").reset();
    document.getElementById("fb-date").value = today();
    if (qty > 0) qtyEl.value = qty;   // reset 后回填本次数量，减少重复输入
    await refreshProjectView();
    await loadProjects();   // 同步侧栏
    showToast("反馈已提交，持仓已更新");
  } catch (err) {
    alert("提交失败：" + (err.message || err));
  }
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

// ===== 新建标的弹窗（P6：侧栏删除后，入口从侧栏表单挪到交易仪表台页头）=====
// 表单固定在文档层而非仪表台 innerHTML 内：其 onsubmit 绑定在此处只执行一次，
// 若放进主区会随重渲染被重建而丢失绑定。
function openProjectModal() {
  const root = document.getElementById("project-modal");
  if (!root) return;
  pmClearLookup();
  pmPaintHint();
  root.classList.remove("hidden");
  const first = document.getElementById("p-name");
  if (first) setTimeout(() => first.focus(), 30);
}
function closeProjectModal() {
  const root = document.getElementById("project-modal");
  if (root) root.classList.add("hidden");
}
document.getElementById("pm-mask").onclick = closeProjectModal;
document.getElementById("pm-close").onclick = closeProjectModal;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeProjectModal(); });

document.getElementById("project-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await API("POST", "/api/projects", {
      name: document.getElementById("p-name").value,
      code: document.getElementById("p-code").value,
      market: document.getElementById("p-market").value,
      bias: document.getElementById("p-bias").value,
      strategy_type: document.getElementById("p-strategy-type").value,
      strategy: document.getElementById("p-strategy").value
    });
  } catch (err) {
    showToast("新建失败：" + err.message);
    return;
  }
  e.target.reset();
  pmClearLookup();
  pmPaintHint();
  closeProjectModal();
  await loadProjects(false);                                   // 只刷新数据，不强制选中
  if (currentView && currentView.name === "dashboard") await renderDashboard();
  showToast("标的已创建");
};

// ===== 代码识别（P9）：填完代码自动带出名称与品种 =====
// 为什么必须有：① 沪深前缀按代码段猜不准（510300 沪 / 159915 深）；
// ② 同一 6 位数字可能既是场内又是场外（019547 国债 vs 某 QDII、161725 LOF vs 某 LOF 联接），
// 用户手填只能试错。后端 /api/symbol/lookup 走腾讯 smartbox（实测本 VPS 可达）。
let pmTimer = null;
let pmSeq = 0;                 // 只认最后一次输入的结果，避免慢请求把新结果覆盖掉

function pmPaintHint() {
  const mkEl = document.getElementById("p-market");
  const hintEl = document.getElementById("p-hint");
  const codeEl = document.getElementById("p-code");
  if (!mkEl) return;
  const mk = mkEl.value;
  if (hintEl) hintEl.textContent = "代码写法：" + (MARKET_HINTS[mk] || MARKET_HINTS.A)
    + " · 填好代码会自动识别名称与品种";
  if (codeEl) codeEl.placeholder = "标的代码（" + marketLabel(mk) + "）";
}

function pmClearLookup() {
  const box = document.getElementById("p-lookup");
  if (box) { box.classList.add("hidden"); box.innerHTML = ""; }
}

function pmApplyCandidate(c) {
  if (!c) return;
  const nameEl = document.getElementById("p-name");
  const codeEl = document.getElementById("p-code");
  const mkEl = document.getElementById("p-market");
  if (c.name && nameEl) nameEl.value = c.name;
  if (c.code && codeEl) codeEl.value = c.display || c.code;
  if (c.market && mkEl) mkEl.value = c.market;
  pmPaintHint();
  pmClearLookup();
  showToast("已识别：" + (c.name || c.code) + "（" + marketLabel(c.market) + "）");
}

async function pmLookup() {
  const box = document.getElementById("p-lookup");
  const codeEl = document.getElementById("p-code");
  if (!box || !codeEl) return;
  // 弹窗对游客本就不可达（#dash-new 不渲染），但检索接口要鉴权，
  // 万一被触达，401 会走 API() 的登出分支——这里先挡住。
  if (isGuest || !token) return;
  const q = (codeEl.value || "").trim();
  if (q.length < 2) { pmClearLookup(); return; }
  const seq = ++pmSeq;
  let items = [];
  try {
    const r = await API("GET", "/api/symbol/lookup?q=" + encodeURIComponent(q));
    items = (r && r.items) || [];
  } catch (err) {
    items = [];               // 检索失败不打扰用户：手填代码仍然可用
  }
  if (seq !== pmSeq) return;
  if (!items.length) { pmClearLookup(); return; }
  box.innerHTML = items.map((c, i) => `
    <button type="button" class="pm-cand" data-cand="${i}">
      <span class="pm-cand-name">${escapeHtml(c.name || c.code)}</span>
      <span class="pm-cand-code">${escapeHtml(c.display || c.code)}</span>
      <span class="pm-kind">${escapeHtml(c.kind_label || c.market_label || "")}</span>
    </button>`).join("");
  box.classList.remove("hidden");
  box.querySelectorAll(".pm-cand").forEach(btn => {
    btn.onclick = () => pmApplyCandidate(items[Number(btn.getAttribute("data-cand"))]);
  });
}

document.getElementById("p-market").onchange = () => { pmPaintHint(); pmLookup(); };
document.getElementById("p-code").addEventListener("blur", pmLookup);
document.getElementById("p-code").addEventListener("input", () => {
  clearTimeout(pmTimer);
  pmTimer = setTimeout(pmLookup, 450);      // 防抖：连续输入只查最后一次
});
pmPaintHint();
