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
let rtState = { date: null, snap: null, seats: [], styles: [], posts: {}, settings: null, history: null, admin: false };
const rtAdmin = () => !!(me && me.is_admin);

// P7：首页已改为「资讯工作台」，圆桌是二级页。
// 页内不再放「← 交易仪表台」——顶栏已有全部页面入口，二级页里的「返回某页」既冗余、
// 又把圆桌错误地挂在仪表台之下（这个按钮是 P1 侧栏时代的遗留）。

async function renderRoundtable() {
  if (isGuest || !token) { showAuth("login"); return; }
  rtState.admin = rtAdmin();
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
      <div class="panel"><h3>今日大盘</h3><div class="rt-indices" id="rt-indices"><span class="muted">加载中…</span></div></div>
      <div class="rt-indices rt-metrics" id="rt-kpi">
        <div class="rt-index"><div class="name">总市值</div><div class="val" id="rt-kpi-mv">—</div></div>
        <div class="rt-index"><div class="name">浮动盈亏</div><div class="val" id="rt-kpi-un">—</div></div>
        <div class="rt-index"><div class="name">累计实现盈亏</div><div class="val" id="rt-kpi-real">—</div></div>
      </div>
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
        <h3>AI 席位 <span class="muted">接入方式：LLM Key 自动（后端直调）· 手动回填（复制提示词外部咨询）· MCP（待接）；每日观点自动在论坛建档存档</span>
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
  document.getElementById("rt-history-toggle").onclick = () => {
    const body = document.getElementById("rt-history-body");
    const btn = document.getElementById("rt-history-toggle");
    const hidden = body.classList.toggle("hidden");
    btn.textContent = hidden ? "展开" : "收起";
  };
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
      const [settings, styles] = await Promise.all([
        API("GET", "/api/roundtable/settings").catch(() => null),
        API("GET", "/api/roundtable/styles").catch(() => []),
      ]);
      rtState.settings = settings;
      rtState.styles = styles || [];
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
function rtApplyFold(card, folded) {
  card.classList.toggle("collapsed", folded);
  const caret = card.querySelector(".rt-seat-caret");
  if (caret) {
    caret.title = folded ? "展开该席位观点" : "折叠该席位观点";
    caret.setAttribute("aria-expanded", folded ? "false" : "true");
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
        </div>
        <div class="rt-seat-actions">${actions}</div>
      </div>
      <div class="rt-seat-body ${post ? "" : "empty"}">${post ? escapeHtml(post.content) : "尚未生成：点击右上角按钮开始"}</div>
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

// 跳到该观点的论坛存档帖：先走 showView("forum") 再定位帖子——
// 保证顶栏高亮与 URL 同步（直接把 main 换成详情会让导航与实际页面不一致）。
async function rtOpenForumThread(tid) {
  if (!tid) return;
  await showView("forum");
  await fmOpenThread(tid);
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
  const reachTag = (m) => {
    if (!m || typeof m.reachable !== "boolean") return "";
    return m.reachable
      ? `<span class="rt-reach ok" title="本机可以直连该域名">可达</span>`
      : `<span class="rt-reach bad" title="本机连不上该域名：本服务器只有 IPv6 出口，IPv4-only 的服务商（如 DeepSeek）在此不可用，只能换服务商或走中继。">不可达</span>`;
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

  const addRow = `
    <div class="rt-settings-row rt-settings-add${rtAddFormOpen ? "" : " hidden"}" id="rt-add-provider-form">
      <div class="row">
        <label class="rt-field" style="width:190px">服务商名称<input type="text" id="rt-new-provider" placeholder="字母数字_-，如 myllm"></label>
        <label class="rt-field grow">base_url<input type="text" id="rt-new-baseurl" placeholder="https://api.example.com/v1"></label>
        <label class="rt-field grow">model<input type="text" id="rt-new-model" placeholder="留空则需在席位里指定"></label>
        <button class="btn-mini" id="rt-add-provider">添加</button>
      </div>
    </div>`;

  body.innerHTML = bar + addRow + rows +
    `<p class="rt-inline-note">「可达」= 本服务器能否直连该 base_url 的主机端口。本机只有 IPv6 出口，标「本机不可达」的服务商即使配好 Key 也无法生成，需换服务商或走中继。<br>模型名各厂商改版较快，可直接在展开行里改成当前可用的型号；任何 OpenAI 兼容服务都可用上方「＋ 自定义服务商」接入（填名称 + base_url + model）。</p>`;

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

