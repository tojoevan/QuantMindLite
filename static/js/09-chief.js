// 09-chief —— 主事工作台（v2：信息中枢 + 执行跟踪器，不做决策 / 不自动下单）
// 设计来源：docs/v2-主事工作台设计方案.md §12 / §13；步骤见 docs/v2-开发步骤拆分.md。
//
// 已落地：
//   S1  chief_* 四张新表（db.py）
//   S2  页面骨架与入口（#/chief）
//   A1  目标权重（**按标的**）+ 现状对照（当前占比 / 领域 / 偏离）
//   A2  纪律规则（结构化条目：作用范围 + 比较符 + 阈值）+ 逐条校验（红=违反 / 黄=接近 / 绿=达标）
//   A3  风险偏好 / 约束（自由文本偏好 + 允许换仓开关，存 kind=preference）
//
// 后续卡片（A4 政策变化 / B 三轮提示词 / C 标注跟踪 / D 历史）
// 逐步填充到 #chief-cards 内。样式复用现有 .panel / .table / .cat-tag / .btn-mini。
//
// 纪律规则形态说明（用户决策）：只收**结构化条目**——平台要能自己算出「当前有没有违规」，
// 也要能把规则原样写进主事提示词；自由文本类约束留给 A3 的风险偏好。
// 默认 2 条：任一单一标的 ≤ 15%、任一单一行业 ≤ 40%（首次进入自动铺好，不落库，改后保存才生效）。

const CHIEF_DEFAULT_RULES = [
  { scope: "any_pos", op: "le", value: 15 },
  { scope: "any_cat", op: "le", value: 40 },
];

// 作用范围文案：any_pos / any_cat / pos:<id> / cat:<名称>
function chiefScopeLabel(scope) {
  const s = String(scope || "any_pos");
  const i = s.indexOf(":");
  const k = i < 0 ? s : s.slice(0, i);
  const arg = i < 0 ? "" : s.slice(i + 1);
  if (k === "any_pos") return "任一单一标的";
  if (k === "any_cat") return "任一单一行业";
  if (k === "pos") {
    const p = chiefState.positions.find(x => String(x.id) === arg);
    return "标的 · " + (p ? (p.name || arg) : (arg || "—"));
  }
  if (k === "cat") return "行业 · " + (arg || "—");
  return s;
}

function chiefScopeKey(scope) { return String(scope || "").split(":")[0]; }
function chiefScopeArg(scope) {
  const s = String(scope || ""); const i = s.indexOf(":");
  return i < 0 ? "" : s.slice(i + 1);
}

const chiefState = {
  positions: [],
  weights: {},                 // project_id -> 目标%
  rules: [],                   // {scope, op, value}
  checks: [],                  // 最近的逐条校验结果
  breaks: { positions: [], categories: [] },
  fromDefault: false,          // 当前展示的是否为「默认 2 条（未保存）」
  pref: { note: "", allow_rebalance: false },  // A3 风险偏好 / 约束
  // ---- v2 主事工作台：会话 / 三轮 / 动作行 / 历史（B·C·D 组）----
  sessionId: null,             // 当前一期会话 id
  sessions: [],                // 历史会话列表（倒序）
  turns: { draft: null, review: null, final: null },  // {prompt, reply}
  actions: [],                 // 已保存动作行（由 agent 提取或手动维护，前端不再自动提取）
};

