// 05-roundtable —— 圆桌讨论（多 AI 席位 · 自动/手动双通道）
// ===== 圆桌讨论（多 AI 席位 · 双通道：自动席位配 Key 直调 LLM / 手动席位复制提示词外部咨询后回填） =====
// 服务商清单的**唯一数据源在后端**（app.py → PROVIDER_DEFAULTS），由
// GET /api/roundtable/settings 连同 base_url / model / 本机可达性一起下发。
// 下表仅用于「后端数据尚未加载」时兜底渲染，字段与后端保持同名；
// 用户在 AI 设置里自建的自定义服务商不在本表内，只来自后端。
const RT_PROVIDERS_FALLBACK = {
  zhipu:       { label: "智谱 GLM",   base_url: "https://open.bigmodel.cn/api/paas/v4",             model: "glm-5.3-flash",            group: "国内" },
  qwen:        { label: "通义千问",   base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus",               group: "国内" },
  doubao:      { label: "火山豆包",   base_url: "https://ark.cn-beijing.volces.com/api/v3",          model: "doubao-seed-2-1-pro-260915", group: "国内" },
  deepseek:    { label: "DeepSeek",   base_url: "https://api.deepseek.com",                         model: "deepseek-flash",           group: "国内" },
  moonshot:    { label: "Kimi 月之暗面", base_url: "https://api.moonshot.cn/v1",                     model: "kimi-k3",                 group: "国内" },
  siliconflow: { label: "硅基流动",   base_url: "https://api.siliconflow.cn/v1",                    model: "Qwen/Qwen2.5-7B-Instruct", group: "国内" },
  qianfan:     { label: "百度千帆 · 文心", base_url: "https://qianfan.baidubce.com/v2",              model: "ernie-5.0",               group: "国内" },
  hunyuan:     { label: "腾讯混元",   base_url: "https://api.hunyuan.cloud.tencent.com/v1",          model: "hunyuan-turbo",           group: "国内" },
  spark:       { label: "讯飞星火",   base_url: "https://spark-api-open.xf-yun.com/v1",              model: "4.0Ultra",                group: "国内" },
  stepfun:     { label: "阶跃星辰",   base_url: "https://api.stepfun.com/v1",                         model: "step-3.7-flash",          group: "国内" },
  yi:          { label: "零一万物 Yi", base_url: "https://api.lingyiwanwu.com/v1",                    model: "yi-lightning",            group: "国内" },
  baichuan:    { label: "百川智能",   base_url: "https://api.baichuan-ai.com/v1",                     model: "Baichuan4-Turbo",         group: "国内" },
  openai:      { label: "OpenAI",     base_url: "https://api.openai.com/v1",                          model: "gpt-4o-mini",             group: "海外" },
  openrouter:  { label: "OpenRouter", base_url: "https://openrouter.ai/api/v1",                       model: "openai/gpt-4o-mini",      group: "海外" },
  gemini:      { label: "Google Gemini", base_url: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-3.8-flash", group: "海外" },
  grok:        { label: "xAI Grok",   base_url: "https://api.x.ai/v1",                                model: "grok-3-mini",             group: "海外" },
  mistral:     { label: "Mistral",    base_url: "https://api.mistral.ai/v1",                          model: "mistral-large-latest",    group: "海外" },
  groq:        { label: "Groq",       base_url: "https://api.groq.com/openai/v1",                     model: "llama-3.3-70b-versatile", group: "海外" },
  ollama:      { label: "Ollama（本地）", base_url: "http://127.0.0.1:11434/v1",                      model: "qwen2.5:7b",              group: "本地 / 自建" },
};

/** 当前生效的服务商表：后端下发优先，未加载时退回兜底表。 */
function rtProviders() {
  const api = (rtState.settings && rtState.settings.providers) || null;
  return (api && Object.keys(api).length) ? api : RT_PROVIDERS_FALLBACK;
}
// 手动席位可选配置的「AI 角色」（预设下拉 + 自定义），仅作身份标签与提示词注入
const RT_ROLES = ["豆包", "DeepSeek", "元宝", "Kimi", "通义千问", "ChatGPT", "Claude", "Gemini", "文心一言", "其他"];

// 席位「接入方式」三态（由后端 mode 驱动）：manual 手动回填 / auto LLM Key 自动 / mcp MCP 待接（本期仅标注）
const RT_MODES = {
  manual: { label: "手动回填", cls: "" },
  auto:   { label: "LLM Key 自动", cls: "mode-auto" },
  mcp:    { label: "MCP 待接", cls: "mode-mcp" },
};
function rtModeInfo(m) { return RT_MODES[m] || RT_MODES.manual; }
let rtState = { date: null, snap: null, seats: [], styles: [], posts: {}, settings: null, history: null, admin: false, llmStats: null, analysis: null };
const rtAdmin = () => !!(me && me.is_admin);

// P7：首页已改为「资讯工作台」，圆桌是二级页。
// 页内不再放「← 交易仪表台」——顶栏已有全部页面入口，二级页里的「返回某页」既冗余、
// 又把圆桌错误地挂在仪表台之下（这个按钮是 P1 侧栏时代的遗留）。

async function renderRoundtable() {
  if (isGuest || !token) { showAuth("login"); return; }
  rtState.admin = rtAdmin();
  // 折叠集合（含席位卡与下方两个类席位卡面板），模板里据此决定初始 collapsed 态
  const _fold = rtFoldSet();
  document.getElementById("main").innerHTML = `
    <div class="rt-page">
      <div class="rt-head">
        <div>
          <h1>圆桌讨论</h1>
          <p class="rt-sub">多个 AI 席位基于今日大盘与你的持仓独立给出建议 · <span id="rt-date">加载中…</span></p>
        </div>
        <div class="rt-head-actions">
          <button class="btn-mini" id="rt-generate-all">生成今日讨论</button>
          <button class="btn-mini ghost" id="rt-manage-toggle">席位管理</button>
          <button class="btn-mini ghost" id="rt-style-toggle">风格模板</button>
          <button class="btn-mini ghost" id="rt-settings-toggle">AI 设置</button>
        </div>
      </div>
      <div class="panel hidden" id="rt-manage-panel"><h3>席位管理 <span class="muted">增删席位、选风格模板与接入通道</span></h3><div id="rt-manage-body"><span class="muted">加载中…</span></div></div>
      <div class="panel hidden" id="rt-style-panel"><h3>风格模板 <span class="muted">人格 / 风格提示词的复用模板 · 席位选用后，改模板即对所有引用它的席位生效</span></h3><div id="rt-style-body"><span class="muted">加载中…</span></div></div>
      <div class="panel hidden" id="rt-settings-panel"><h3>AI 设置 <span class="muted">API Key 仅存服务器本地数据库，页面不回显</span></h3><div id="rt-settings-body"><span class="muted">加载中…</span></div></div>
      <div class="panel"><h3>今日大盘 <button class="btn-mini ghost" id="rt-indices-toggle">收起</button></h3><div class="rt-indices" id="rt-indices"><span class="muted">加载中…</span></div></div>
      <div class="panel"><h3>账户概览 <button class="btn-mini ghost" id="rt-kpi-toggle">收起</button></h3>
        <div class="rt-indices" id="rt-kpi">
          <div class="rt-index"><div class="name">总市值</div><div class="val" id="rt-kpi-mv">—</div></div>
          <div class="rt-index"><div class="name">浮动盈亏</div><div class="val" id="rt-kpi-un">—</div></div>
          <div class="rt-index"><div class="name">累计实现盈亏</div><div class="val" id="rt-kpi-real">—</div></div>
        </div>
      </div>
      <div class="panel">
        <h3>持仓总览 <span class="muted" id="rt-holdings-count"></span><button class="btn-mini ghost" id="rt-holdings-toggle">收起</button></h3>
        <div id="rt-holdings-body">
          <div class="table-wrap"><table id="rt-holdings"><thead><tr><th>标的</th><th>最新价</th><th>市值(元)</th><th>收益率</th><th>今日</th><th>系统建议</th></tr></thead><tbody></tbody></table></div>
        </div>
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
        <h3>AI 席位 <span class="muted">接入方式：LLM Key 自动 / 手动回填 / MCP（待接）· 观点每日自动存档论坛</span>
          <button class="btn-mini ghost" id="rt-seat-collapse" title="折叠所有席位卡，只看标题与状态">全部折叠</button>
          <button class="btn-mini ghost" id="rt-seat-expand" title="展开所有席位卡，阅读完整观点">全部展开</button>
          ${rtState.admin ? `<button class="btn-mini ghost" id="rt-add-seat-toggle" style="margin-left:8px">+ 新增席位</button>` : ""}
        </h3>
        <div class="rt-seats" id="rt-seats"><span class="muted">加载中…</span></div>
        ${rtState.admin ? `
        <div class="panel hidden" id="rt-add-seat-panel" style="margin-top:10px">
          <h4>新增席位</h4>
          <form id="rt-seat-quick-add" class="rt-manage-row">
            <div class="row">
              <label class="rt-field" style="width:170px">席位名（AI 名称）<input type="text" name="name" required placeholder="如：价值派"></label>
              <label class="rt-field" style="width:140px">AI 角色<input type="text" name="role" list="rt-role-list" placeholder="豆包/元宝…"></label>
              <label class="rt-field" style="width:150px">接入方式
                <select name="mode">
                  <option value="manual">手动回填</option>
                  <option value="auto">LLM Key 自动</option>
                  <option value="mcp">MCP（待接）</option>
                </select>
              </label>
            </div>
            <div class="row">
              <label class="rt-field" style="width:180px">风格模板
                <select name="style_id"></select>
              </label>
              <label class="rt-field grow" data-persona-wrap>风格提示词<textarea name="persona" rows="2" placeholder="该席位的分析风格与偏好（选「自定义」时生效）…"></textarea><span class="rt-inline-note hidden" data-style-note></span></label>
              <button class="btn-mini" type="submit">添加</button>
              <button class="btn-mini ghost" type="button" id="rt-add-seat-cancel">取消</button>
            </div>
          </form>
        </div>` : ""}
      <div class="rt-seat${_fold.has("rt-analysis-panel") ? " collapsed" : ""}" id="rt-analysis-panel">
        <div class="rt-seat-head">
          <div class="rt-seat-title">
            <button class="rt-seat-caret" type="button" aria-expanded="${_fold.has("rt-analysis-panel") ? "false" : "true"}" title="${_fold.has("rt-analysis-panel") ? "展开观点分析" : "折叠观点分析"}"></button>
            <span class="rt-seat-name">观点分析</span>
            <span class="muted">汇总全部席位观点 · 配置的 LLM 交叉分析</span>
          </div>
          <div class="rt-seat-actions">
            ${rtState.admin ? `<button class="btn-mini" id="rt-analyze-run">${rtState.analysis ? "重新分析" : "开始分析"}</button>` : ""}
          </div>
        </div>
        <div class="rt-seat-body" id="rt-analysis-body"><span class="muted">加载中…</span></div>
      </div>
      <div class="rt-seat${_fold.has("rt-review-panel") ? " collapsed" : ""}" id="rt-review-panel">
        <div class="rt-seat-head">
          <div class="rt-seat-title">
            <button class="rt-seat-caret" type="button" aria-expanded="${_fold.has("rt-review-panel") ? "false" : "true"}" title="${_fold.has("rt-review-panel") ? "展开观点复盘" : "折叠观点复盘"}"></button>
            <span class="rt-seat-name">观点复盘</span>
            <span class="muted" title="席位输出为「分析结论」（对持仓与新闻的判断与理由）；其「素材质量 / 趋势 / 引用资讯信息量」由真实行情机械判定（方向 × 真实收益），不在页面做任何「对错」自评">席位输出为「分析结论」；素材质量由真实行情机械判定，不做对错自评</span>
          </div>
          <div class="rt-seat-actions">
            ${rtState.admin ? `<button class="btn-mini" id="rt-review-run">${rtHasReview() ? "重新生成" : "生成复盘"}</button>` : ""}
          </div>
        </div>
        <div class="rt-seat-body" id="rt-review-body"><span class="muted">加载中…</span></div>
      </div>
    </div>`;
  document.getElementById("rt-generate-all").onclick = () => rtGenerate(null);
  document.getElementById("rt-manage-toggle").onclick = () => {
    if (!rtState.admin) { showToast("仅管理员可管理席位"); return; }
    document.getElementById("rt-manage-panel").classList.toggle("hidden");
  };
  document.getElementById("rt-style-toggle").onclick = () => {
    if (!rtState.admin) { showToast("仅管理员可管理风格模板"); return; }
    document.getElementById("rt-style-panel").classList.toggle("hidden");
  };
  document.getElementById("rt-settings-toggle").onclick = () => {
    if (!rtState.admin) { showToast("仅管理员可修改 AI 设置"); return; }
    document.getElementById("rt-settings-panel").classList.toggle("hidden");
  };
  document.getElementById("rt-history-toggle").onclick = () => rtPanelFold("rt-history-body", "rt-history-toggle");
  document.getElementById("rt-holdings-toggle").onclick = () => rtPanelFold("rt-holdings-body", "rt-holdings-toggle");
  document.getElementById("rt-indices-toggle").onclick = () => rtPanelFold("rt-indices", "rt-indices-toggle");
  document.getElementById("rt-kpi-toggle").onclick = () => rtPanelFold("rt-kpi", "rt-kpi-toggle");
  // 观点分析 / 观点复盘两个类席位卡面板：折叠走 caret（与席位卡一致），不再用「收起」按钮
  const aCaret = document.querySelector("#rt-analysis-panel .rt-seat-caret");
  if (aCaret) aCaret.onclick = () => rtFoldAux("rt-analysis-panel", !document.getElementById("rt-analysis-panel").classList.contains("collapsed"));
  const rCaret = document.querySelector("#rt-review-panel .rt-seat-caret");
  if (rCaret) rCaret.onclick = () => rtFoldAux("rt-review-panel", !document.getElementById("rt-review-panel").classList.contains("collapsed"));
  const analyzeRun = document.getElementById("rt-analyze-run");
  if (analyzeRun) analyzeRun.onclick = () => rtAnalyze();
  const reviewRun = document.getElementById("rt-review-run");
  if (reviewRun) reviewRun.onclick = () => rtReviewRun();
  document.getElementById("rt-seat-collapse").onclick = () => rtFoldAll(true);
  document.getElementById("rt-seat-expand").onclick = () => rtFoldAll(false);
  const addSeatToggle = document.getElementById("rt-add-seat-toggle");
  if (addSeatToggle) addSeatToggle.onclick = () => document.getElementById("rt-add-seat-panel").classList.toggle("hidden");
  const addSeatCancel = document.getElementById("rt-add-seat-cancel");
  if (addSeatCancel) addSeatCancel.onclick = () => { document.getElementById("rt-add-seat-panel").classList.add("hidden"); };
  const addSeatForm = document.getElementById("rt-seat-quick-add");
  if (addSeatForm) {
    // 风格下拉的选项要等 rtState.styles 加载完才填得上，故在此单独同步一次
    const qStyle = addSeatForm.querySelector('[name="style_id"]');
    qStyle.innerHTML = rtStyleOptions(null);
    qStyle.onchange = () => rtSyncStyleRow(addSeatForm);
    rtSyncStyleRow(addSeatForm);
    addSeatForm.onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      const q = (n) => f.querySelector(`[name="${n}"]`);
      try {
        await API("POST", "/api/roundtable/seats", {
          name: q("name").value.trim() || "新席位",
          role: q("role").value.trim(),
          persona: q("persona").value,
          style_id: Number(q("style_id").value) || 0,   // 0 = 自定义
          mode: q("mode").value,   // 接入方式：manual 手动回填 / auto LLM Key 自动 / mcp MCP（待接）
          provider: "deepseek",
          model: "",
        });
        f.reset();
        rtSyncStyleRow(f);
        document.getElementById("rt-add-seat-panel").classList.add("hidden");
        showToast("席位已添加");
        await rtReloadSeats();
      } catch (err) { showToast("添加失败：" + err.message); }
    };
  }
  await rtLoadAll();
}

async function rtLoadAll() {
  try {
    const [seats, posts, snap, hist, review, analysis] = await Promise.all([
      API("GET", "/api/roundtable/seats"),
      API("GET", "/api/roundtable/posts"),
      API("GET", "/api/roundtable/snapshot"),
      API("GET", "/api/roundtable/history").catch(() => null),
      API("GET", "/api/roundtable/review/stats").catch(() => null),
      API("GET", "/api/roundtable/analysis").catch(() => null),
    ]);
    rtState.seats = seats || [];
    rtState.snap = snap;
    rtState.date = snap ? snap.date : null;
    rtState.history = hist;
    rtState.review = review;
    // API() 返回完整响应体（01-core.js 直接 r.json()，不解包）：GET 的 {date, analysis}
    // 要取内层，与 POST 后 rtState.analysis = r.analysis 的赋值保持同构——
    // 少了这层解包，rtRenderAnalysis 会拿到外壳、全部字段 undefined（空壳渲染）。
    rtState.analysis = (analysis && analysis.analysis) || null;
    rtState.posts = {};
    (posts || []).forEach(p => { rtState.posts[p.seat_id] = p; });
    if (rtState.admin) {
      const [settings, styles, stats] = await Promise.all([
        API("GET", "/api/roundtable/settings").catch(() => null),
        API("GET", "/api/roundtable/styles").catch(() => []),
        API("GET", "/api/roundtable/llm-stats").catch(() => null),
      ]);
      rtState.settings = settings;
      rtState.styles = styles || [];
      rtState.llmStats = stats;
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
  rtRenderTotals();
  rtRenderIndices();
  rtRenderHoldings();
  rtRenderHistory();
  rtRenderSeats();
  rtRenderAnalysis();
  rtRenderReview();
  if (rtState.admin) {
    rtRenderManage();
    rtRenderStyles();
    rtRenderSettings();
    // 席位面板里那个「+ 新增席位」快捷表单在主模板里、渲染时风格还没加载，
    // 这里补填一次选项（保留用户已选的值，避免重渲染把它打回「自定义」）。
    const qs = document.querySelector('#rt-seat-quick-add [name="style_id"]');
    if (qs) {
      const cur = qs.value;
      qs.innerHTML = rtStyleOptions(cur || null);
      rtSyncStyleRow(qs.closest("form"));
    }
  }
}

// 账户三指标（总市值 / 浮动盈亏 / 累计实现盈亏）——取自后端快照 totals，
// 与交易仪表台 KPI、结算帖同源（app._account_totals 是唯一定义），页面不另算。
// 卡片外观复用「今日大盘」指数卡的 .rt-index（见 renderRoundtable 里的结构），
// 故这里的文字槽位是 .val 而非 .kpi-value —— 两处改样式走同一套 CSS。
function rtRenderTotals() {
  const t = (rtState.snap && rtState.snap.totals) || null;
  const set = (id, val, cls) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = (val == null) ? "—" : fmtMoney(val);
    el.className = "val" + (cls ? " " + cls : "");
  };
  set("rt-kpi-mv", t ? t.market_value : null, "");
  set("rt-kpi-un", t ? t.unrealized : null, t ? signCls(t.unrealized) : "");
  set("rt-kpi-real", t ? t.realized : null, t ? signCls(t.realized) : "");
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

// ---- 面板折叠（历史标的与操作记录 / 持仓总览）----
// 与「席位卡折叠」刻意不同：那两个是**列表很长时的长期偏好**，按席位 id 存 localStorage；
// 这里是「临时让位给下方内容」，状态**不持久化**，刷新/切页回来仍是展开 —— 否则用户
// 第二天进来会以为持仓没了。force 传布尔则指定状态，省略则切换。
function rtPanelFold(bodyId, btnId, force) {
  const body = document.getElementById(bodyId);
  const btn = document.getElementById(btnId);
  if (!body || !btn) return;
  const hidden = (typeof force === "boolean")
    ? body.classList.toggle("hidden", force)
    : body.classList.toggle("hidden");
  btn.textContent = hidden ? "展开" : "收起";
}

// ---- 席位卡折叠 ----
// 折叠状态按「席位 id」存 localStorage：生成后刷新、改名后刷新都会重渲染卡片，
// 状态只存 DOM 的话每次刷新都会被打回全部展开。列表很长时「全部折叠」看到概览尤其需要它。
const RT_FOLD_KEY = "qw_rt_folded";

function rtFoldSet() {
  try {
    const raw = JSON.parse(localStorage.getItem(RT_FOLD_KEY) || "[]");
    return new Set((Array.isArray(raw) ? raw : []).map(String));
  } catch (e) { return new Set(); }
}

function rtFoldSave(set) {
  try { localStorage.setItem(RT_FOLD_KEY, JSON.stringify([...set])); } catch (e) { /* 无痕模式写不了就只当次生效 */ }
}

// 只改一张卡片的 DOM（不碰存储），供单个/批量两条路径复用。
// 箭头朝向由 .rt-seat.collapsed 驱动（CSS 三角），这里只维护 title 与 aria。
// label 默认「该席位观点」（席位卡用），观点分析/复盘面板传各自名称。
function rtApplyFold(card, folded, label) {
  label = label || "该席位观点";
  card.classList.toggle("collapsed", folded);
  const caret = card.querySelector(".rt-seat-caret");
  if (caret) {
    caret.title = folded ? `展开${label}` : `折叠${label}`;
    caret.setAttribute("aria-expanded", folded ? "false" : "true");
  }
}

// 观点分析 / 观点复盘两个「类席位卡」面板的折叠：复用同一套 localStorage 折叠集合
// （rtFoldSet/rtFoldSave），折叠态与席位卡完全一致的视觉与持久化行为。
function rtFoldAux(id, folded) {
  const card = document.getElementById(id);
  if (!card) return;
  const set = rtFoldSet();
  if (folded) set.add(id); else set.delete(id);
  rtFoldSave(set);
  rtApplyFold(card, folded, id === "rt-analysis-panel" ? "观点分析" : "观点复盘");
  // 复盘面板的 echarts 在折叠（display:none）时建图会得到 0 尺寸，展开后需 resize 重绘
  if (!folded && id === "rt-review-panel" && typeof rtReviewChart !== "undefined" && rtReviewChart) {
    setTimeout(() => { try { rtReviewChart.resize(); } catch (e) {} }, 0);
  }
}

function rtFoldSeat(sid, folded) {
  const card = document.querySelector(`#rt-seats .rt-seat[data-seat-id="${sid}"]`);
  if (!card) return;
  const set = rtFoldSet();
  if (folded) set.add(String(sid)); else set.delete(String(sid));
  rtFoldSave(set);
  rtApplyFold(card, folded);
}

function rtFoldAll(folded) {
  const cards = document.querySelectorAll("#rt-seats .rt-seat");
  if (!cards.length) { showToast("暂无席位卡片"); return; }
  rtFoldSave(folded ? new Set([...cards].map(c => c.getAttribute("data-seat-id"))) : new Set());
  cards.forEach(c => rtApplyFold(c, folded));
  showToast(folded ? `已折叠全部 ${cards.length} 个席位` : "已展开全部席位");
}

function rtRenderSeats() {
  const wrap = document.getElementById("rt-seats");
  if (!wrap) return;
  const seats = (rtState.seats || []).filter(s => s.enabled);
  if (!seats.length) { wrap.innerHTML = `<span class="muted">暂无可用席位，请在「席位管理」中添加。</span>`; return; }
  const folded = rtFoldSet();
  wrap.innerHTML = "";
  seats.forEach(s => {
    const post = rtState.posts[s.id];
    const isAuto = s.mode === "auto";     // 仅 auto 走后端直调 LLM；manual / mcp 均走手动通道
    const modeInfo = rtModeInfo(s.mode);  // 接入方式标注
    const isFolded = folded.has(String(s.id));
    const card = document.createElement("div");
    card.className = "rt-seat" + (isFolded ? " collapsed" : "");
    card.setAttribute("data-seat-id", s.id);
    const stateBadge = post
      ? `<span class="rt-badge state-done">${post.source === "auto" ? "已生成" : "已录入"}</span>`
      : `<span class="rt-badge state-pending">待生成</span>`;
    // 命中率徽标：仅当有效样本达标（>=MIN_SAMPLE）才显示，避免小样本误导。
    const _rs = (rtState.review && rtState.review.seat_stats) || [];
    const _stat = _rs.find(x => x.seat_id === s.id);
    const rateBadge = (_stat && _stat.min_ok && _stat.rate != null)
      ? `<span class="rt-badge rate" title="近 ${((rtState.review && rtState.review.meta) || {}).review_days || 20} 天有效样本 ${_stat.hit}+${_stat.miss}，T+${((rtState.review && rtState.review.meta) || {}).horizon || 5} 机械判定素材质量（命中率）">素材质量 ${_stat.rate}%</span>`
      : "";
    let actions = "";
    if (isAuto) {
      actions = `<button class="btn-mini" data-gen="${s.id}">${post ? "重新生成" : "生成"}</button>`;
      if (!s.has_key) actions += `<span class="rt-inline-note">未配置 Key</span>`;
    } else {
      actions = `<button class="btn-mini" data-copy="${s.id}">复制提示词</button>`
        + `<button class="btn-mini ghost" data-paste="${s.id}">粘贴反馈</button>`;
    }
    // 已同步 → 直达存档帖（能看到观点各次修订的完整历史，卡片只留最新一层）；
    // 未同步 → 给补建入口。命名刻意用「查看」而非「存档」：建档是保存时自动完成的，
    // 这里只是跳转 / 补救，叫「论坛存档」会让人误以为又要手动存一次。
    if (post && post.thread_id) {
      actions += `<button class="btn-mini ghost" data-thread="${post.thread_id}">查看存档</button>`;
    } else if (post) {
      actions += `<button class="btn-mini ghost" data-archive="${s.id}" title="该观点的论坛存档未建成，点此补建">补建存档</button>`;
    }
    // 风格来源也放这一行：用了模板就标模板名（改模板全局生效），自己写了正文的标「自定义」。
    // 风格已独立成模板管理项，卡片上不显示的话，光看卡片不知道这个席位在用哪套风格。
    const styleTag = s.style_name
      ? `风格 ${escapeHtml(s.style_name)}`
      : (s.persona ? "自定义风格" : "");
    const runMeta = post
      ? `${post.source === "auto" ? `模型 ${escapeHtml(post.model || "—")} · ` : ""}更新于 ${escapeHtml((post.updated_at || "").replace("T", " ").slice(0, 19))}`
      : "";
    const meta = [styleTag, runMeta].filter(Boolean).join(" · ");
    // 管理员可在卡片上直接手工修改 AI 名称 / AI 角色
    const nameHtml = rtState.admin
      ? `<span class="rt-seat-name rt-editable" data-edit-name="${s.id}" title="点击修改 AI 名称">${escapeHtml(s.name)}</span>`
      : `<span class="rt-seat-name">${escapeHtml(s.name)}</span>`;
    let roleHtml;
    if (s.role) {
      roleHtml = rtState.admin
        ? `<span class="rt-role rt-editable" data-edit-role="${s.id}" title="点击修改 AI 角色">${escapeHtml(s.role)}</span>`
        : `<span class="rt-role">${escapeHtml(s.role)}</span>`;
    } else {
      roleHtml = rtState.admin ? `<button class="btn-mini ghost rt-add-role" data-add-role="${s.id}">+ 角色</button>` : "";
    }
    card.innerHTML = `
      <div class="rt-seat-head">
        <div class="rt-seat-title">
          <button class="rt-seat-caret" type="button" aria-expanded="${isFolded ? "false" : "true"}"
            title="${isFolded ? "展开该席位观点" : "折叠该席位观点"}"></button>
          ${nameHtml}
          <span class="rt-badge ${modeInfo.cls}">${modeInfo.label}</span>
          ${roleHtml}
          ${stateBadge}
          ${rateBadge}
        </div>
        <div class="rt-seat-actions">${actions}</div>
      </div>
      <div class="rt-seat-body md-body ${post ? "" : "empty"}">${post ? mdRender(post.content) : "尚未生成：点击右上角按钮开始"}</div>
      ${meta ? `<div class="rt-seat-meta">${meta}</div>` : ""}`;
    wrap.appendChild(card);
    const caretBtn = card.querySelector(".rt-seat-caret");
    if (caretBtn) caretBtn.onclick = () => rtFoldSeat(s.id, !card.classList.contains("collapsed"));
    if (rtState.admin) {
      const nameEl = card.querySelector("[data-edit-name]");
      if (nameEl) nameEl.onclick = () => rtInlineEditSeat(s.id, "name", nameEl, s.name);
      const roleEl = card.querySelector("[data-edit-role]");
      if (roleEl) roleEl.onclick = () => rtInlineEditSeat(s.id, "role", roleEl, s.role);
      const addBtn = card.querySelector("[data-add-role]");
      if (addBtn) addBtn.onclick = () => rtInlineEditSeat(s.id, "role", addBtn, "");
    }
  });
  wrap.querySelectorAll("[data-copy]").forEach(b => b.onclick = () => rtCopyPrompt(parseInt(b.getAttribute("data-copy"), 10)));
  wrap.querySelectorAll("[data-paste]").forEach(b => b.onclick = () => rtOpenPaste(parseInt(b.getAttribute("data-paste"), 10)));
  wrap.querySelectorAll("[data-gen]").forEach(b => b.onclick = () => rtGenerate([parseInt(b.getAttribute("data-gen"), 10)]));
  wrap.querySelectorAll("[data-thread]").forEach(b => b.onclick = () => rtOpenForumThread(parseInt(b.getAttribute("data-thread"), 10)));
  wrap.querySelectorAll("[data-archive]").forEach(b => b.onclick = () => rtArchiveSeat(parseInt(b.getAttribute("data-archive"), 10)));
}

// ---- 观点分析面板：汇总当日全部席位观点，交配置的 LLM（默认 DeepSeek）交叉分析 ----
// 与观点复盘的分工：复盘是「单条判断 × 真实行情」的机械兑现（事后、判对错），
// 这里是「多条观点之间」的横向汇总（共识/分歧/风险/综合结论），不做对错判断。
// 分析由管理员手动触发（调 LLM 有费用），结果按日期覆盖保存，所有登录用户可见。
function rtRenderAnalysis() {
  rtSyncAnalysisBtn();
  const el = document.getElementById("rt-analysis-body");
  if (!el) return;
  const a = rtState.analysis;
  if (!a) {
    el.innerHTML = `<span class="muted">${rtState.admin
      ? "暂无分析结果——点击「开始分析」汇总当日全部席位观点。"
      : "暂无分析结果（管理员可手动触发汇总分析）。"}</span>`;
    return;
  }
  const meta = [
    `${a.seat_count || 0} 条席位观点`,
    `${escapeHtml(a.provider || "—")} · ${escapeHtml(a.model || "—")}`,
    `更新于 ${escapeHtml((a.updated_at || "").replace("T", " ").slice(0, 19))}`,
  ].filter(Boolean).join(" · ");
  el.innerHTML = `
    <div class="rt-review-note">分析基于 ${escapeHtml(a.date || rtState.date || "")} 全部启用席位的观点，由配置的 LLM 交叉汇总；只呈现共识与分歧，不判定对错。</div>
    <div class="md-body">${mdRender(a.content || "")}</div>
    <div class="rt-seat-meta">${meta}</div>`;
}

async function rtAnalyze() {
  // 防误触：分析要调用 LLM（产生费用），与「生成今日讨论」同一约定。
  if (!confirm("确认汇总分析当日全部 AI 席位观点？\n将调用配置的 LLM（默认 DeepSeek）生成交叉分析。")) return;
  const btn = document.getElementById("rt-analyze-run");
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }
  try {
    const r = await API("POST", "/api/roundtable/analyze", { date: rtState.date });
    rtState.analysis = (r && r.analysis) || null;
    rtRenderAnalysis();
    showToast("观点分析完成");
  } catch (e) {
    showToast("分析失败：" + e.message);
  } finally {
    // 不用 finally 还原旧文案：成功/失败后文案都由 rtSyncAnalysisBtn 按当前数据决定
    // （成功一次后应显示「重新分析」，还原 label 会退回「开始分析」）。
    if (btn) { btn.disabled = false; rtSyncAnalysisBtn(); }
  }
}

// 按钮文案按「当前是否已有分析结果」决定：无=开始分析，有=重新分析。
function rtSyncAnalysisBtn() {
  const btn = document.getElementById("rt-analyze-run");
  if (!btn || btn.disabled) return;   // 生成中不要覆盖「分析中…」
  btn.textContent = rtState.analysis ? "重新分析" : "开始分析";
}

// 复盘按钮文案：已有判定记录=重新生成，尚无=生成复盘。
function rtSyncReviewBtn() {
  const btn = document.getElementById("rt-review-run");
  if (!btn || btn.disabled) return;   // 生成中不要覆盖「复盘中…」
  btn.textContent = rtHasReview() ? "重新生成" : "生成复盘";
}

// 是否已有可复盘的判定记录（用于决定按钮文案）
function rtHasReview() {
  return !!(rtState.review && (rtState.review.seat_stats || []).some(s => s.total > 0));
}

async function rtReviewRun() {
  const d = rtState.date;
  // 必须显式传 date：端点不传参走 daily（复盘「昨日」），只有传 date 才复盘当前选定日。
  if (!confirm(`确认对 ${d} 的观点重新抽取并复盘？\n将对当日每个有观点的席位各调用一次 LLM（产生费用），\n随后按真实行情机械判定。\n\n（幂等：同一 日期+席位+标的 不会重复落行，已存在的只更新。）`)) return;
  const btn = document.getElementById("rt-review-run");
  if (btn) { btn.disabled = true; btn.textContent = "复盘中…"; }
  try {
    const r = await API("POST", "/api/roundtable/review", { date: d });
    const res = (r && r.result) || {};
    // 复盘产出的判定落 rt_reviews，页面数据来自 review/stats，要重拉一次才看得到新结果。
    const s = await API("GET", "/api/roundtable/review/stats").catch(() => null);
    if (s) rtState.review = s;
    rtRenderReview();
    if (res.calls || res.records) {
      showToast(`复盘完成：观点 ${res.posts || 0} · 抽取 ${res.calls || 0} · 判定 ${res.records || 0} 条`);
    } else {
      showToast(`${d} 没有可复盘的观点（当日无席位输出）`);
    }
  } catch (e) {
    showToast("复盘失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; rtSyncReviewBtn(); }
  }
}

// ---- 观点复盘面板（G3）：席位命中率 / 趋势 / 引用资讯信息量 / 样本明细 ----
// 数据来自 GET /api/roundtable/review/stats（rtreview 模块机械判定，不在前端自评对错）。
const RT_DIR_LABEL = { up: "看多", down: "看空", hold: "持有", observe: "观察" };
const RT_VERDICT = {
  hit:    { label: "命中", cls: "rt-v-hit" },
  miss:   { label: "未中", cls: "rt-v-miss" },
  flat:   { label: "无效波动", cls: "rt-v-flat" },
  pending:{ label: "待定", cls: "rt-v-pending" },
};
let rtReviewChart = null;

function rtRenderReview() {
  rtSyncReviewBtn();
  const el = document.getElementById("rt-review-body");
  if (!el) return;
  const data = rtState.review;
  if (!data) { el.innerHTML = `<span class="muted">复盘数据加载失败（不影响其他面板）</span>`; return; }
  const meta = data.meta || {};
  const horizon = meta.horizon || 5;
  const band = meta.flat_band != null ? meta.flat_band : 0.5;
  const minSample = meta.min_sample || 3;

  // 1) 席位命中率表（只列有记录者；样本不足者不展示徽标但仍在表里标明）
  const seats = (data.seat_stats || []).filter(s => s.total > 0);
  const seatRows = seats.length ? seats.map(s => {
    const rate = s.rate == null ? "—" : s.rate + "%";
    const flag = s.min_ok ? "" : `<span class="muted">（样本不足）</span>`;
    return `<tr>
      <td>${escapeHtml(s.name)}</td>
      <td class="${s.rate == null ? "" : (s.rate >= 50 ? "rt-v-hit" : "rt-v-miss")}">${rate}</td>
      <td>${s.hit}/${s.miss}</td>
      <td>${s.flat}</td>
      <td>${s.pending}</td>
      <td>${s.total} ${flag}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">暂无复盘记录（每日 cron 自动复盘，或管理员手动触发）</td></tr>`;

  // 2) 引用资讯信息量（按被引用次数降序，前若干条）
  const news = (data.news_hit || []).filter(n => n.cited > 0).slice(0, 12);
  const newsRows = news.length ? news.map(n => {
    const rate = n.rate == null ? "—" : n.rate + "%";
    return `<tr>
      <td class="rt-news-title" title="${escapeHtml(n.title)}">${escapeHtml(n.title)}</td>
      <td>${n.cited}</td>
      <td class="${n.rate == null ? "" : (n.rate >= 50 ? "rt-v-hit" : "rt-v-miss")}">${rate}</td>
      <td>${n.hit}/${n.miss}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="4" class="muted">暂无被引用的资讯（仅「自动生成」且观点明确引用「资讯 N」的判断才会挂依据）</td></tr>`;

  // 3) 样本明细（近 limit 条，含判定）
  const samples = data.samples || [];
  const sampleRows = samples.length ? samples.map(r => {
    const v = RT_VERDICT[r.verdict_t5] || RT_VERDICT.pending;
    const basis = (r.basis && r.basis.length) ? r.basis.map(b => escapeHtml(b)).join("、") : `<span class="muted">—</span>`;
    const t5 = r.ret_t5 == null ? "—" : (r.ret_t5 >= 0 ? "+" : "") + r.ret_t5 + "%";
    return `<tr>
      <td>${r.date}</td>
      <td>${escapeHtml(r.seat)}</td>
      <td>${escapeHtml(r.name || r.code)}</td>
      <td>${RT_DIR_LABEL[r.direction] || r.direction}</td>
      <td class="rt-news-title" title="${escapeHtml((r.basis || []).join('、'))}">${basis}</td>
      <td class="${r.ret_t5 == null ? "" : (r.ret_t5 >= 0 ? "rt-v-hit" : "rt-v-miss")}">${t5}</td>
      <td class="${v.cls}">${v.label}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="7" class="muted">暂无样本</td></tr>`;

  el.innerHTML = `
    <div class="rt-review-note">
      判定窗口 T+${horizon}（与策略外推同尺度）；|收益| &lt; ${band}% 视为无效波动、不计入素材质量分母；
      hold/观察无法用收益判对错，同样不计入。样本 &lt; ${minSample} 不展示素材质量徽标。
    </div>
    <div class="rt-review-chart" id="rt-review-chart"></div>
    <div class="rt-hist-sub">席位素材质量（T+${horizon}，= 机械判定命中率）</div>
    <div class="table-wrap"><table class="rt-review-table">
      <thead><tr><th>席位</th><th>素材质量</th><th>命中/未中</th><th>无效波动</th><th>待定</th><th>样本</th></tr></thead>
      <tbody>${seatRows}</tbody>
    </table></div>
    <div class="rt-hist-sub">引用资讯的信息量（被引用 × 兑现）</div>
    <div class="table-wrap"><table class="rt-review-table">
      <thead><tr><th>资讯</th><th>被引用</th><th>信息量</th><th>命中/未中</th></tr></thead>
      <tbody>${newsRows}</tbody>
    </table></div>
    <div class="rt-hist-sub">样本明细（近 ${samples.length} 条）</div>
    <div class="table-wrap rt-scroll"><table class="rt-review-table">
      <thead><tr><th>日期</th><th>席位</th><th>标的</th><th>方向</th><th>依据资讯</th><th>T+${horizon}</th><th>判定</th></tr></thead>
      <tbody>${sampleRows}</tbody>
    </table></div>`;

  // 趋势图：逐日整体命中率（仅已有 hit/miss 的日期）
  const trend = (data.trend || []).filter(t => t.n > 0);
  const chartEl = document.getElementById("rt-review-chart");
  if (chartEl && typeof window.echarts !== "undefined" && trend.length) {
    try {
      if (rtReviewChart) { rtReviewChart.dispose(); rtReviewChart = null; }
      const c = chartTheme();
      const chart = echarts.init(chartEl);
      rtReviewChart = chart;
      chart.setOption({
        grid: { left: 40, right: 16, top: 24, bottom: 28 },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: trend.map(t => t.date.slice(5)), axisLabel: { color: c.axis, fontSize: 11 }, axisLine: { lineStyle: { color: c.axis } } },
        yAxis: { type: "value", min: 0, max: 100, axisLabel: { color: c.axis, formatter: "{value}%" }, splitLine: { lineStyle: { color: c.axis, opacity: .2 } } },
        series: [{ type: "line", smooth: true, data: trend.map(t => t.rate), itemStyle: { color: c.actual }, lineStyle: { color: c.actual, width: 2 }, areaStyle: { color: c.actual, opacity: .12 } }],
      });
    } catch (e) {
      if (chartEl) chartEl.innerHTML = `<span class="muted">趋势图渲染失败</span>`;
    }
  } else if (chartEl) {
    chartEl.style.display = "none";
  }
}

// 跳到该观点的论坛存档帖：直接以详情态进入论坛视图——
// showView 会同时搞定顶栏高亮与 URL（#/forum/<id>），刷新/后退都不丢
async function rtOpenForumThread(tid) {
  if (!tid) return;
  await showView("forum", tid);
}

// 补建论坛存档：保存观点时的自动同步失败是静默的（观点已独立入库、接口仍返回成功），
// 卡片会因此既不显示「查看存档」、用户也无从察觉。这里手动补齐，与自动同步同一套语义。
async function rtArchiveSeat(seatId) {
  const btn = document.querySelector(`[data-archive="${seatId}"]`);
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "补建中…"; }
  try {
    const r = await API("POST", "/api/roundtable/archive", { seat_id: seatId, date: rtState.date });
    showToast(r && r.thread_id ? "已补建论坛存档" : "存档已是最新");
    await rtRefreshPosts();       // 重渲染后按钮会变成「查看存档」，无需手工恢复
  } catch (e) {
    showToast("补建存档失败：" + e.message);
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

// 卡片上内联编辑席位字段（name=AI 名称 / role=AI 角色），回车或失焦保存，Esc 取消
async function rtInlineEditSeat(sid, field, host, currentVal) {
  if (!rtState.admin || host.dataset.editing === "1") return;
  host.dataset.editing = "1";
  const input = document.createElement("input");
  input.className = "rt-inline-input";
  input.value = currentVal || "";
  input.maxLength = 64;
  input.placeholder = field === "name" ? "AI 名称" : "AI 角色";
  host.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const val = input.value.trim();
    try {
      if (val !== (currentVal || "")) {
        await API("PATCH", `/api/roundtable/seats/${sid}`, { [field]: val });
        showToast(field === "name" ? "AI 名称已更新" : "AI 角色已更新");
      }
    } catch (e) { showToast("保存失败：" + e.message); }
    await rtReloadSeats();
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(); }
    else if (e.key === "Escape") { done = true; rtReloadSeats(); }
  };
  input.onblur = () => commit();
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
  rtState.llmStats = await API("GET", "/api/roundtable/llm-stats").catch(() => null);
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
    "将外部 AI（如 ChatGPT / Claude 网页版）对该席位提示词的回复完整粘贴到下方，保存后展示在席位卡片中（再次粘贴可覆盖），同时自动在论坛建档一条存档帖，方便后期回看历史。",
    true, async () => {
      const ta = document.getElementById("rt-modal-text");
      const content = (ta && ta.value || "").trim();
      if (!content) { showToast("内容不能为空"); return false; }
      const r = await API("POST", "/api/roundtable/posts", { seat_id: seatId, content });
      // 接口在「存档失败」时仍返回成功（观点已独立落库），届时 thread_id 为空 ——
      // 把这一点带回弹窗，好让提示如实、并引导用户去卡片上补建。
      return { archived: !!(r && r.thread_id) };
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
        const res = await onSave();
        if (res !== false) {
          root.remove();
          // res 为对象时带 archived 标记：存档失败不该再报「已同步」（观点本身是存好了的）
          if (res && res.archived === false) {
            showToast("已保存到席位，但论坛存档未同步 —— 可在卡片上点「补建存档」重试");
          } else {
            showToast("已保存到席位，并同步到论坛存档");
          }
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
  // 防误触：生成会调用 LLM（产生费用）并同步到论坛存档。
  if (seatIds && seatIds.length === 1) {
    const seat = (rtState.seats || []).find(s => s.id === seatIds[0]);
    const name = seat ? seat.name : "该席位";
    if (!confirm(`确认为「${name}」生成观点？\n将调用 LLM 生成内容并同步到论坛存档。`)) return;
  } else {
    if (!confirm("确认生成今日讨论？\n将为全部自动席位调用 LLM 生成观点，并同步到论坛存档。")) return;
  }
  // 折叠状态下卡片头部的「生成」按钮仍可点，但结果会藏在折叠区里 —— 先把要生成的席位展开。
  // 「生成今日讨论」传 null（全部），刻意不展开：那是用户自己选择只看概览，状态徽标已足够提示。
  if (seatIds && seatIds.length) seatIds.forEach(id => rtFoldSeat(id, false));
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
      showToast(r.message || "没有可自动生成的席位（需将接入方式设为「LLM Key 自动」并配置 Key）");
    } else {
      const okCount = results.filter(x => x.ok).length;
      const fails = results.filter(x => !x.ok);
      const noArch = results.filter(x => x.ok && !x.thread_id).length;   // 生成了但存档没建成
      if (okCount && !fails.length && !noArch) showToast(`已生成 ${okCount} 条观点，并同步到论坛存档`);
      else if (okCount && !fails.length) showToast(`已生成 ${okCount} 条观点，其中 ${noArch} 条论坛存档未同步（可在卡片上「补建存档」）`);
      else if (okCount) showToast(`生成 ${okCount} 条（其中 ${noArch} 条未同步论坛），失败 ${fails.length} 条：${fails[0].error || ""}`);
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
        <label class="rt-field" style="width:130px">接入方式
          <select data-f="mode">
            <option value="manual" ${s.mode === "manual" ? "selected" : ""}>手动回填</option>
            <option value="auto" ${s.mode === "auto" ? "selected" : ""}>LLM Key 自动</option>
            <option value="mcp" ${s.mode === "mcp" ? "selected" : ""}>MCP（待接）</option>
          </select>
        </label>
        <label class="rt-field" style="width:190px">风格模板
          <select data-f="style_id">${rtStyleOptions(s.style_id)}</select>
        </label>
        <label class="rt-field" style="width:120px">AI 角色<input type="text" data-f="role" value="${escapeHtml(s.role)}" list="rt-role-list" placeholder="豆包/元宝…"></label>
      </div>
      <div class="row">
        <label class="rt-field grow" data-persona-wrap>风格提示词<textarea rows="2" data-f="persona" placeholder="该席位的分析风格与偏好（选「自定义」时生效）…">${escapeHtml(s.persona)}</textarea><span class="rt-inline-note hidden" data-style-note></span></label>
        <label class="rt-field" style="width:150px">提供方
          <select data-f="provider">
            ${Object.entries(rtProviders()).map(([p, m]) => `<option value="${p}" ${s.provider === p ? "selected" : ""}>${escapeHtml(m.label || p)}</option>`).join("")}
          </select>
        </label>
        <label class="rt-field grow">模型（留空用默认）<input type="text" data-f="model" value="${escapeHtml(s.model)}" placeholder="${escapeHtml((rtProviders()[s.provider] || {}).model || "")}"></label>
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
        <label class="rt-field" style="width:130px">接入方式
          <select name="mode"><option value="manual">手动回填</option><option value="auto">LLM Key 自动</option><option value="mcp">MCP（待接）</option></select>
        </label>
        <label class="rt-field" style="width:190px">风格模板
          <select name="style_id">${rtStyleOptions(null)}</select>
        </label>
        <label class="rt-field" style="width:120px">AI 角色<input type="text" name="role" list="rt-role-list" placeholder="豆包/元宝…"></label>
      </div>
      <div class="row">
        <label class="rt-field grow" data-persona-wrap>风格提示词<textarea name="persona" rows="2" placeholder="该席位的分析风格与偏好（选「自定义」时生效）…"></textarea><span class="rt-inline-note hidden" data-style-note></span></label>
        <label class="rt-field" style="width:150px">提供方
          <select name="provider">${Object.entries(rtProviders()).map(([p, m]) => `<option value="${p}">${escapeHtml(m.label || p)}</option>`).join("")}</select>
        </label>
        <label class="rt-field grow">模型（留空用默认）<input type="text" name="model"></label>
        <button class="btn-mini" type="submit">添加席位</button>
      </div>
    </form>`;
  // 风格模板下拉一改，就切换「自定义提示词」输入区的显隐与说明
  body.querySelectorAll('[data-f="style_id"], [name="style_id"]').forEach(sel => {
    sel.onchange = () => rtSyncStyleRow(sel.closest(".rt-manage-row"));
  });
  body.querySelectorAll(".rt-manage-row").forEach(r => rtSyncStyleRow(r));
  body.querySelectorAll("[data-save]").forEach(b => b.onclick = async () => {
    const row = b.closest(".rt-manage-row");
    const id = parseInt(b.getAttribute("data-save"), 10);
    const q = (f) => row.querySelector(`[data-f="${f}"]`);
    const payload = {
      name: q("name").value.trim() || "未命名席位",
      persona: q("persona").value,                       // 仅「自定义」时生效；选模板时原样留着，切回来还在
      style_id: Number(q("style_id").value) || 0,        // 0 = 改回自定义（后端据此清除模板引用）
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
        style_id: Number(q("style_id").value) || 0,   // 0 = 自定义
        mode: q("mode").value,
        provider: q("provider").value,
        model: q("model").value.trim(),
        role: q("role").value.trim(),
      });
      f.reset();
      rtSyncStyleRow(f);
      showToast("席位已添加");
      await rtReloadSeats();
    } catch (err) { showToast("添加失败：" + err.message); }
  };
}

// ---- 风格模板（人格 / 风格提示词作为独立管理项，与席位管理、AI 设置并列）----

/** 风格模板下拉的 <option> 列表；sel 为当前选中的模板 id（空/0 = 自定义）。 */
function rtStyleOptions(sel) {
  const styles = rtState.styles || [];
  const cur = Number(sel) || 0;
  return `<option value="">自定义（不套用模板）</option>`
    + styles.map(st => `<option value="${st.id}" ${cur === st.id ? "selected" : ""}>${escapeHtml(st.name)}</option>`).join("");
}

/** 按当前选中的模板切换「自定义提示词」与「模板说明」的显隐。行内没有这些元素时安全返回。 */
function rtSyncStyleRow(row) {
  if (!row) return;
  const sel = row.querySelector('[data-f="style_id"], [name="style_id"]');
  if (!sel) return;
  const wrap = row.querySelector("[data-persona-wrap]");
  const ta = wrap ? wrap.querySelector("textarea") : null;
  const note = row.querySelector("[data-style-note]");
  const st = (rtState.styles || []).find(x => String(x.id) === sel.value);
  // ⚠️ 只切换槽位**内部**的两个元素，不要隐藏槽位本身：否则模板态下整行少一个 flex 项，
  // 剩余项重新分配宽度，会把右边的「启用」勾选框挤到换行（本机 1440px 下会看到「启 用」竖排）。
  if (ta) ta.classList.toggle("hidden", !!st);
  if (note) {
    note.textContent = st ? `由模板「${st.name}」提供 · 改模板即对本席位生效` : "";
    note.classList.toggle("hidden", !st);
  }
}

async function rtReloadStyles() {
  rtState.styles = await API("GET", "/api/roundtable/styles").catch(() => rtState.styles || []);
  rtRenderStyles();
  rtRenderManage();   // 席位行里的模板下拉与「N 个席位在用」要跟着更新
}

function rtRenderStyles() {
  const body = document.getElementById("rt-style-body");
  if (!body) return;
  const styles = rtState.styles || [];
  const rows = styles.map(st => `
    <div class="rt-manage-row">
      <div class="row">
        <label class="rt-field" style="width:150px">模板名<input type="text" data-f="name" value="${escapeHtml(st.name)}"></label>
        <label class="rt-field grow">一句话定位<input type="text" data-f="summary" value="${escapeHtml(st.summary)}" placeholder="列表里显示的风格摘要"></label>
        <span class="rt-badge ${st.builtin ? "mode-manual" : "mode-auto"}">${st.builtin ? "内置" : "自定义"}</span>
        <span class="rt-inline-note">${st.used_by ? `${st.used_by} 个席位在用` : "未被席位引用"}</span>
      </div>
      <div class="row">
        <label class="rt-field grow">提示词正文<textarea rows="2" data-f="persona" placeholder="该风格的分析视角、偏好与表达方式…">${escapeHtml(st.persona)}</textarea></label>
        <button class="btn-mini" data-save-style="${st.id}">保存</button>
        ${st.builtin ? "" : `<button class="btn-mini danger" data-del-style="${st.id}">删除</button>`}
      </div>
    </div>`).join("");
  body.innerHTML = `
    <h4>模板列表 <span class="muted">内置 ${styles.filter(s => s.builtin).length} 套 · 自定义 ${styles.filter(s => !s.builtin).length} 套</span></h4>
    ${rows || '<div class="muted">暂无模板</div>'}
    <h4>新增自定义模板</h4>
    <form id="rt-style-add" class="rt-manage-row">
      <div class="row">
        <label class="rt-field" style="width:150px">模板名<input type="text" name="name" required placeholder="如：红利低波"></label>
        <label class="rt-field grow">一句话定位<input type="text" name="summary" placeholder="列表里显示的风格摘要"></label>
      </div>
      <div class="row">
        <label class="rt-field grow">提示词正文<textarea name="persona" rows="2" placeholder="该风格的分析视角、偏好与表达方式…"></textarea></label>
        <button class="btn-mini" type="submit">添加模板</button>
      </div>
    </form>
    <p class="rt-inline-note">内置模板不可删除（可改名、改正文）；席位仍在引用的模板要先改掉席位才能删。</p>`;
  body.querySelectorAll("[data-save-style]").forEach(b => b.onclick = async () => {
    const row = b.closest(".rt-manage-row");
    const id = parseInt(b.getAttribute("data-save-style"), 10);
    const q = (f) => row.querySelector(`[data-f="${f}"]`);
    try {
      await API("PATCH", `/api/roundtable/styles/${id}`, {
        name: q("name").value.trim(),
        summary: q("summary").value,
        persona: q("persona").value,
      });
      showToast("模板已保存（引用它的席位即刻生效）");
      await rtReloadStyles();
      await rtReloadSeats();   // 席位卡上的「风格 X」要同步
    } catch (e) { showToast("保存失败：" + e.message); }
  });
  body.querySelectorAll("[data-del-style]").forEach(b => b.onclick = async () => {
    const id = parseInt(b.getAttribute("data-del-style"), 10);
    if (!confirm("确认删除该风格模板？")) return;
    try {
      await API("DELETE", `/api/roundtable/styles/${id}`);
      showToast("模板已删除");
      await rtReloadStyles();
    } catch (e) { showToast("删除失败：" + e.message); }
  });
  document.getElementById("rt-style-add").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const q = (n) => f.querySelector(`[name="${n}"]`);
    try {
      await API("POST", "/api/roundtable/styles", {
        name: q("name").value.trim(),
        summary: q("summary").value,
        persona: q("persona").value,
      });
      f.reset();
      showToast("模板已添加");
      await rtReloadStyles();
    } catch (err) { showToast("添加失败：" + err.message); }
  };
}

// ---- AI 设置（API Key，脱敏保存） ----
// 面板展开态。服务商有 20 家左右，全部铺开会把面板拉到三四屏，
// 「＋ 自定义服务商」也会被挤到最下面找不到（2026-10-17 用户反馈「缺少自定义」的实因）。
// 故：默认只展开「已配置 Key」的，其余折叠成一行；自定义入口固定在面板顶部。
let rtProvOpen = null;      // Set<provider>；null = 尚未按「已配置」初始化过
let rtAddFormOpen = false;  // 「＋ 自定义服务商」表单是否展开

// ---- 生成健康度（LLM 调用成功率 / 失败分类 / 超预算兜底，管理员可见） ----
// 数据来自 GET /api/roundtable/llm-stats（llm_call_log 表聚合）。
// 设计意图：把「点了没反应」变成可归因的数字——成功率低时按 by_kind 直接定位
// （budget=推理模型超预算、network=连接、relay_forbidden=中继密钥错）。
const LLM_KIND_LABEL = {
  budget: "推理超预算", empty: "空返回", network: "连接失败",
  relay_forbidden: "中继密钥错", http4xx: "源站4xx", http5xx: "源站5xx", other: "其他",
};
function rtHealthHtml() {
  const s = rtState.llmStats;
  if (!s || !(s.d7 || s.d30)) return "";
  const rateCls = (r) => (r === null ? "" : r >= 90 ? "llm-good" : r >= 70 ? "llm-warn" : "llm-bad");
  const cell = (w) => {
    if (!w || !w.total) return `<span class="muted">暂无调用</span>`;
    const cls = rateCls(w.rate);
    return `<span class="llm-rate ${cls}">${w.rate}%</span><span class="muted"> ${w.ok}/${w.total}</span>`;
  };
  const kinds = Object.entries(s.by_kind || {}).map(([k, n]) =>
    `<span class="llm-chip" title="${escapeHtml(LLM_KIND_LABEL[k] || k)}">${escapeHtml(LLM_KIND_LABEL[k] || k)} ×${n}</span>`).join("");
  const provs = Object.entries(s.by_provider || {}).map(([p, d]) =>
    `<span class="llm-chip${d.ok === d.total ? "" : " llm-chip-warn"}" title="${d.ok}/${d.total} 成功">${escapeHtml(p)} ${d.ok}/${d.total}</span>`).join("");
  const fails = (s.recent_failures || []).map(f => `
    <div class="llm-fail-row"><span class="muted">${escapeHtml(f.time)}</span>
      <span>${escapeHtml(f.seat)}</span><span class="muted">${escapeHtml(f.provider)}${f.relay ? " ·中继" : ""}</span>
      <span class="llm-chip">${escapeHtml(LLM_KIND_LABEL[f.kind] || f.kind || "-")}</span>
      <span class="grow muted" title="${escapeHtml(f.error)}">${escapeHtml(f.error).slice(0, 60)}</span>
    </div>`).join("");
  return `
  <div class="llm-health">
    <div class="row rt-health-head">
      <span class="th">生成健康度</span>
      <span class="muted">近7天 ${cell(s.d7)} · 近30天 ${cell(s.d30)}</span>
      ${s.fallback_7d ? `<span class="llm-chip llm-chip-warn" title="有模型思考发散烧光预算，已自动关思考重试兜底">兜底 ×${s.fallback_7d}</span>` : ""}
    </div>
    ${kinds ? `<div class="row rt-health-line">${kinds}</div>` : ""}
    ${provs ? `<div class="row rt-health-line muted">${provs}</div>` : ""}
    ${fails ? `<div class="llm-fail-list">${fails}</div>` : `<p class="rt-inline-note">近期无失败调用。</p>`}
  </div>`;
}

function rtRenderSettings() {
  const body = document.getElementById("rt-settings-body");
  if (!body) return;
  const provs = rtProviders();
  const cur = (rtState.settings && rtState.settings.providers) || {};

  if (!rtProvOpen) {
    rtProvOpen = new Set(Object.keys(provs).filter(p => (cur[p] || {}).configured));
  }

  // 可达性徽标：本服务器只有 IPv6 出口，IPv4-only 的服务商（如 DeepSeek）配了 Key 也调不通。
  // 不把这件事显式标出来，用户只会看到「点了没反应」。
  // 可达性徽标三态（后端 reach_mode）：direct 直连可达 / relay 自动走中继（可用）/
  // none 真不可用。本服务器只有 IPv6 出口，IPv4-only 的服务商（如 DeepSeek）
  // 必然直连失败，但调用时会自动经中继（relay.kapibala.icu）转发——标红「不可达」
  // 会把能用的服务商误判成坏的，所以有中继兜底的走琥珀色「经中继」中间态。
  const reachTag = (m) => {
    if (!m) return "";
    const mode = m.reach_mode || (m.reachable ? "direct" : "none");
    if (mode === "direct") {
      return `<span class="rt-reach ok" title="本机可以直连该域名">可达</span>`;
    }
    if (mode === "relay") {
      return `<span class="rt-reach relay" title="本机无 IPv4 出口、无法直连该域名；调用时会自动经中继（relay.kapibala.icu）转发，可正常使用。">经中继</span>`;
    }
    // none：区分「本地地址中继帮不了」和「域名不在中继白名单」，title 说清差在哪
    const host = m.host || "";
    const local = /^(localhost|127\.|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.|.*\.local$)/.test(host);
    const why = local
      ? "本地/内网地址，公网中继无法转发——如需使用请在本机可达的网络环境下配置。"
      : "本机无 IPv4 出口无法直连，且该域名不在中继白名单——将其加入 rss-relay 的 ALLOW_HOSTS 并部署 Worker 后即可经中继使用。";
    return `<span class="rt-reach bad" title="${escapeHtml(why)}">不可达</span>`;
  };

  // TypeSafe 判断协议徽标（Jev）：只输出概率不生成正文，能力边界与 chat 协议不同
  const protoTag = (m) => {
    if (!m || m.protocol !== "systemone") return "";
    return `<span class="rt-reach ok" style="color:var(--accent,#2563eb)" title="TypeSafe 判断协议（/v1/systemone）：只输出结构化判断概率（如资讯重要度），不生成自然语言。可用于资讯 AI 评分；不能作为圆桌席位、不能生成市场概要。">判断协议</span>`;
  };

  const rowHtml = (p, m) => {
    m = m || {};
    const c = cur[p] || m;
    const custom = m.builtin === false;
    const open = rtProvOpen.has(p);
    const configured = !!c.configured;
    return `
    <div class="rt-settings-row${open ? " open" : ""}" data-provider="${p}">
      <div class="row rt-prov-head">
        <span class="rt-prov-name" title="${escapeHtml(p)}">${escapeHtml(m.label || p)}${custom ? ` <span class="muted">自定义</span>` : ""}</span>
        ${protoTag(m)}
        ${reachTag(m)}
        <span class="rt-prov-state${configured ? " on" : ""}">${configured ? "已配置" : "未配置"}</span>
        <span class="rt-prov-url muted" title="${escapeHtml(c.base_url || m.base_url || "")}">${escapeHtml(c.base_url || m.base_url || "")}</span>
        <button class="btn-mini ghost" data-toggle-prov="${p}">${open ? "收起" : (configured ? "修改" : "配置")}</button>
      </div>
      <div class="rt-prov-body${open ? "" : " hidden"}">
        <div class="row">
          <label class="rt-field grow">base_url<input type="text" data-f="base_url" value="${escapeHtml(c.base_url || m.base_url || "")}"></label>
          <label class="rt-field grow">model<input type="text" data-f="model" value="${escapeHtml(c.model || m.model || "")}"></label>
        </div>
        <div class="row">
          <label class="rt-field grow">API Key ${configured ? "（已配置 · 留空保存=不修改）" : "（未配置）"}
            <input type="password" data-f="api_key" autocomplete="new-password" placeholder="${configured ? "已配置，输入新值可覆盖" : "sk-..."}">
          </label>
          <button class="btn-mini" data-save-settings="${p}">保存</button>
          ${configured ? `<button class="btn-mini danger" data-clear-settings="${p}">清除 Key</button>` : ""}
          ${custom ? `<button class="btn-mini danger" data-del-settings="${p}">删除</button>` : ""}
        </div>
      </div>
    </div>`;
  };

  // 分组：顺序以后端下发的 groups 为准，未知分组按出现顺序补在后面
  const order = ((rtState.settings && rtState.settings.groups) || ["国内", "海外", "本地 / 自建", "自定义"]).slice();
  const byGroup = {};
  Object.entries(provs).forEach(([p, m]) => {
    const g = (m && m.group) || "自定义";
    (byGroup[g] = byGroup[g] || []).push([p, m]);
  });
  Object.keys(byGroup).forEach(g => { if (order.indexOf(g) < 0) order.push(g); });
  const rows = order.filter(g => byGroup[g] && byGroup[g].length).map(g => `
    <div class="rt-prov-group">${escapeHtml(g)}<span class="muted"> ${byGroup[g].length} 家</span></div>
    ${byGroup[g].map(([p, m]) => rowHtml(p, m)).join("")}`).join("");

  const bar = `
    <div class="rt-settings-bar">
      <span class="rt-inline-note">协议为 OpenAI 兼容的 <code>/chat/completions</code>。默认只展开已配置的服务商，点「配置」展开填写；Key 仅存于服务器本地数据库，页面任何情况不回显。</span>
      <button class="btn-mini ghost" id="rt-add-provider-toggle">＋ 自定义服务商</button>
    </div>`;

  // 资讯 AI 服务商选择（与圆桌席位独立）：资讯评分/市场概要用。
  // Jev（判断协议）只做评分更便宜；市场概要需生成式模型，切到 Jev 后该功能会明确报错。
  const newsProv = (rtState.settings && rtState.settings.news_llm_provider) || "deepseek";
  const newsProvOpts = Object.keys(provs).map(p => {
    const m = provs[p] || {};
    const conf = (cur[p] || {}).configured;
    const proto = m.protocol === "systemone" ? "（判断协议·仅评分）" : "";
    return `<option value="${escapeHtml(p)}"${p === newsProv ? " selected" : ""}${conf ? "" : " disabled"}>${escapeHtml(m.label || p)}${conf ? "" : "（未配置）"}${proto}</option>`;
  }).join("");
  const newsProvBar = `
    <div class="rt-settings-bar">
      <span class="rt-inline-note">资讯 AI 服务商（资讯评分 / 市场概要用，与圆桌席位独立）：</span>
      <select id="rt-news-provider-sel" class="mini-select" style="max-width:220px">${newsProvOpts}</select>
      <button class="btn-mini" id="rt-news-provider-save">切换</button>
      <span class="rt-inline-note muted">当前：${escapeHtml((provs[newsProv] || {}).label || newsProv)}${newsProv === "jev" ? " · 仅支持评分，市场概要需切回生成式服务商" : ""}</span>
    </div>`;

  const health = rtHealthHtml();

  const addRow = `
    <div class="rt-settings-row rt-settings-add${rtAddFormOpen ? "" : " hidden"}" id="rt-add-provider-form">
      <div class="row">
        <label class="rt-field" style="width:190px">服务商名称<input type="text" id="rt-new-provider" placeholder="字母数字_-，如 myllm"></label>
        <label class="rt-field grow">base_url<input type="text" id="rt-new-baseurl" placeholder="https://api.example.com/v1"></label>
        <label class="rt-field grow">model<input type="text" id="rt-new-model" placeholder="留空则需在席位里指定"></label>
        <button class="btn-mini" id="rt-add-provider">添加</button>
      </div>
    </div>`;

  body.innerHTML = bar + newsProvBar + health + addRow + rows +
    `<p class="rt-inline-note">可达性三态：<b>可达</b>＝本机能直连该域名；<b style="color:var(--amber,#b58900)">经中继</b>＝本机只有 IPv6 出口、无法直连，调用时自动经中继（relay.kapibala.icu）转发，可正常使用；<b style="color:var(--red)">不可达</b>＝本地/内网地址（中继转发不到）或域名不在中继白名单（加入 rss-relay 的 ALLOW_HOSTS 并部署后可转经中继）。<br>模型名各厂商改版较快，可直接在展开行里改成当前可用的型号；任何 OpenAI 兼容服务都可用上方「＋ 自定义服务商」接入（填名称 + base_url + model）。</p>`;

  // 资讯 AI 服务商切换
  const npSave = body.querySelector("#rt-news-provider-save");
  if (npSave) npSave.onclick = async () => {
    const sel = body.querySelector("#rt-news-provider-sel");
    const provider = sel.value;
    npSave.disabled = true;
    try {
      await API("POST", "/api/roundtable/news-provider", { provider });
      showToast("已切换资讯 AI 服务商：" + provider);
      rtReloadSettings();
    } catch (err) {
      showToast("切换失败：" + err.message);
    } finally {
      npSave.disabled = false;
    }
  };

  // 折叠 / 展开（重渲染而非就地显隐：折叠态统一由 rtProvOpen 决定，避免有输入值时状态错位）
  body.querySelectorAll("[data-toggle-prov]").forEach(b => b.onclick = () => {
    const p = b.getAttribute("data-toggle-prov");
    if (rtProvOpen.has(p)) rtProvOpen.delete(p); else rtProvOpen.add(p);
    rtRenderSettings();
  });

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

  // 删除自定义服务商（内置项不渲染该按钮，后端也会拒绝）
  body.querySelectorAll("[data-del-settings]").forEach(b => b.onclick = async () => {
    const provider = b.getAttribute("data-del-settings");
    if (!confirm(`确认删除自定义服务商「${provider}」？其 base_url / model / Key 将一并清除。`)) return;
    try {
      await API("PUT", "/api/roundtable/settings", { provider, delete: true });
      rtProvOpen.delete(provider);
      showToast("已删除");
      await rtReloadSettings();
    } catch (e) { showToast("删除失败：" + e.message); }
  });

  // 自定义服务商表单由顶部按钮控制显隐
  const addToggle = document.getElementById("rt-add-provider-toggle");
  if (addToggle) addToggle.onclick = () => {
    rtAddFormOpen = !rtAddFormOpen;
    const form = document.getElementById("rt-add-provider-form");
    if (form) form.classList.toggle("hidden", !rtAddFormOpen);
    addToggle.textContent = rtAddFormOpen ? "收起自定义入口" : "＋ 自定义服务商";
    if (rtAddFormOpen) {
      const n = document.getElementById("rt-new-provider");
      if (n) n.focus();
    }
  };

  // 新增自定义服务商：后端要求必须带 base_url（自定义项没有内置默认值）
  const addBtn = document.getElementById("rt-add-provider");
  if (addBtn) addBtn.onclick = async () => {
    const name = document.getElementById("rt-new-provider").value.trim();
    const bu = document.getElementById("rt-new-baseurl").value.trim();
    const md = document.getElementById("rt-new-model").value.trim();
    if (!name || !bu) { showToast("请填写服务商名称与 base_url"); return; }
    try {
      await API("PUT", "/api/roundtable/settings", { provider: name, base_url: bu, model: md });
      if (rtProvOpen) rtProvOpen.add(name);      // 新增后直接展开，方便接着填 Key
      showToast(`已添加「${name}」，请再填入它的 API Key`);
      await rtReloadSettings();
    } catch (e) { showToast("添加失败：" + e.message); }
  };
}

// 圆桌入口的页级绑定统一收口到 06-pages.js 的 bindNav()

