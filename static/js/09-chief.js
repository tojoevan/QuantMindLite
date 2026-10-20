// 09-chief —— 主事工作台（v2：信息中枢 + 执行跟踪器，不做决策 / 不自动下单）
// 设计来源：docs/v2-主事工作台设计方案.md §12 / §13；步骤见 docs/v2-开发步骤拆分.md。
//
// 已落地：
//   S1  chief_* 四张新表（db.py）
//   S2  页面骨架与入口（#/chief）
//   A1  目标权重（**按标的**）+ 现状对照（当前占比 / 领域 / 偏离）
//
// 后续卡片（A2 纪律规则 / A3 风险偏好 / A4 政策变化 / B 三轮提示词 / C 标注跟踪 / D 历史）
// 逐步填充到 #chief-cards 内。样式复用现有 .panel / .table / .cat-tag / .btn-mini。

const chiefState = { positions: [], weights: {} };   // weights: project_id -> 目标%

function chiefFmtPct(v) { return v == null ? "—" : v.toFixed(1) + "%"; }

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
    return `
      <tr>
        <td><b>${escapeHtml(p.name || "")}</b>${p.code ? ` <span class="muted">${escapeHtml(p.code)}</span>` : ""}</td>
        <td>${p.category ? `<span class="cat-tag">${escapeHtml(p.category)}</span>` : '<span class="muted">—</span>'}</td>
        <td>${chiefFmtPct(cur)}</td>
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

// 载入持仓 + 已存目标权重
async function chiefLoadGoals() {
  const tb = document.getElementById("chief-weights-body");
  const hint = document.getElementById("chief-w-hint");
  try {
    const [ov, gs] = await Promise.all([
      API("GET", "/api/overview"),
      API("GET", "/api/chief/goals?kind=weight"),
    ]);
    chiefState.positions = (ov && ov.positions) || [];
    const w = {};
    ((gs && gs.items) || []).forEach(g => {
      const pid = parseInt(String(g.key || "").replace(/^p:/, ""), 10);
      const v = parseFloat(g.value);
      if (pid && !isNaN(v) && v > 0) w[pid] = v;
    });
    chiefState.weights = w;
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
      <div class="panel">
        <h3>目标与规则审视 <span class="muted">目标权重按标的配置 · 让主事看清每笔钱投的领域</span></h3>
        <div id="chief-cards">
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
          <div class="chief-actions">
            <button class="btn-mini" id="chief-w-save" type="button">保存目标权重</button>
            <span class="muted" id="chief-w-hint"></span>
          </div>
        </div>
      </div>
    </div>`;
  const save = document.getElementById("chief-w-save");
  if (save) save.onclick = chiefSaveWeights;
  chiefLoadGoals();
}
