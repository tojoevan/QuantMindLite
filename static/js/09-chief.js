// 09-chief —— 主事工作台（v2：信息中枢 + 执行跟踪器，不做决策 / 不自动下单）
// 设计来源：docs/v2-主事工作台设计方案.md §12 / §13；步骤见 docs/v2-开发步骤拆分.md。
//
// ✅ 已落地：
//   S1  chief_* 四张新表（db.py）
//   S2  页面骨架与入口（#/chief）
//   A3  风险偏好 / 约束（自由文本偏好 + 允许换仓开关，存 kind=preference）
//   A5  近期政策 / 市场变化（资讯选条）
//   B/B3-B5  会话 + inspect（审仓位）→ ruleinspect（审纪律→四件套）→ draft → review → final
//   C2/C3  动作行标注与留痕   D1 历史会话
// ⛔ 已下线：
//   A1 逐标的精确目标权重（2026-10-22，过于僵化）→ 改为 Gemini 审视持仓给相对态度
//   A2 结构化纪律规则 + 逐条红黄绿校验（2026-10-23）→ 改为 ChatGPT 审视纪律产出四件套
//      （constants CHIEF_DEFAULT_RULES / chiefScopeLabel / chiefScopeKey / chiefScopeArg
//       与 A2 全部函数随之下线，不要恢复）
//
// 卡片逐步填充到 #chief-cards 内。样式复用现有 .panel / .table / .cat-tag / .btn-mini。

const chiefState = {
  positions: [],
  // 注：weights 随 A1「逐标的精确目标权重」废止已在 UI 上无用，但 chiefSaveWeights
  // 仍会读写它（该函数按 2026-10-22 的决定保留备用），故字段留存。
  weights: {},
  // ⚠️ rules / checks / breaks / fromDefault 已随 A2「结构化纪律规则」下线（2026-10-23）；
  // 纪律约束改由 ruleinspect 轮的四件套原文承载，平台不再本地持有阈值。
  pref: { note: "", allow_rebalance: false },  // A3 风险偏好 / 约束
  // ---- v2 主事工作台：会话 / 三轮 / 动作行 / 历史（B·C·D 组）----
  sessionId: null,             // 当前一期会话 id
  sessions: [],                // 历史会话列表（倒序）
  turns: {},                   // {role: {prompt, reply}}，role 见 CHIEF_ROLES
  actions: [],                 // 已保存动作行（由 agent 提取或手动维护，前端不再自动提取）
};

// 一期会话要回填的全部角色。inspect 与 ruleinspect 同属第 ① 步的两张卡，两者平行、
// 互不依赖（可以先审仓位也可以先审纪律）。新增 / 改名时务必同步这里——
// 「新建会话清空」与「打开会话回填」都遍历它，漏一个就会留下上一期的旧原文。
const CHIEF_ROLES = ["inspect", "ruleinspect", "draft", "review", "final"];

function chiefFmtPct(v) { return v == null ? "—" : v.toFixed(1) + "%"; }

// 市值 / 收益率格式化（表格转纯展示后用：原来那两列是「目标权重输入」与「偏离」）
function chiefFmtMoney(v) {
  const n = Number(v) || 0;
  if (!n) return "—";
  if (n >= 10000) return (n / 10000).toFixed(2) + " 万";
  return n.toFixed(2);
}

function chiefFmtRet(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (isNaN(n)) return "—";
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
}

function chiefOpText(op) { return op === "ge" ? "≥" : "≤"; }

function chiefTotalMv() {
  return chiefState.positions.reduce((s, p) => s + (p.market_value || 0), 0);
}

// 当前占比（占总市值，%）；无市值则 0
function chiefCurPct(p) {
  const t = chiefTotalMv();
  return t > 0 ? (p.market_value || 0) / t * 100 : 0;
}

// ---------- A1：目标权重表 ----------
function chiefRenderWeights() {
  const tb = document.getElementById("chief-weights-body");
  if (!tb) return;
  const pos = [...chiefState.positions].sort(
    (a, b) => (b.market_value || 0) - (a.market_value || 0));
  if (!pos.length) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">暂无标的</td></tr>`;
    chiefRefreshSum();
    return;
  }
  tb.innerHTML = pos.map(p => {
    const cur = chiefCurPct(p);
    return `
      <tr data-pid="${p.id}" data-cat="${escapeHtml(p.category || "")}">
        <td><b>${escapeHtml(p.name || "")}</b>${p.code ? ` <span class="muted">${escapeHtml(p.code)}</span>` : ""}</td>
        <td>${p.category ? `<span class="cat-tag">${escapeHtml(p.category)}</span>` : '<span class="muted">—</span>'}</td>
        <td>${chiefFmtPct(cur)}</td>
        <td>${chiefFmtMoney(p.market_value)}</td>
        <td class="${(p.pct || 0) >= 0 ? "pos" : "neg"}">${chiefFmtRet(p.pct)}</td>
      </tr>`;
  }).join("");
  chiefRefreshSum();
  chiefRenderConcentration();
}