function chiefFmtPct(v) { return v == null ? "—" : v.toFixed(1) + "%"; }

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
    const tgt = chiefState.weights[p.id];
    const has = typeof tgt === "number" && tgt > 0;
    const dev = has ? cur - tgt : null;
    const devCls = dev == null ? "muted"
      : (Math.abs(dev) < 0.05 ? "muted" : (dev > 0 ? "pos" : "neg"));
    const brk = chiefState.breaks.positions.indexOf(p.id) >= 0;
    const catBrk = !!(p.category && chiefState.breaks.categories.indexOf(p.category) >= 0);
    return `
      <tr class="${brk ? "chief-row-break" : ""}" data-pid="${p.id}" data-cat="${escapeHtml(p.category || "")}">
        <td><b>${escapeHtml(p.name || "")}</b>${p.code ? ` <span class="muted">${escapeHtml(p.code)}</span>` : ""}</td>
        <td>${p.category ? `<span class="cat-tag${catBrk ? " break" : ""}">${escapeHtml(p.category)}</span>` : '<span class="muted">—</span>'}</td>
        <td class="${brk ? "pos" : ""}"${brk ? ' title="该标的占比已违反纪律规则"' : ""}>${chiefFmtPct(cur)}</td>
        <td><input class="chief-w-input" type="number" min="0" max="100" step="0.5"
             value="${has ? tgt : ""}" placeholder="—" data-pid="${p.id}"
             title="该标的的目标占比（%）；留空表示未配置"></td>
        <td class="chief-dev ${devCls}">${dev == null ? "—" : (dev > 0 ? "+" : "") + dev.toFixed(1) + "%"}</td>
      </tr>`;
  }).join("");
  tb.querySelectorAll("input[data-pid]").forEach(inp => {
    inp.oninput = () => chiefRefreshDev(inp);
  });
  chiefRefreshSum();
  chiefRenderConcentration();
}

// 输入目标值时，实时重算该行偏离与底部合计（不请求后端）
function chiefRefreshDev(inp) {
  const tr = inp.closest("tr");
  if (!tr) return;
  const pid = parseInt(inp.getAttribute("data-pid"), 10);
  const p = chiefState.positions.find(x => x.id === pid);
  const td = tr.querySelector("td.chief-dev");
  if (!td || !p) return;
  const v = parseFloat(inp.value);
  const dev = (isNaN(v) || v <= 0) ? null : chiefCurPct(p) - v;
  td.className = "chief-dev " + (dev == null ? "muted"
    : (Math.abs(dev) < 0.05 ? "muted" : (dev > 0 ? "pos" : "neg")));
  td.textContent = dev == null ? "—" : (dev > 0 ? "+" : "") + dev.toFixed(1) + "%";
  chiefRefreshSum();
}

function chiefRefreshSum() {
  const el = document.getElementById("chief-w-sum");
  if (!el) return;
  let sum = 0, n = 0;
  document.querySelectorAll("#chief-weights-body input[data-pid]").forEach(inp => {
    const v = parseFloat(inp.value);
    if (!isNaN(v) && v > 0) { sum += v; n++; }
  });
  el.textContent = n ? sum.toFixed(1) + "%" : "—";
  el.className = (n && sum > 100.05) ? "pos" : "";
}

// Top3 集中度：按市值取前三，显示合计占比 + 各自百分比。
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

// 全量覆盖保存（后端按当前用户全量替换 kind=weight 的行）
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

// ---------- A2：纪律规则 ----------
// **存与算分离**：规则存在 chief_goals，校验走 POST /api/chief/rules/check（不落库）。
// 于是「默认 2 条」「刚改阈值还没保存」也能立刻看到红黄绿 —— 阈值改完 400ms 自动重算；
// 若只有保存后才有状态，纪律规则最核心的即时反馈就没了。
function chiefFindCheck(r) {
  return (chiefState.checks || []).find(c =>
    c.scope === r.scope && c.op === r.op && Math.abs((c.value || 0) - (r.value || 0)) < 0.005);
}

function chiefCurHtml(ck) {
  if (!ck) return "—";
  const hits = (ck.hits && ck.hits.length)
    ? ck.hits.map(h => h.name || ("#" + h.id)).join("、") : "";
  return chiefFmtPct(ck.current) + (hits ? ` <span class="muted">${escapeHtml(hits)}</span>` : "");
}

function chiefBadgeHtml(ck) {
  if (!ck) return '<span class="badge plain">未设阈值</span>';
  if (ck.status === "break") return '<span class="badge danger">违反</span>';
  if (ck.status === "warn") return '<span class="badge warn">接近</span>';
  if (ck.status === "ok") return '<span class="badge ok">达标</span>';
  return '<span class="badge plain">—</span>';
}

// 违反同步到目标权重表：整行左侧红条 + 占比红字 + 行业标签描红
function chiefSyncWeightBreaks() {
  const bp = chiefState.breaks.positions || [], bc = chiefState.breaks.categories || [];
  document.querySelectorAll("#chief-weights-body tr[data-pid]").forEach(tr => {
    const pid = parseInt(tr.getAttribute("data-pid"), 10);
    const cat = tr.getAttribute("data-cat") || "";
    const brk = bp.indexOf(pid) >= 0;
    tr.classList.toggle("chief-row-break", brk);
    const pctTd = tr.children[2];
    if (pctTd) pctTd.classList.toggle("pos", brk);
    const tag = tr.querySelector(".cat-tag");
    if (tag) tag.classList.toggle("break", !!(cat && bc.indexOf(cat) >= 0));
  });
}

