// 05-roundtable —— 圆桌讨论（多 AI 席位 · 自动/手动双通道）
// ===== 圆桌讨论（多 AI 席位 · 双通道：自动席位配 Key 直调 LLM / 手动席位复制提示词外部咨询后回填） =====
// 服务商清单的**唯一数据源在后端**（app.py → PROVIDER_DEFAULTS），由
// GET /api/roundtable/settings 连同 base_url / model / 本机可达性一起下发。
// 下表仅用于「后端数据尚未加载」时兜底渲染，字段与后端保持同名；
// 用户在 AI 设置里自建的自定义服务商不在本表内，只来自后端。
const RT_PROVIDERS_FALLBACK = {
  deepseek:    { label: "DeepSeek",   base_url: "https://api.deepseek.com",                         model: "deepseek-chat" },
  zhipu:       { label: "智谱 GLM",   base_url: "https://open.bigmodel.cn/api/paas/v4",             model: "glm-4-air" },
  qwen:        { label: "通义千问",   base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  doubao:      { label: "火山豆包",   base_url: "https://ark.cn-beijing.volces.com/api/v3",          model: "doubao-pro-32k" },
  siliconflow: { label: "硅基流动",   base_url: "https://api.siliconflow.cn/v1",                    model: "Qwen/Qwen2.5-7B-Instruct" },
  openai:      { label: "OpenAI",     base_url: "https://api.openai.com/v1",                         model: "gpt-4o-mini" },
  openrouter:  { label: "OpenRouter", base_url: "https://openrouter.ai/api/v1",                      model: "openai/gpt-4o-mini" },
  moonshot:    { label: "Kimi",       base_url: "https://api.moonshot.cn/v1",                        model: "moonshot-v1-8k" },
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
let rtState = { date: null, snap: null, seats: [], posts: {}, settings: null, history: null, admin: false };
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
        <h3>AI 席位 <span class="muted">接入方式：LLM Key 自动（后端直调）· 手动回填（复制提示词外部咨询）· MCP（待接）</span>
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
              <label class="rt-field grow">人格 / 风格提示词<textarea name="persona" rows="2" placeholder="该席位的分析风格与偏好…"></textarea></label>
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
  const addSeatToggle = document.getElementById("rt-add-seat-toggle");
  if (addSeatToggle) addSeatToggle.onclick = () => document.getElementById("rt-add-seat-panel").classList.toggle("hidden");
  const addSeatCancel = document.getElementById("rt-add-seat-cancel");
  if (addSeatCancel) addSeatCancel.onclick = () => { document.getElementById("rt-add-seat-panel").classList.add("hidden"); };
  const addSeatForm = document.getElementById("rt-seat-quick-add");
  if (addSeatForm) addSeatForm.onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const q = (n) => f.querySelector(`[name="${n}"]`);
    try {
      await API("POST", "/api/roundtable/seats", {
        name: q("name").value.trim() || "新席位",
        role: q("role").value.trim(),
        persona: q("persona").value,
        mode: q("mode").value,   // 接入方式：manual 手动回填 / auto LLM Key 自动 / mcp MCP（待接）
        provider: "deepseek",
        model: "",
      });
      f.reset();
      document.getElementById("rt-add-seat-panel").classList.add("hidden");
      showToast("席位已添加");
      await rtReloadSeats();
    } catch (err) { showToast("添加失败：" + err.message); }
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
    const isAuto = s.mode === "auto";     // 仅 auto 走后端直调 LLM；manual / mcp 均走手动通道
    const modeInfo = rtModeInfo(s.mode);  // 接入方式标注
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
      showToast(r.message || "没有可自动生成的席位（需将接入方式设为「LLM Key 自动」并配置 Key）");
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
        <label class="rt-field" style="width:130px">接入方式
          <select data-f="mode">
            <option value="manual" ${s.mode === "manual" ? "selected" : ""}>手动回填</option>
            <option value="auto" ${s.mode === "auto" ? "selected" : ""}>LLM Key 自动</option>
            <option value="mcp" ${s.mode === "mcp" ? "selected" : ""}>MCP（待接）</option>
          </select>
        </label>
        <label class="rt-field grow">人格 / 风格提示词<textarea rows="2" data-f="persona">${escapeHtml(s.persona)}</textarea></label>
        <label class="rt-field" style="width:120px">AI 角色<input type="text" data-f="role" value="${escapeHtml(s.role)}" list="rt-role-list" placeholder="豆包/元宝…"></label>
      </div>
      <div class="row">
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
        <label class="rt-field grow">人格 / 风格提示词<textarea name="persona" rows="2" placeholder="该席位的分析风格与偏好…"></textarea></label>
        <label class="rt-field" style="width:120px">AI 角色<input type="text" name="role" list="rt-role-list" placeholder="豆包/元宝…"></label>
      </div>
      <div class="row">
        <label class="rt-field" style="width:150px">提供方
          <select name="provider">${Object.entries(rtProviders()).map(([p, m]) => `<option value="${p}">${escapeHtml(m.label || p)}</option>`).join("")}</select>
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
  const provs = rtProviders();
  const cur = (rtState.settings && rtState.settings.providers) || {};

  // 可达性徽标：本服务器只有 IPv6 出口，IPv4-only 的服务商（如 DeepSeek）配了 Key 也调不通。
  // 不把这件事显式标出来，用户只会看到「点了没反应」。
  const reachTag = (m) => {
    if (!m || typeof m.reachable !== "boolean") return "";
    return m.reachable
      ? `<span class="rt-reach ok" title="本机可以直连该域名">可达</span>`
      : `<span class="rt-reach bad" title="本机连不上该域名：本服务器只有 IPv6 出口，IPv4-only 的服务商（如 DeepSeek）在此不可用，只能换服务商或走中继。">不可达</span>`;
  };

  const rows = Object.entries(provs).map(([p, m]) => {
    m = m || {};
    const c = cur[p] || m;
    const custom = m.builtin === false;
    return `
    <div class="rt-settings-row" data-provider="${p}">
      <div class="row">
        <span class="rt-seat-name" style="width:190px" title="${escapeHtml(p)}">${escapeHtml(m.label || p)}${custom ? ` <span class="muted">自定义</span>` : ""} ${reachTag(m)}</span>
        <label class="rt-field grow">base_url<input type="text" data-f="base_url" value="${escapeHtml(c.base_url || m.base_url || "")}"></label>
        <label class="rt-field grow">model<input type="text" data-f="model" value="${escapeHtml(c.model || m.model || "")}"></label>
      </div>
      <div class="row">
        <label class="rt-field grow">API Key ${c.configured ? "（已配置 · 留空保存=不修改）" : "（未配置）"}
          <input type="password" data-f="api_key" autocomplete="new-password" placeholder="${c.configured ? "已配置，输入新值可覆盖" : "sk-..."}">
        </label>
        <button class="btn-mini" data-save-settings="${p}">保存</button>
        ${c.configured ? `<button class="btn-mini danger" data-clear-settings="${p}">清除 Key</button>` : ""}
        ${custom ? `<button class="btn-mini danger" data-del-settings="${p}">删除</button>` : ""}
      </div>
    </div>`;
  }).join("");

  const addRow = `
    <div class="rt-settings-row rt-settings-add">
      <div class="row">
        <label class="rt-field" style="width:190px">＋ 自定义服务商<input type="text" id="rt-new-provider" placeholder="名称，如 myllm"></label>
        <label class="rt-field grow">base_url<input type="text" id="rt-new-baseurl" placeholder="https://api.example.com/v1"></label>
        <label class="rt-field grow">model<input type="text" id="rt-new-model" placeholder="留空则需在席位里指定"></label>
        <button class="btn-mini" id="rt-add-provider">添加</button>
      </div>
    </div>`;

  body.innerHTML = rows + addRow + `<p class="rt-inline-note">协议为 OpenAI 兼容的 <code>/chat/completions</code>：内置常见服务商，也可用上方「＋ 自定义服务商」接入任意同类服务（填名称 + base_url + model，名称只作区分）。Key 仅存于服务器本地数据库，页面任何情况不回显；清除 Key 后自动席位将无法生成。<br>「可达」= 本服务器能否直连该域名。本机只有 IPv6 出口，标「本机不可达」的服务商即使配好 Key 也无法生成。</p>`;
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
      showToast("已删除");
      await rtReloadSettings();
    } catch (e) { showToast("删除失败：" + e.message); }
  });

  // 新增自定义服务商：后端要求必须带 base_url（自定义项没有内置默认值）
  const addBtn = document.getElementById("rt-add-provider");
  if (addBtn) addBtn.onclick = async () => {
    const name = document.getElementById("rt-new-provider").value.trim();
    const bu = document.getElementById("rt-new-baseurl").value.trim();
    const md = document.getElementById("rt-new-model").value.trim();
    if (!name || !bu) { showToast("请填写服务商名称与 base_url"); return; }
    try {
      await API("PUT", "/api/roundtable/settings", { provider: name, base_url: bu, model: md });
      showToast(`已添加「${name}」，请再填入它的 API Key`);
      await rtReloadSettings();
    } catch (e) { showToast("添加失败：" + e.message); }
  };
}

// 圆桌入口的页级绑定统一收口到 06-pages.js 的 bindNav()