// 底部合计：改为「合计市值」（原先显示的是目标权重合计，那个概念已废止）
function chiefRefreshSum() {
  const el = document.getElementById("chief-w-sum");
  if (!el) return;
  const t = chiefTotalMv();
  el.textContent = t > 0 ? chiefFmtMoney(t) : "—";
}
// 纯前端算（positions 自带 market_value / name），无后端依赖。集中度越高风险越大：
// ≥85% 红（过高）/ ≥70% 黄（偏高）/ 其余中性。
function chiefRenderConcentration() {
  const el = document.getElementById("chief-conc");
  if (!el) return;
  const pos = chiefState.positions
    .filter(p => (p.market_value || 0) > 0)
    .sort((a, b) => (b.market_value || 0) - (a.market_value || 0));
  if (!pos.length) { el.innerHTML = ""; return; }
  const total = pos.reduce((s, p) => s + (p.market_value || 0), 0);
  const top = pos.slice(0, 3);
  const sumTop = top.reduce((s, p) => s + (p.market_value || 0), 0);
  const pct = total > 0 ? sumTop / total * 100 : 0;
  const cls = pct >= 85 ? "pos" : (pct >= 70 ? "chief-warn-text" : "");
  const parts = top.map(p =>
    `${escapeHtml(p.name || ("#" + p.id))} ${chiefFmtPct(total > 0 ? (p.market_value || 0) / total * 100 : 0)}`);
  const label = top.length === 3 ? "Top3 集中度" : `Top${top.length} 集中度`;
  el.innerHTML = `<span class="${cls}"><b>${label} ${pct.toFixed(1)}%</b></span>` +
    `<span class="muted">（${parts.join(" · ")}）</span>`;
}