// 只刷新状态相关的 UI（当前值 / 徽章 / 汇总 / 权重表红条），不重建表格 —— 免得输入时丢焦点
function chiefSyncChecksUI() {
  const tb = document.getElementById("chief-rules-body");
  if (tb) {
    [...tb.querySelectorAll("tr")].forEach((tr, i) => {
      const r = chiefState.rules[i];
      if (!r) return;
      const ck = chiefFindCheck(r);
      const curTd = tr.querySelector(".chief-r-cur");
      const badgeTd = tr.querySelector(".chief-r-badge");
      if (curTd) curTd.innerHTML = chiefCurHtml(ck);
      if (badgeTd) badgeTd.innerHTML = chiefBadgeHtml(ck);
    });
  }
  chiefRenderRuleSummary();
  chiefSyncWeightBreaks();
}

// 把当前页面上的规则（含未保存的）送后端算一次
let chiefRecheckTimer = null;
function chiefRecheckSoon() {
  if (chiefRecheckTimer) clearTimeout(chiefRecheckTimer);
  chiefRecheckTimer = setTimeout(() => { chiefRecheckTimer = null; chiefRecheck(true); }, 400);
}

async function chiefRecheck(doSync) {
  const items = chiefState.rules.filter(r => r.value > 0)
    .map(r => ({ scope: r.scope, op: r.op, value: r.value }));
  try {
    const r = await API("POST", "/api/chief/rules/check", { items });
    chiefState.checks = (r && r.items) || [];
    chiefState.breaks = (r && r.breaks) || { positions: [], categories: [] };
  } catch (e) {
    chiefState.checks = [];
    chiefState.breaks = { positions: [], categories: [] };
    return;
  }
  if (doSync) chiefSyncChecksUI();
}

function chiefRenderRules() {
  const tb = document.getElementById("chief-rules-body");
  if (!tb) return;
  if (!chiefState.rules.length) {
    tb.innerHTML = `<tr><td colspan="6" class="muted">暂无规则，可在下方添加，或点「恢复默认 2 条」</td></tr>`;
  } else {
    tb.innerHTML = chiefState.rules.map((r, i) => {
      const ck = chiefFindCheck(r);
      const badge = chiefBadgeHtml(ck);
      return `
        <tr>
          <td>${escapeHtml(chiefScopeLabel(r.scope))}</td>
          <td><select class="chief-r-op" data-ri="${i}" data-f="op">
            <option value="le"${r.op !== "ge" ? " selected" : ""}>≤</option>
            <option value="ge"${r.op === "ge" ? " selected" : ""}>≥</option>
          </select></td>
          <td><input class="chief-w-input" type="number" min="0" max="100" step="0.5"
               value="${r.value > 0 ? r.value : ""}" placeholder="—"
               data-ri="${i}" data-f="value" title="阈值（占总市值 %）"></td>
          <td class="chief-r-cur">${chiefCurHtml(ck)}</td>
          <td class="chief-r-badge">${badge}</td>
          <td><button class="btn-mini chief-minor" type="button" data-rm="${i}"
               title="移除该规则（保存后生效）">移除</button></td>
        </tr>`;
    }).join("");
    tb.querySelectorAll("select[data-f='op']").forEach(sel => {
      sel.onchange = () => {
        const i = parseInt(sel.getAttribute("data-ri"), 10);
        if (chiefState.rules[i]) { chiefState.rules[i].op = sel.value; chiefRenderRules(); chiefRecheck(true); }
      };
    });
    tb.querySelectorAll("input[data-f='value']").forEach(inp => {
      inp.oninput = () => {
        const i = parseInt(inp.getAttribute("data-ri"), 10);
        const v = parseFloat(inp.value);
        if (chiefState.rules[i]) {
          chiefState.rules[i].value = (isNaN(v) || v <= 0) ? 0 : v;
          chiefRecheckSoon();   // 阈值一改就重算，不等保存
        }
      };
    });
    tb.querySelectorAll("button[data-rm]").forEach(btn => {
      btn.onclick = () => {
        const i = parseInt(btn.getAttribute("data-rm"), 10);
        chiefState.rules.splice(i, 1);
        chiefRenderRules();
        chiefRecheck(true);
      };
    });
  }
  chiefRenderRuleSummary();
}

