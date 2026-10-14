// 03-auth —— 登录门禁 / 多用户 / 游客模式 / 抽屉菜单 / 启动
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
  // 只刷新项目列表，落地视图交给统一路由（登录用户默认落地「圆桌」= 讨论优先）
  loadProjects(false).then(() => showView(navDefault()));
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
  // 圆桌入口对游客保留可见：点击会落到登录门禁，作为登录引导
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
    showView("dashboard");   // 游客落地「交易仪表台」（只读）
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