// ⛔ 已停用（2026-10-22）：逐标的「精确目标权重」改为交由 Gemini 审视（见 ↓ 审视区），
// 因多标的场景下逐个填数过于僵化、且从未被配置过。函数与后端 PUT 端点保留但不接任何入口，
// 以免将来要复用时重写；页面上已无 #chief-w-save，故它不会被触发。
async function chiefSaveWeights() {
  const items = [];
  document.querySelectorAll("#chief-weights-body input[data-pid]").forEach(inp => {
    const v = parseFloat(inp.value);
    items.push({
      project_id: parseInt(inp.getAttribute("data-pid"), 10),
      weight: (isNaN(v) || v <= 0) ? 0 : v,
    });
  });
  const btn = document.getElementById("chief-w-save");
  const hint = document.getElementById("chief-w-hint");
  if (btn) btn.disabled = true;
  try {
    const r = await API("PUT", "/api/chief/goals/weight", { items });
    chiefState.weights = {};
    items.forEach(it => {
      if (it.weight > 0) chiefState.weights[it.project_id] = it.weight;
    });
    if (hint) hint.textContent = `已保存 ${r && r.saved ? r.saved : 0} 个标的的目标权重`;
    if (typeof showToast === "function") showToast("目标权重已保存");
    chiefRenderWeights();
  } catch (e) {
    if (hint) hint.textContent = "保存失败：" + (e.message || String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------- A3：风险偏好 / 约束 ----------
async function chiefSavePref() {
  const ta = document.getElementById("chief-pref-note");
  const sw = document.getElementById("chief-pref-rebalance");
  const hint = document.getElementById("chief-p-ref-hint");
  const btn = document.getElementById("chief-pref-save");
  const note = ta ? ta.value : "";
  const allow = !!(sw && sw.checked);
  if (btn) btn.disabled = true;
  try {
    await API("PUT", "/api/chief/goals/preference", { note, allow_rebalance: allow });
    chiefState.pref = { note, allow_rebalance: allow };
    if (hint) hint.textContent = "已保存风险偏好";
    if (typeof showToast === "function") showToast("风险偏好已保存");
  } catch (e) {
    if (hint) hint.textContent = "保存失败：" + (e.message || String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 载入持仓 + 已存目标权重
async function chiefLoadGoals() {
  const tb = document.getElementById("chief-weights-body");
  const hint = document.getElementById("chief-w-hint");
  try {
    // ⚠️ 不再请求 kind=rule（A2 已下线）；weight 仍取（chiefSaveWeights 保留备用）
    const [ov, gs, ps] = await Promise.all([
      API("GET", "/api/overview"),
      API("GET", "/api/chief/goals?kind=weight"),
      API("GET", "/api/chief/goals?kind=preference"),
    ]);
    chiefState.positions = (ov && ov.positions) || [];
    const w = {};
    ((gs && gs.items) || []).forEach(g => {
      const pid = parseInt(String(g.key || "").replace(/^p:/, ""), 10);
      const v = parseFloat(g.value);
      if (pid && !isNaN(v) && v > 0) w[pid] = v;
    });
    chiefState.weights = w;
    const prefs = ((ps && ps.items) || []);
    chiefState.pref = {
      note: (prefs.find(x => x.key === "note") || {}).value || "",
      allow_rebalance: ((prefs.find(x => x.key === "allow_rebalance") || {}).value || "0") === "1",
    };
    // 把已加载的风险偏好同步进 DOM（卡片在 renderChief 时按初始空值渲染，
    // 之后异步拉到的偏好不会自动回填，必须手动同步，否则刷新后不显示已存内容）
    const ta = document.getElementById("chief-pref-note");
    if (ta) ta.value = chiefState.pref.note || "";
    const sw = document.getElementById("chief-pref-rebalance");
    if (sw) sw.checked = !!chiefState.pref.allow_rebalance;
    chiefRenderWeights();
    if (hint) hint.textContent = "目标合计不必等于 100%（留空 = 未配置），配置将作为主事提示词的上下文头";
  } catch (e) {
    if (tb) tb.innerHTML = `<tr><td colspan="5" class="muted">加载失败：${escapeHtml(e.message || String(e))}</td></tr>`;
    if (hint) hint.textContent = "";
  }
}

function renderChief() {
  const main = document.getElementById("main");
  if (!main) return;
  main.innerHTML = `
    <div class="chief-page">
      <div class="chief-steps" id="chief-steps"></div>
      <div class="panel" id="chief-step-inspect">
        <h3>① 审视目标与规则 <span class="muted">仓位 → Gemini 审视；纪律 → ChatGPT 审视并产出「目标·计划·规则·纪律」四件套，由你终裁</span></h3>
        <div class="chief-sessionbar">
          <span id="chief-cur-session" class="muted">本期会话：未创建（生成提示词时自动新建）</span>
          <button class="btn-mini chief-minor" id="chief-new-session" type="button">新建一期</button>
        </div>
        <div id="chief-cards">
          <div class="chief-sub">
            <h4>持仓全景 <span class="muted">按市值倒序 · 审视对象（原「逐标的填目标权重」已废止）</span></h4>
            <div class="table-wrap">
              <table class="table" id="chief-weights">
                <thead><tr>
                  <th>标的</th>
                  <th>领域</th>
                  <th>当前占比</th>
                  <th>市值</th>
                  <th>收益率</th>
                </tr></thead>
                <tbody id="chief-weights-body">
                  <tr><td colspan="5" class="muted">加载中…</td></tr>
                </tbody>
                <tfoot><tr>
                  <td colspan="3" class="muted">合计</td>
                  <td id="chief-w-sum">—</td>
                  <td></td>
                </tr></tfoot>
              </table>
            </div>
            <div class="chief-conc" id="chief-conc"></div>
          </div>

          <div class="chief-sub">
            <h4>交由 Gemini 审视仓位 <span id="chief-inspect-status" class="muted"></span></h4>
            <div class="muted chief-sub-desc">
              不为每只标的填精确目标——多标的场景下那既繁琐又僵化。改由 Gemini 给出
              <b>相对态度 + 大致区间</b>（如「核心持有 / 逐步减持 / 10–15%」），你终裁后作为后续各轮依据。
            </div>
            <div class="chief-round-actions">
              <button class="btn-mini" data-gen="inspect" type="button">生成审视提示词</button>
              <button class="btn-mini chief-minor" data-copy="inspect" type="button">复制提示词</button>
              <button class="btn-mini" data-save="inspect" type="button">保存反馈</button>
            </div>
            <textarea class="chief-prompt" id="chief-prompt-inspect" placeholder="点「生成审视提示词」自动装配（持仓全景 + 风险偏好 + 素材体 + 审视指令）"></textarea>
            <textarea class="chief-reply" id="chief-reply-inspect" placeholder="在此粘贴 Gemini 的仓位审视结论，保存后作为本期 ②③④ 的依据"></textarea>
          </div>

          <!-- ⚠️ 原「纪律规则」结构化配置卡（A2）已于 2026-10-23 下线：
               纪律改由下方 ChatGPT 审视产出四件套承载，平台不再保存结构化阈值。 -->

          <div class="chief-sub" id="chief-card-ruleinspect">
            <h4>交由 ChatGPT 审视纪律 <span id="chief-ruleinspect-status" class="muted"></span></h4>
            <div class="muted chief-sub-desc">
              纪律不是定好就完事的：市场、资金和风险偏好都在变。把你的
              <b>持仓全景 + 风险偏好 + 上一版纲领</b>交给 ChatGPT，产出 / 修订你的
              <b>投资目标 / 投资计划 / 交易规则 / 交易纪律</b> 四件套，作为本期最上位的纲领。
              与「审仓位」并列，<b>两卡互不依赖，先后随意</b>；四件套会进入 ②③④ 的提示词。
            </div>
            <div class="chief-round-actions">
              <button class="btn-mini" data-gen="ruleinspect" type="button">生成纪律审视提示词</button>
              <button class="btn-mini chief-minor" data-copy="ruleinspect" type="button">复制提示词</button>
              <button class="btn-mini" data-save="ruleinspect" type="button">保存反馈</button>
            </div>
            <textarea class="chief-prompt" id="chief-prompt-ruleinspect" placeholder="点「生成纪律审视提示词」自动装配（持仓全景 + 风险偏好 + 上一版纲领 + 素材体 + 四件套指令）"></textarea>
            <textarea class="chief-reply" id="chief-reply-ruleinspect" placeholder="在此粘贴 ChatGPT 的四件套原文（投资目标 / 投资计划 / 交易规则 / 交易纪律），保存后作为本期 ②③④ 的依据"></textarea>
            <div class="muted chief-policy-label">已保存的纲领</div>
            <div id="chief-policy-render" class="md-body"></div>
          </div>
          <div class="chief-sub">
            <h4>风险偏好 / 约束 <span class="muted">自由文本约束 · 换仓开关</span></h4>
            <div class="chief-pref">
              <label class="chief-field">
                <span class="chief-field-label">风险偏好与约束说明</span>
                <textarea id="chief-pref-note" class="chief-textarea" rows="4"
                  placeholder="如：最大回撤容忍 -15%；偏价值而非成长；不接受杠杆…（将作为主事提示词上下文）">${escapeHtml(chiefState.pref.note || "")}</textarea>
              </label>
              <label class="chief-switch-row">
                <span class="chief-field-label">允许主事建议换仓</span>
                <span class="switch">
                  <input type="checkbox" id="chief-pref-rebalance" ${chiefState.pref.allow_rebalance ? "checked" : ""}>
                  <span class="slider"></span>
                </span>
                <span class="muted chief-switch-hint">关闭时，主事只评估当前持仓、不提新增/减持标的</span>
              </label>
            </div>
            <div class="chief-actions">
              <button class="btn-mini" id="chief-pref-save" type="button">保存偏好</button>
              <span class="muted" id="chief-p-ref-hint"></span>
            </div>
          </div>

          <div class="chief-sub">
            <h4>近期政策 / 市场变化 <span class="muted">★ = 与我的持仓相关，供规则复核对照（§4.2.1）</span></h4>
            <div id="chief-news" class="chief-news">加载中…</div>
          </div>
        </div>

        <div class="chief-round" id="chief-step-draft">
          <h4>② 初稿（→ Gemini）<span id="chief-draft-status" class="muted"></span></h4>
          <div class="chief-round-actions">
            <button class="btn-mini" data-gen="draft" type="button">生成提示词</button>
            <button class="btn-mini chief-minor" data-copy="draft" type="button">复制提示词</button>
            <button class="btn-mini" data-save="draft" type="button">保存答复</button>
          </div>
          <textarea class="chief-prompt" id="chief-prompt-draft" placeholder="点「生成提示词」自动装配（上下文头 + 素材体 + 指令尾）；也可手动粘贴/微调"></textarea>
          <textarea class="chief-reply" id="chief-reply-draft" placeholder="在此粘贴 Gemini 初稿原文，点「保存答复」留档"></textarea>
        </div>

        <div class="chief-round" id="chief-step-review">
          <h4>③ 审查（→ ChatGPT）<span id="chief-review-status" class="muted"></span></h4>
          <div class="chief-round-actions">
            <button class="btn-mini" data-gen="review" type="button">内嵌初稿生成</button>
            <button class="btn-mini chief-minor" data-copy="review" type="button">复制提示词</button>
            <button class="btn-mini" data-save="review" type="button">保存答复</button>
          </div>
          <textarea class="chief-prompt" id="chief-prompt-review" placeholder="自动内嵌 ② 初稿原文 + 反向审查指令"></textarea>
          <textarea class="chief-reply" id="chief-reply-review" placeholder="在此粘贴 ChatGPT 审查意见原文，点「保存答复」留档"></textarea>
        </div>

        <div class="chief-round" id="chief-step-final">
          <h4>④ 终稿（→ Gemini，执行依据）<span id="chief-final-status" class="muted"></span></h4>
          <div class="chief-round-actions">
            <button class="btn-mini" data-gen="final" type="button">内嵌审查生成</button>
            <button class="btn-mini chief-minor" data-copy="final" type="button">复制提示词</button>
            <button class="btn-mini" data-save="final" type="button">保存答复</button>
          </div>
          <textarea class="chief-prompt" id="chief-prompt-final" placeholder="自动内嵌 ③ 审查意见 + 终稿指令（须显式说明采纳/未采纳哪些审查意见）"></textarea>
          <textarea class="chief-reply" id="chief-reply-final" placeholder="在此粘贴 Gemini 终稿原文（执行依据）"></textarea>
        </div>

        <div class="chief-sub" id="chief-step-exec">
          <h4>⑤ 执行跟踪</h4>
          <div class="chief-track">
            <div class="chief-track-left">
              <div class="muted">终稿原文（执行依据）</div>
              <div id="chief-final-render" class="md-body"></div>
            </div>
            <div class="chief-track-right">
              <div class="muted">动作行（调仓组 / 规则组，由 agent 提取或手动维护）</div>
              <div id="chief-actions"></div>
            </div>
          </div>
        </div>

        <div class="chief-history">
          <h4 class="chief-collapse" id="chief-history-toggle">▸ 历史会话</h4>
          <div id="chief-history-list" style="display:none"></div>
        </div>
      </div>
    </div>`;
  const save = document.getElementById("chief-w-save");
  if (save) save.onclick = chiefSaveWeights;
  // 注：原 A2 规则卡的四个绑定（chief-r-save / r-add / r-default / r-new-scope）
  // 已于 2026-10-23 随该卡下线移除，对应元素不再存在，不必保留空绑定。
  const pSave = document.getElementById("chief-pref-save");
  if (pSave) pSave.onclick = chiefSavePref;

  // ---- v2：会话 / 三轮 / 提取 / 历史 ----
  const ns = document.getElementById("chief-new-session");
  if (ns) ns.onclick = chiefNewSession;
  document.querySelectorAll("[data-gen]").forEach(b =>
    b.onclick = () => chiefGenPrompt(b.getAttribute("data-gen")));
  document.querySelectorAll("[data-copy]").forEach(b =>
    b.onclick = () => chiefCopyPrompt(b.getAttribute("data-copy")));
  document.querySelectorAll("[data-save]").forEach(b =>
    b.onclick = () => chiefSaveReply(b.getAttribute("data-save")));
  const ht = document.getElementById("chief-history-toggle");
  if (ht) ht.onclick = chiefToggleHistory;

  chiefLoadGoals();
  chiefLoadNews();
  chiefLoadSessions();
}

// =====================================================================
// v2 主事工作台：会话 / 三轮提示词（B1-B5）/ 动作行标注与写回（C2-C3）/ 历史（D1）
// 注：C1「自动提取动作行」已移除——终稿原文本身即完整反馈，提取改由 agent 负责。
// 平台定位：不做决策、不自动下单；只归集→出提示词→回收原文→跟踪。
// =====================================================================

// ---------- A5：近期政策 / 市场变化 ----------
async function chiefLoadNews() {
  const el = document.getElementById("chief-news");
  if (!el) return;
  try {
    const r = await API("GET", "/api/chief/news");
    const items = (r && r.items) || [];
    if (!items.length) {
      el.innerHTML = '<span class="muted">近期暂无重要度达标的资讯</span>';
      return;
    }
    el.innerHTML = items.map(it => {
      const star = it.related ? "★ " : "";
      const codes = (it.codes && it.codes.length) ? ` · 关联：${it.codes.join("、")}` : "";
      return `<div class="chief-news-item">
        <span class="${it.related ? "chief-news-star" : "muted"}">${star}</span>
        <span class="chief-news-title">${escapeHtml(it.title || "")}</span>
        <span class="muted">（${escapeHtml(it.source || "")} · ${escapeHtml(it.date || "")}）</span>
        ${codes ? `<span class="muted">${codes}</span>` : ""}
      </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<span class="muted">资讯加载失败：${escapeHtml(e.message || String(e))}</span>`;
  }
}

// ---------- 会话管理 ----------
async function chiefNewSession() {
  try {
    const r = await API("POST", "/api/chief/sessions", {});
    chiefState.sessionId = r.id;
    chiefState.sessions = [r].concat(chiefState.sessions);
    chiefState.actions = [];                 // 新会话无动作行
    // 必须一并清空上一期的三轮与输入框：否则旧原文留在框里会被误保存，
    // 且新建的这一期会被进度条误判为「全部已完成」。
    chiefState.turns = {};
    for (const role of CHIEF_ROLES) {
      const p = document.getElementById("chief-prompt-" + role);
      const rp = document.getElementById("chief-reply-" + role);
      if (p) p.value = "";
      if (rp) rp.value = "";
    }
    chiefRenderFinalReply();
    chiefRenderPolicy();                     // 四件套渲染区同步清空
    chiefUpdateSessionBar();
    chiefRenderActions();                    // 渲染空态，避免右栏永久空白
    showToast("已新建一期会话");
  } catch (e) {
    showToast("新建失败：" + (e.message || e));
  }
}

async function chiefLoadSessions() {
  try {
    const r = await API("GET", "/api/chief/sessions");
    chiefState.sessions = (r && r.items) || [];
    if (chiefState.sessions.length) {
      await chiefLoadSessionDetail(chiefState.sessions[0].id);  // 默认载入最新一期
    } else {
      chiefState.sessionId = null;
      chiefUpdateSessionBar();
      chiefRenderPolicy();
      chiefRenderFinalReply();
    }
  } catch (e) { /* 不影响主流程 */ }
}

async function chiefLoadSessionDetail(sid) {
  try {
    const r = await API("GET", "/api/chief/sessions/" + sid);
    chiefState.turns = (r && r.turns) || {};
    chiefState.actions = (r && r.actions) || [];
    for (const role of CHIEF_ROLES) {
      const p = document.getElementById("chief-prompt-" + role);
      const rp = document.getElementById("chief-reply-" + role);
      if (p && chiefState.turns[role]) p.value = chiefState.turns[role].prompt || "";
      if (rp && chiefState.turns[role]) rp.value = chiefState.turns[role].reply || "";
    }
    chiefUpdateSessionBar();
    chiefRenderFinalReply();
    chiefRenderPolicy();
    chiefRenderActions();
  } catch (e) { /* ignore */ }
}

// ---------- 本期工作流进度条（五步，状态由现有数据推导，不新增接口） ----------
// 由来：生产库实证「3 期会话 0 期走完」——最大短板不是功能缺，而是用户看不出下一步该做什么。
// 每步状态只依赖 chiefState.turns / actions / sessionId，故轮次保存后刷新即可，无需后端配合。
const CHIEF_STEPS = [
  { key: "inspect", label: "① 审视仓位与纪律", hint: "Gemini 审盘 + ChatGPT 出四件套" },
  { key: "draft",   label: "② 初稿",     hint: "生成提示词 → Gemini" },
  { key: "review",  label: "③ 审查",     hint: "反向质疑 → ChatGPT" },
  { key: "final",   label: "④ 终稿",     hint: "定稿 → Gemini" },
  { key: "exec",    label: "⑤ 执行跟踪", hint: "动作行 → 下单回填" },
];

// 单轮三态：已存答复=done / 只生成了提示词=doing（等粘贴回来）/ 没动过=todo
function chiefTurnState(role) {
  const t = (chiefState.turns || {})[role];
  const reply = t ? String(t.reply || "").trim() : "";
  const prompt = t ? String(t.prompt || "").trim() : "";
  if (reply) return "done";
  if (prompt) return "doing";
  return "todo";
}

// 多张平行角色合并成一步的状态：任一回填完=done（这一步已有产出）/
// 都没回填但已生成提示词=doing（在等粘贴回来）/ 完全没动=todo。
// 注意：不能用「全部回填才算 done」——① 的两张卡刻意设计成可只做一张
// （行情没变就不必再审仓位），那样会让主动跳过一张的用户永远卡在「进行中」。
// 缺的那张交给「生成下一轮」的软提醒提示，不在进度条上二次惩罚。
function chiefMergeState(roles) {
  const vs = roles.map(chiefTurnState);
  if (vs.some(v => v === "done")) return "done";
  return vs.some(v => v !== "todo") ? "doing" : "todo";
}

function chiefStepStates() {
  const acts = chiefState.actions || [];
  const st = {};
  // ① 由两张平行的卡组成：inspect（Gemini 审仓位）+ ruleinspect（ChatGPT 审纪律→四件套）。
  // 两张都回填了才算 done——只做一半时 ② 就缺一半依据。
  st.inspect = chiefMergeState(["inspect", "ruleinspect"]);
  st.draft = chiefTurnState("draft");
  st.review = chiefTurnState("review");
  st.final = chiefTurnState("final");
  // ⑤ 执行：无动作行=todo；有且全部完成=done；有未完成=doing
  st.exec = !acts.length ? "todo" : (acts.every(a => a.status === "done") ? "done" : "doing");
  return st;
}

function chiefRenderStepper() {
  const box = document.getElementById("chief-steps");
  if (!box) return;
  const st = chiefStepStates();
  // 「当前」= 第一个未完成的步骤；全部完成时不再高亮某一步
  let cur = "";
  for (const s of CHIEF_STEPS) {
    if (st[s.key] !== "done") { cur = s.key; break; }
  }
  box.innerHTML = CHIEF_STEPS.map((s, i) => {
    const v = st[s.key];
    const isCur = s.key === cur;
    // 内容与位置必须分开表达：原先「当前步」一律显示「进行中」，可用户一步未动
    // 看着像已在途。现改为：内容状态看 v，位置用橙色边框 + 「下一步」标签。
    const tag = v === "done" ? "已完成"
      : (v === "doing" ? "进行中" : (isCur ? "下一步" : "待做"));
    return `<div class="chief-step state-${v}${isCur ? " is-cur" : ""}" data-step="${s.key}" title="${escapeHtml(s.hint)} · 点击定位">
      <span class="chief-step-dot">${v === "done" ? "✓" : i + 1}</span>
      <span class="chief-step-body"><b>${s.label}</b><span class="muted">${tag} · ${escapeHtml(s.hint)}</span></span>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-step]").forEach(el =>
    el.onclick = () => chiefGoStep(el.getAttribute("data-step")));
}

function chiefGoStep(key) {
  const el = document.getElementById("chief-step-" + key);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// 顺序软提醒（用户选定不做硬拦截）：明知缺料也给生成，但把代价说清楚。
// 实证：会话 #2 曾跳过②直接存③，悄无声息地让审查失去被审查对象。
// 软提醒的是「上一轮缺料」，链路为 inspect → draft → review → final
const CHIEF_PREV = { draft: [["inspect", "① 仓位审视"], ["ruleinspect", "① 纪律审视（四件套）"]],
                     review: [["draft", "② 初稿"]], final: [["review", "③ 审查"]] };
const CHIEF_ROUND_CN = { inspect: "① 仓位审视", ruleinspect: "① 纪律审视（四件套）",
                         draft: "② 初稿", review: "③ 审查", final: "④ 终稿" };
function chiefWarnPrevRound(round) {
  const miss = (CHIEF_PREV[round] || [])
    .filter(n => chiefTurnState(n[0]) !== "done")
    .map(n => n[1]);
  if (!miss.length) return;
  // 合并成一条：连续弹多个 toast 会互相覆盖，用户只看得到最后一条。缺 ① 那两张卡时最常见。
  showToast(`还没保存 ${miss.join("、")} 的原文，${CHIEF_ROUND_CN[round] || round}会缺少依据（仍为你生成）`);
}

function chiefUpdateSessionBar() {
  const el = document.getElementById("chief-cur-session");
  if (!el) { chiefRenderStepper(); return; }
  const s = chiefState.sessions.find(x => x.id === chiefState.sessionId);
  if (!s) { el.textContent = "本期会话：未创建（生成提示词时自动新建）"; chiefRenderStepper(); return; }
  // 状态原本直接吐英文草稿名（draft/review/final），用户看着像调试信息而非进度 → 转中文
  const cnMap = { inspect: "准备中", draft: "初稿已存", review: "审查完成",
                  final: "终稿已定", executing: "执行中", done: "已归档" };
  el.textContent = `本期会话：#${s.id} ${s.title} · ${cnMap[s.status] || s.status} · 调仓 ${s.rebalance_count} · 规则 ${s.rule_count}`;
  chiefRenderStepper();
}

// ---------- B3/B4/B5：三轮提示词生成 / 复制 / 保存 ----------
async function chiefGenPrompt(round) {
  chiefWarnPrevRound(round);          // 软提醒：缺上一轮原文时提示，但不拦截
  if (!chiefState.sessionId) await chiefNewSession();
  if (!chiefState.sessionId) return;
  const btn = document.querySelector(`[data-gen="${round}"]`);
  if (btn) btn.disabled = true;
  try {
    const r = await API("POST", "/api/chief/prompt",
      { session_id: chiefState.sessionId, round });
    const ta = document.getElementById("chief-prompt-" + round);
    if (ta) ta.value = (r && r.prompt) || "";
    // 同步进 chiefState：进度条靠它判断「已生成 vs 已回填」，不同步则①永远亮不起来
    const prev0 = chiefState.turns[round] || {};
    chiefState.turns[round] = { prompt: (r && r.prompt) || "", reply: prev0.reply || "" };
    chiefUpdateSessionBar();
    showToast(`${CHIEF_ROUND_CN[round] || round}提示词已生成`);
  } catch (e) {
    showToast("生成失败：" + (e.message || e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

function chiefCopyPrompt(round) {
  const ta = document.getElementById("chief-prompt-" + round);
  if (!ta) return;
  const text = ta.value || "";
  if (!text) { showToast("提示词为空"); return; }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text)
      .then(() => showToast("已复制提示词"), () => chiefFallbackCopy(ta));
  } else {
    chiefFallbackCopy(ta);
  }
}

function chiefFallbackCopy(ta) {
  ta.select();
  try { document.execCommand("copy"); showToast("已复制提示词"); } catch (e) { /* noop */ }
}

async function chiefSaveReply(round) {
  if (!chiefState.sessionId) await chiefNewSession();
  if (!chiefState.sessionId) return;
  const promptTa = document.getElementById("chief-prompt-" + round);
  const replyTa = document.getElementById("chief-reply-" + round);
  const reply = replyTa ? replyTa.value : "";
  const promptText = promptTa ? promptTa.value : "";
  const btn = document.querySelector(`[data-save="${round}"]`);
  if (btn) btn.disabled = true;
  try {
    const r = await API("POST", "/api/chief/turns",
      { session_id: chiefState.sessionId, role: round,
        prompt_text: promptText, reply_text: reply });
    if (r && r.status) {
      const s = chiefState.sessions.find(x => x.id === chiefState.sessionId);
      if (s) s.status = r.status;
    }
    chiefState.turns[round] = { prompt: promptText, reply };
    if (round === "final") chiefRenderFinalReply();
    if (round === "ruleinspect") chiefRenderPolicy();
    chiefUpdateSessionBar();
    showToast(`${CHIEF_ROUND_CN[round] || round}答复已保存`);
  } catch (e) {
    showToast("保存失败：" + (e.message || e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

function chiefRenderFinalReply() {
  const el = document.getElementById("chief-final-render");
  if (!el) return;
  const reply = (chiefState.turns.final && chiefState.turns.final.reply) || "";
  el.innerHTML = reply
    ? mdRender(reply)
    : '<span class="muted">尚未保存终稿（在 ④ 粘贴并保存）</span>';
}

function chiefRenderPolicy() {
  const el = document.getElementById("chief-policy-render");
  if (!el) return;
  const reply = (chiefState.turns.ruleinspect && chiefState.turns.ruleinspect.reply) || "";
  el.innerHTML = reply
    ? mdRender(reply)
    : '<span class="muted">尚未保存纲领（在上方粘贴 ChatGPT 的四件套原文）</span>';
}

// ---------- C2/C3：动作行标注 + 规则采纳写回 ----------
function chiefRenderActions() {
  const el = document.getElementById("chief-actions");
  if (!el) return;
  const acts = chiefState.actions || [];
  if (!acts.length) {
    el.innerHTML = '<span class="muted">暂无动作行（调仓依据直接看左侧终稿原文；动作行由 agent 提取或手动维护）</span>';
    return;
  }
  const rb = acts.filter(a => a.kind !== "rule");
  const rl = acts.filter(a => a.kind === "rule");
  const rbHtml = rb.length ? rb.map(chiefActRowHtml).join("") : '<div class="muted">（无调仓动作）</div>';
  const rlHtml = rl.length ? rl.map(chiefActRowHtml).join("") : '<div class="muted">（无规则变更）</div>';
  el.innerHTML =
    `<div class="chief-act-group">调仓组（计划 / 部分 / 已完成 / 取消）</div>${rbHtml}` +
    `<div class="chief-act-group">规则组（采纳 → 立即写回目标配置并留痕）</div>${rlHtml}`;
  el.querySelectorAll("[data-act-id]").forEach(sel =>
    sel.onchange = () => chiefUpdateAction(+sel.getAttribute("data-act-id"),
      sel.value, sel.getAttribute("data-field")));
  el.querySelectorAll(".chief-act-w").forEach(inp =>
    inp.onchange = () => chiefUpdateAction(+inp.getAttribute("data-act-id"),
      inp.value, inp.getAttribute("data-field")));
}

function chiefActRowHtml(a) {
  const isRule = a.kind === "rule";
  const opts = isRule
    ? [["adopted", "采纳"], ["rejected", "未采纳"]]
    : [["planned", "计划"], ["partial", "部分"], ["done", "已完成"], ["canceled", "取消"]];
  const sel = `<select class="chief-act-status" data-act-id="${a.id}" data-field="status">` +
    opts.map(o => `<option value="${o[0]}"${a.status === o[0] ? " selected" : ""}>${o[1]}</option>`).join("") +
    `</select>`;
  const weight = isRule
    ? `新值 <input class="chief-act-w" data-act-id="${a.id}" data-field="new_value" value="${escapeHtml(a.new_value || "")}" size="6">%`
    : `目标 <input class="chief-act-w" data-act-id="${a.id}" data-field="target_weight" value="${escapeHtml(a.target_weight != null ? a.target_weight : "")}" size="6">%`;
  return `<div class="chief-act-row">
    <span class="chief-act-action">${escapeHtml(a.action || "")}</span>
    <span class="chief-act-target">${escapeHtml(a.target || "")}</span>
    ${weight} ${sel}
  </div>`;
}

async function chiefUpdateAction(id, val, field) {
  try {
    await API("PATCH", "/api/chief/actions/" + id, { [field]: val });
    await chiefLoadSessionDetail(chiefState.sessionId);  // 刷新（规则采纳会写回 chief_goals）
    if (field === "status" && val === "adopted") showToast("规则已采纳并写回目标配置");
    else showToast("已更新");
  } catch (e) {
    showToast("更新失败：" + (e.message || e));
  }
}

// ---------- D1：历史会话 ----------
function chiefToggleHistory() {
  const list = document.getElementById("chief-history-list");
  const tog = document.getElementById("chief-history-toggle");
  if (!list) return;
  const open = list.style.display !== "none";
  list.style.display = open ? "none" : "";
  if (tog) tog.textContent = (open ? "▸" : "▾") + " 历史会话";
  if (!open) chiefRenderHistory();
}

function chiefRenderHistory() {
  const list = document.getElementById("chief-history-list");
  if (!list) return;
  const sess = chiefState.sessions || [];
  if (!sess.length) { list.innerHTML = '<div class="muted">暂无历史会话</div>'; return; }
  list.innerHTML = sess.map(s => `
    <div class="chief-hist-item" data-sid="${s.id}">
      <span class="chief-hist-title">#${s.id} ${escapeHtml(s.title || "")}</span>
      <span class="muted">${(s.created_at || "").slice(0, 10)} · ${s.status} · 调仓 ${s.rebalance_count} · 规则 ${s.rule_count}</span>
      <button class="btn-mini chief-minor" data-open="${s.id}" type="button">载入</button>
    </div>`).join("");
  list.querySelectorAll("[data-open]").forEach(b =>
    b.onclick = () => {
      const sid = +b.getAttribute("data-open");
      chiefState.sessionId = sid;
      chiefLoadSessionDetail(sid);
    });
}