function chiefRenderRuleSummary() {
  const sum = document.getElementById("chief-r-sum");
  if (!sum) return;
  const s = (chiefState.checks || []).reduce((a, c) => {
    a[c.status] = (a[c.status] || 0) + 1; return a;
  }, {});
  const parts = [];
  if (s.break) parts.push(`<span class="pos">违反 ${s.break}</span>`);
  if (s.warn) parts.push(`<span class="chief-warn-text">接近 ${s.warn}</span>`);
  if (s.ok) parts.push(`<span class="neg">达标 ${s.ok}</span>`);
  sum.innerHTML = parts.length ? " · " + parts.join(" · ") : "";
}

// 类型切换：只有「指定标的 / 指定行业」需要选目标
function chiefSyncRuleTarget() {
  const scopeSel = document.getElementById("chief-r-new-scope");
  const tgt = document.getElementById("chief-r-new-target");
  if (!scopeSel || !tgt) return;
  const k = chiefScopeKey(scopeSel.value);
  const need = (k === "pos" || k === "cat");
  tgt.style.display = need ? "" : "none";
  if (!need) { tgt.innerHTML = ""; return; }
  if (k === "pos") {
    const pos = [...chiefState.positions].sort(
      (a, b) => (b.market_value || 0) - (a.market_value || 0));
    tgt.innerHTML = pos.map(p =>
      `<option value="pos:${p.id}">${escapeHtml(p.name || "#" + p.id)}</option>`).join("");
  } else {
    const cats = [];
    chiefState.positions.forEach(p => {
      const c = (p.category || "").trim();
      if (c && cats.indexOf(c) < 0) cats.push(c);
    });
    tgt.innerHTML = cats.length
      ? cats.map(c => `<option value="cat:${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("")
      : `<option value="">（无已分类标的）</option>`;
  }
}

function chiefAddRule() {
  const scopeSel = document.getElementById("chief-r-new-scope");
  const tgt = document.getElementById("chief-r-new-target");
  if (!scopeSel) return;
  const k = chiefScopeKey(scopeSel.value);
  const scope = (k === "pos" || k === "cat") ? (tgt && tgt.value ? tgt.value : "") : scopeSel.value;
  if (!scope) { if (typeof showToast === "function") showToast("请先选择标的或行业"); return; }
  const dup = chiefState.rules.some(r => r.scope === scope);
  if (dup) { if (typeof showToast === "function") showToast("该规则已存在"); return; }
  chiefState.rules.push({ scope, op: "le", value: (scope === "any_pos" ? 15 : 40) });
  chiefRenderRules();
  chiefRecheck(true);
  const hint = document.getElementById("chief-r-hint");
  if (hint) hint.textContent = "已在本地新增，填好阈值后点「保存规则」才生效";
}

// 全量覆盖保存（后端按当前用户整体替换 kind=rule 的行）
async function chiefSaveRules() {
  const btn = document.getElementById("chief-r-save");
  const hint = document.getElementById("chief-r-hint");
  const items = chiefState.rules
    .filter(r => r.value > 0)
    .map(r => ({ scope: r.scope, op: r.op, value: r.value }));
  if (btn) btn.disabled = true;
  try {
    const r = await API("PUT", "/api/chief/goals/rule", { items });
    chiefState.fromDefault = false;
    await chiefRecheck(false);
    chiefRenderRules();
    chiefRenderWeights();
    if (hint) hint.textContent = `已保存 ${r && r.saved ? r.saved : 0} 条纪律规则`;
    if (typeof showToast === "function") showToast("纪律规则已保存");
  } catch (e) {
    if (hint) hint.textContent = "保存失败：" + (e.message || String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

function chiefRestoreDefaultRules() {
  chiefState.rules = CHIEF_DEFAULT_RULES.map(r => ({ scope: r.scope, op: r.op, value: r.value }));
  chiefState.fromDefault = true;
  chiefRenderRules();
  chiefRecheck(true);
  const hint = document.getElementById("chief-r-hint");
  if (hint) hint.textContent = "已填入默认 2 条（单一标的 ≤15% / 单一行业 ≤40%），点「保存规则」生效";
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
    const [ov, gs, rs, ps] = await Promise.all([
      API("GET", "/api/overview"),
      API("GET", "/api/chief/goals?kind=weight"),
      API("GET", "/api/chief/goals?kind=rule"),
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
    const saved = ((rs && rs.items) || []).map(g => ({
      scope: (g.key || "any_pos").trim(),
      op: (g.op || "le").trim(),
      value: parseFloat(g.value) || 0,
    })).filter(r => r.value > 0);
    chiefState.rules = saved;
    const prefs = ((ps && ps.items) || []);
    chiefState.pref = {
      note: (prefs.find(x => x.key === "note") || {}).value || "",
      allow_rebalance: ((prefs.find(x => x.key === "allow_rebalance") || {}).value || "0") === "1",
    };
    chiefState.fromDefault = false;
    // 把已加载的风险偏好同步进 DOM（卡片在 renderChief 时按初始空值渲染，
    // 之后异步拉到的偏好不会自动回填，必须手动同步，否则刷新后不显示已存内容）
    const ta = document.getElementById("chief-pref-note");
    if (ta) ta.value = chiefState.pref.note || "";
    const sw = document.getElementById("chief-pref-rebalance");
    if (sw) sw.checked = !!chiefState.pref.allow_rebalance;
    if (!saved.length) {   // 首次进入：铺默认 2 条，但不落库
      chiefState.rules = CHIEF_DEFAULT_RULES.map(r => ({ scope: r.scope, op: r.op, value: r.value }));
      chiefState.fromDefault = true;
    }
    await chiefRecheck(false);
    chiefRenderWeights();
    chiefRenderRules();
    chiefSyncRuleTarget();
    const rh = document.getElementById("chief-r-hint");
    if (rh) {
      rh.textContent = chiefState.fromDefault
        ? "已铺默认 2 条（未保存）：单一标的 ≤15%、单一行业 ≤40%，可改后保存"
        : "";
    }
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
        <h3>① 目标与规则审视 <span class="muted">目标与纪律都在平台侧配置，主事只给建议、由你终裁</span></h3>
        <div class="chief-sessionbar">
          <span id="chief-cur-session" class="muted">本期会话：未创建（生成提示词时自动新建）</span>
          <button class="btn-mini chief-minor" id="chief-new-session" type="button">新建一期</button>
        </div>
        <div id="chief-cards">
          <div class="chief-sub">
            <h4>目标权重（按标的） <span class="muted">让主事看清每笔钱投的领域</span></h4>
            <div class="table-wrap">
              <table class="table" id="chief-weights">
                <thead><tr>
                  <th>标的</th>
                  <th>领域</th>
                  <th>当前占比</th>
                  <th>目标权重 %</th>
                  <th title="当前占比 − 目标权重；红=超配（可减），绿=低配（可加）">偏离</th>
                </tr></thead>
                <tbody id="chief-weights-body">
                  <tr><td colspan="5" class="muted">加载中…</td></tr>
                </tbody>
                <tfoot><tr>
                  <td colspan="3" class="muted">目标合计</td>
                  <td id="chief-w-sum">—</td>
                  <td></td>
                </tr></tfoot>
              </table>
            </div>
            <div class="chief-conc" id="chief-conc"></div>
            <div class="chief-actions">
              <button class="btn-mini" id="chief-w-save" type="button">保存目标权重</button>
              <span class="muted" id="chief-w-hint"></span>
            </div>
          </div>

          <div class="chief-sub">
            <h4>纪律规则 <span class="muted">结构化条目 · 红=违反 / 黄=接近 / 绿=达标</span><span id="chief-r-sum"></span></h4>
            <div class="table-wrap">
              <table class="table" id="chief-rules">
                <thead><tr>
                  <th>规则</th>
                  <th>条件</th>
                  <th>阈值 %</th>
                  <th title="取最极端的那个标的 / 行业来判；只要有一个超了就是违反">当前</th>
                  <th>状态</th>
                  <th></th>
                </tr></thead>
                <tbody id="chief-rules-body">
                  <tr><td colspan="6" class="muted">加载中…</td></tr>
                </tbody>
              </table>
            </div>
            <div class="chief-new-row">
              <select id="chief-r-new-scope" class="chief-select">
                <option value="any_pos">任一单一标的</option>
                <option value="any_cat">任一单一行业</option>
                <option value="pos">指定标的</option>
                <option value="cat">指定行业</option>
              </select>
              <select id="chief-r-new-target" class="chief-select" style="display:none"></select>
              <button class="btn-mini chief-minor" id="chief-r-add" type="button">添加规则</button>
            </div>
            <div class="chief-actions">
              <button class="btn-mini" id="chief-r-save" type="button">保存规则</button>
              <button class="btn-mini chief-minor" id="chief-r-default" type="button">恢复默认 2 条</button>
              <span class="muted" id="chief-r-hint"></span>
            </div>
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
  const rSave = document.getElementById("chief-r-save");
  if (rSave) rSave.onclick = chiefSaveRules;
  const rAdd = document.getElementById("chief-r-add");
  if (rAdd) rAdd.onclick = chiefAddRule;
  const rDef = document.getElementById("chief-r-default");
  if (rDef) rDef.onclick = chiefRestoreDefaultRules;
  const rScope = document.getElementById("chief-r-new-scope");
  if (rScope) rScope.onchange = chiefSyncRuleTarget;
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
    chiefState.turns = { draft: null, review: null, final: null };
    for (const role of ["draft", "review", "final"]) {
      const p = document.getElementById("chief-prompt-" + role);
      const rp = document.getElementById("chief-reply-" + role);
      if (p) p.value = "";
      if (rp) rp.value = "";
    }
    chiefRenderFinalReply();
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
    }
  } catch (e) { /* 不影响主流程 */ }
}

async function chiefLoadSessionDetail(sid) {
  try {
    const r = await API("GET", "/api/chief/sessions/" + sid);
    chiefState.turns = (r && r.turns) || {};
    chiefState.actions = (r && r.actions) || [];
    for (const role of ["draft", "review", "final"]) {
      const p = document.getElementById("chief-prompt-" + role);
      const rp = document.getElementById("chief-reply-" + role);
      if (p && chiefState.turns[role]) p.value = chiefState.turns[role].prompt || "";
      if (rp && chiefState.turns[role]) rp.value = chiefState.turns[role].reply || "";
    }
    chiefUpdateSessionBar();
    chiefRenderFinalReply();
    chiefRenderActions();
  } catch (e) { /* ignore */ }
}

// ---------- 本期工作流进度条（五步，状态由现有数据推导，不新增接口） ----------
// 由来：生产库实证「3 期会话 0 期走完」——最大短板不是功能缺，而是用户看不出下一步该做什么。
// 每步状态只依赖 chiefState.turns / actions / sessionId，故轮次保存后刷新即可，无需后端配合。
const CHIEF_STEPS = [
  { key: "inspect", label: "① 审视目标", hint: "目标权重 / 纪律 / 偏好" },
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

function chiefStepStates() {
  const acts = chiefState.actions || [];
  const st = {};
  // ① 审视：本身无「保存」动作，以「已装配初稿提示词」视为审视完成（配置已进入上下文头）。
  const dr = chiefTurnState("draft");
  st.inspect = dr === "todo" ? (chiefState.sessionId ? "doing" : "todo") : "done";
  st.draft = dr;
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
    const cls = v === "done" ? "done" : (s.key === cur ? "doing" : "todo");
    const tag = v === "done" ? "已完成" : (s.key === cur ? "进行中" : "待做");
    return `<div class="chief-step ${cls}" data-step="${s.key}" title="${escapeHtml(s.hint)} · 点击定位">
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
const CHIEF_PREV = { review: ["draft", "② 初稿"], final: ["review", "③ 审查"] };
function chiefWarnPrevRound(round) {
  const need = CHIEF_PREV[round];
  if (!need) return;
  const prev = need[0], label = need[1];
  if (chiefTurnState(prev) !== "done") {
    showToast(`还没保存${label}原文，${round === "review" ? "③ 审查" : "④ 终稿"}会缺少依据（仍为你生成）`);
  }
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
    showToast(`提示词已生成（${round}）`);
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
    chiefUpdateSessionBar();
    showToast(round + " 答复已保存");
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
