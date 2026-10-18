// 04-about-users —— 首页返回 / 关于页 / 管理员设置
// 左上角标题：返回默认落地页（登录 → 圆桌；游客 → 资讯）
function goHome() {
  showView(navDefault());
}
document.getElementById("brand-link").addEventListener("click", goHome);

// ===== 关于：项目框架与设计（右侧页面，非弹窗） =====
function renderAbout() {
  document.getElementById("main").innerHTML = `
    <div class="about-page">
      <h1>关于 · 慢量化</h1>
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
        <li>普通用户仅能访问自己的项目；管理员额外拥有「设置」页面，可处理用户的“忘记密码”申请并重置为随机密码。</li>
      </ul>

      <p class="about-foot">版本 v9 · 仅本地/自部署用途 · <a href="https://github.com/tojoevan/QuantMindLite" target="_blank" rel="noopener">GitHub 仓库</a></p>
    </div>`;
}
// 页级导航（圆桌/论坛/交易/设置/关于）已统一收口到 06-pages.js 的 bindNav()；
// 此处仅保留非页级入口（主题切换）。
document.getElementById("btn-theme").onclick = toggleTheme;

// ===== 管理员：设置（当前含用户管理分区；后续全局配置项作为新分区平级加入）=====
async function renderUsers(flash) {
  if (!(me && me.is_admin)) { showAuth("login"); return; }
  document.getElementById("main").innerHTML = `
    <div class="page">
      <div class="panel">
        <h3>设置 <span class="muted">仅管理员可见</span></h3>
        <div id="users-content"><span class="muted">加载中…</span></div>
      </div>
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
      <h4>用户管理 · 待重置申请</h4>
      <div class="user-list">${reqHtml}</div>
      <h4>用户管理 · 全部用户</h4>
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
