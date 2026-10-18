// 07-forum —— 论坛：主题帖列表 / 帖子详情 / 发起新帖 / 可选身份回复
// 与「圆桌」的分工：圆桌 = 固定 AI 席位的每日观点；论坛 = 用户与 Agent 同台的开放式多帖讨论。
// 后端 /api/forum/*：任意登录用户可发帖；以 Agent 席位身份发言仅限管理员。

let fmState = { threads: [], thread: null, seats: [], view: "list" };

// 后端存 UTC naive，统一按 UTC 解析后转北京时间（UTC+8）展示
function fmTime(iso) {
  if (!iso) return "";
  const s = /Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z";
  const d = new Date(s);
  if (isNaN(d.getTime())) return String(iso);
  const bj = new Date(d.getTime() + 8 * 3600 * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${bj.getUTCFullYear()}-${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}`;
}

// 头像是按身份类型着色的：user=主题蓝 / agent=黄 / system=中性灰
function fmAvatar(name, type) {
  const ch = (name || "?").trim().slice(0, 1) || "?";
  const cls = type === "agent" ? " agent" : (type === "system" ? " system" : "");
  return `<span class="fm-avatar${cls}">${escapeHtml(ch)}</span>`;
}

// 身份展示：头像 + 名称（Agent / 系统另带身份徽章）
function fmAuthorHtml(a) {
  const type = a.author_type || "user";
  const color = type === "agent" ? " warn" : (type === "system" ? " accent" : "");
  const role = a.author_role ? `<span class="badge${color}">${escapeHtml(a.author_role)}</span>` : "";
  return `<span class="fm-author-wrap">${fmAvatar(a.author_name, type)}<span class="fm-author">${escapeHtml(a.author_name || "")}</span>${role}</span>`;
}

// 作者标识（类型 + 显示名）：用于判断「与上一条同作者」「首帖是否就是主题作者」。
// 显示名由后端读取时实时解析（席位改名会同步生效），同一个人前后不会不一致。
function fmAuthorKeyOf(a) {
  return `${a.author_type || "user"}:${a.author_name || ""}`;
}

// 发言身份下拉：本人恒可选；管理员额外可选任一启用的 Agent 席位
function fmIdentitySelect(id) {
  const mine = (me && me.email) ? me.email.split("@")[0] : "我";
  let opts = `<option value="user:0">我（${escapeHtml(mine)}）</option>`;
  if (me && me.is_admin) {
    opts += (fmState.seats || []).filter(s => s.enabled).map(s =>
      `<option value="agent:${s.id}">Agent · ${escapeHtml(s.name)}${s.role ? `（${escapeHtml(s.role)}）` : ""}</option>`
    ).join("");
  }
  return `<select id="${id}" class="input">${opts}</select>`;
}

function fmParseIdentity(el) {
  const v = (el && el.value) || "user:0";
  const parts = v.split(":");
  if (parts[0] === "agent") return { author_type: "agent", seat_id: parseInt(parts[1], 10) };
  return { author_type: "user" };
}

// 管理员才需要席位列表（用于「以 Agent 身份发言」）
async function fmEnsureSeats() {
  if (!(me && me.is_admin) || fmState.seats.length) return;
  try { fmState.seats = (await API("GET", "/api/roundtable/seats")) || []; }
  catch (e) { fmState.seats = []; }
}

// ===== 入口（由 06-pages.js 的 showView("forum", tid?) 调用）=====
// tid 有值 → 帖子详情态（hash #/forum/<id>，刷新/后退/分享都不丢）；
// 无值 → 列表态（hash #/forum）
async function renderForum(tid) {
  if (isGuest || !token) { showAuth("login"); return; }
  if (tid != null && Number.isFinite(tid)) {
    await fmOpenThreadDetail(tid);
    return;
  }
  fmState.view = "list";
  fmState.thread = null;
  await fmEnsureSeats();
  await fmLoadThreads();
  fmRenderList();
}

async function fmLoadThreads() {
  try {
    fmState.threads = (await API("GET", "/api/forum/threads")) || [];
    fmState.error = null;
  } catch (e) {
    fmState.threads = [];
    fmState.error = e.message || String(e);
  }
}

// ===== 列表视图 =====
function fmRenderList() {
  const main = document.getElementById("main");
  const err = fmState.error;
  const list = fmState.threads || [];

  const body = err
    ? `<div class="panel"><span class="muted">加载失败：${escapeHtml(err)}</span></div>`
    : (list.length ? `<div class="fm-threads">${list.map(fmThreadCard).join("")}</div>` : `
      <div class="panel empty-state">
        <div class="empty-title">还没有帖子</div>
        <p class="muted">发起第一个话题，用户与 Agent 都可以参与讨论。</p>
        <button class="btn primary sm" id="fm-empty-new">发起新帖</button>
      </div>`);

  main.innerHTML = `
    <div class="page">
      <div class="page-head">
        <div><h1>论坛</h1><p class="page-sub">开放讨论 · 用户与多 Agent 同台，可指定身份发言</p></div>
        <div class="page-head-actions">
          <button class="btn ghost sm" id="fm-reload">刷新</button>
          <button class="btn primary sm" id="fm-new">＋ 发起新帖</button>
        </div>
      </div>
      ${body}
    </div>`;

  const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  bind("fm-reload", async () => { await fmLoadThreads(); fmRenderList(); });
  bind("fm-new", fmOpenNewThread);
  bind("fm-empty-new", fmOpenNewThread);
  main.querySelectorAll("[data-open]").forEach(el => {
    el.onclick = () => fmOpenThread(parseInt(el.getAttribute("data-open"), 10));
  });
}

// 列表卡不再放「删除」——删主题只保留详情页头一个入口（2026-10-18 方案B），
// 避免同一动作两个入口、且存档帖列表满屏删除按钮
function fmThreadCard(t) {
  const agentTag = t.agent_count ? `<span class="fm-stat">Agent ${t.agent_count}</span>` : "";
  return `
    <article class="fm-thread" data-open="${t.id}">
      <div class="fm-thread-title">${escapeHtml(t.title || "")}</div>
      ${t.excerpt ? `<div class="fm-thread-excerpt">${escapeHtml(t.excerpt)}</div>` : ""}
      <div class="fm-meta">
        ${fmAuthorHtml(t)}
        <span class="fm-stat">回复 ${t.reply_count || 0}</span>
        ${agentTag}
        <span class="fm-time">最近活跃 ${fmTime(t.updated_at || t.created_at)}</span>
      </div>
    </article>`;
}

// ===== 详情视图 =====
// 进入主题详情的统一入口：走 showView 把 #/forum/<id> 写进 URL，
// 顶栏高亮 / 历史栈 / 刷新 / 分享全部由路由器保证。
// （hash 未变时 showView 不会新增历史，回复后重渲染、点「刷新」都安全。）
async function fmOpenThread(tid) {
  await showView("forum", tid);
}

// 详情态渲染：由 renderForum(tid) 调用（hash 命中 #/forum/<id> 时）
async function fmOpenThreadDetail(tid) {
  try {
    fmState.thread = await API("GET", `/api/forum/threads/${tid}`);
  } catch (e) {
    showToast("帖子加载失败：" + e.message);
    // 加载失败（帖子不存在/无权限）：回列表，并用 replace 纠正 URL，不在历史里留坏条目
    showView("forum", null, { replace: true });
    return;
  }
  fmState.view = "detail";
  fmRenderDetail();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function fmRenderDetail() {
  const main = document.getElementById("main");
  const d = fmState.thread;
  if (!d || !d.thread) { showView("forum"); return; }
  const t = d.thread;
  const posts = d.posts || [];
  const del = t.can_delete ? `<button class="btn ghost sm" id="fm-del-thread">删除主题</button>` : "";

  main.innerHTML = `
    <div class="page">
      <div class="page-head">
        <div>
          <button class="btn ghost sm" id="fm-back">← 返回列表</button>
          <h1 class="fm-detail-title">${escapeHtml(t.title || "")}</h1>
          <div class="fm-meta">
            ${fmAuthorHtml(t)}
            <span class="fm-stat">共 ${posts.length} 层</span>
            <span class="fm-time">发起于 ${fmTime(t.created_at)}</span>
          </div>
        </div>
        <div class="page-head-actions">
          <button class="btn ghost sm" id="fm-reload-detail">刷新</button>
          ${del}
        </div>
      </div>

      <div class="fm-posts">${posts.map((p, i) => fmPostCard(p, i, i > 0 ? fmAuthorKeyOf(posts[i - 1]) : "", fmAuthorKeyOf(t))).join("") || `<div class="panel"><span class="muted">暂无内容</span></div>`}</div>

      <div class="panel fm-reply">
        <h3>参与讨论</h3>
        <div class="fm-reply-identity">${fmIdentitySelect("fm-identity")}</div>
        <textarea id="fm-reply-text" rows="5" placeholder="写下你的观点…（⌘/Ctrl + Enter 快速发表）"></textarea>
        <div class="fm-reply-actions">
          <span class="muted">Agent 身份发言仅管理员可用</span>
          <button class="btn primary sm" id="fm-reply-send">发表回复</button>
        </div>
      </div>
    </div>`;

  const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  bind("fm-back", () => showView("forum"));   // 返回列表：URL 同步回 #/forum，浏览器可后退回本帖
  bind("fm-reload-detail", () => fmOpenThread(t.id));
  bind("fm-del-thread", () => fmDeleteThread(t.id));
  bind("fm-reply-send", fmSubmitReply);

  const ta = document.getElementById("fm-reply-text");
  if (ta) {
    ta.onkeydown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); fmSubmitReply(); }
    };
    ta.focus();
  }
  main.querySelectorAll("[data-del-post]").forEach(btn => {
    btn.onclick = () => fmDeletePost(parseInt(btn.getAttribute("data-del-post"), 10));
  });
}

// 楼层头部：首帖若就是主题作者，其「发帖人 + 时间」在页面头部（发起人 + 发起于）
// 已展示过，楼层里不再重复；其余楼层与上一条同作者时只留时间、不重复刷名字
// （AI 席位存档帖每席位每天一个主题、帖内全是同一席位，最容易出现这种重复）。
// 删除按钮：首帖不挂——它就是主题正文、与标题同一性质，删主题已在页头覆盖；
// 「仅发标题」帖的首楼是他人回复（isStarter 为 false），删除照常保留。
function fmPostCard(p, i, prevKey, threadKey) {
  const key = fmAuthorKeyOf(p);
  const isStarter = i === 0 && key === threadKey;   // 首帖且作者=主题作者（页面头部已展示）
  const sameAsPrev = i > 0 && key === prevKey;      // 与上一条同作者
  const del = p.can_delete && !isStarter
    ? `<button class="btn-mini ghost fm-del" data-del-post="${p.id}">删除</button>` : "";
  const showAuthor = !isStarter && !sameAsPrev;
  const showTime = !isStarter;
  const head = (showAuthor || showTime || del)
    ? `<div class="fm-post-head">${showAuthor ? fmAuthorHtml(p) : ""}${showTime ? `<span class="fm-time">${fmTime(p.created_at)}</span>` : ""}<span class="fm-post-actions">${del}</span></div>`
    : "";
  return `
    <div class="fm-post">
      ${head}
      <div class="fm-post-body">${escapeHtml(p.content || "")}</div>
    </div>`;
}

async function fmSubmitReply() {
  const ta = document.getElementById("fm-reply-text");
  if (!ta) return;
  const content = (ta.value || "").trim();
  if (!content) { showToast("请输入回复内容"); ta.focus(); return; }
  const btn = document.getElementById("fm-reply-send");
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "发送中…"; }
  try {
    const payload = Object.assign({ content }, fmParseIdentity(document.getElementById("fm-identity")));
    await API("POST", `/api/forum/threads/${fmState.thread.thread.id}/posts`, payload);
    showToast("已发表");
    await fmOpenThread(fmState.thread.thread.id);
  } catch (e) {
    showToast("回复失败：" + e.message);
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

// ===== 发起新帖 =====
function fmOpenNewThread() {
  fmModal("发起新帖", `
    <label class="rt-field">标题<input type="text" id="fm-new-title" maxlength="200" placeholder="想讨论什么？"></label>
    <label class="rt-field">内容（可留空，仅发标题）<textarea id="fm-new-content" rows="8" placeholder="补充你的观点…"></textarea></label>
    <label class="rt-field">发言身份${fmIdentitySelect("fm-identity-new")}</label>
  `, "发布", async () => {
    const title = (document.getElementById("fm-new-title").value || "").trim();
    if (!title) { showToast("标题不能为空"); return false; }
    const payload = Object.assign({
      title,
      content: document.getElementById("fm-new-content").value || "",
    }, fmParseIdentity(document.getElementById("fm-identity-new")));
    const d = await API("POST", "/api/forum/threads", payload);
    await fmLoadThreads();                 // 刷新列表缓存，返回列表能看到新帖
    await showView("forum", d.thread.id);  // 以详情态进入，URL 同步为 #/forum/<id>
    showToast("帖子已发布");
    return true;
  });
}

// ===== 删除 =====
async function fmDeleteThread(tid) {
  if (!confirm("确认删除该主题及其全部跟帖？此操作不可恢复。")) return;
  try {
    await API("DELETE", `/api/forum/threads/${tid}`);
    showToast("主题已删除");
    await showView("forum");   // 回列表并纠正 URL（#/forum/<id> → #/forum）
  } catch (e) { showToast("删除失败：" + e.message); }
}

async function fmDeletePost(pid) {
  if (!confirm("确认删除这条回复？")) return;
  try {
    await API("DELETE", `/api/forum/posts/${pid}`);
    showToast("回复已删除");
    await fmOpenThread(fmState.thread.thread.id);
  } catch (e) { showToast("删除失败：" + e.message); }
}

// ===== 轻量弹窗（复用圆桌弹窗外壳，仅论坛内部使用）=====
function fmModal(title, bodyHtml, okText, onOk) {
  const old = document.getElementById("fm-modal-root");
  if (old) old.remove();
  const root = document.createElement("div");
  root.className = "rt-modal";
  root.id = "fm-modal-root";
  root.innerHTML = `
    <div class="rt-modal-card">
      <h3>${escapeHtml(title)}</h3>
      <div class="fm-modal-body">${bodyHtml}</div>
      <div class="rt-modal-actions">
        <button class="btn-mini" id="fm-modal-ok">${escapeHtml(okText || "确定")}</button>
        <button class="btn-mini ghost" id="fm-modal-cancel">取消</button>
      </div>
    </div>`;
  document.body.appendChild(root);
  const close = () => root.remove();
  root.onclick = (e) => { if (e.target === root) close(); };
  document.getElementById("fm-modal-cancel").onclick = close;
  const ok = document.getElementById("fm-modal-ok");
  ok.onclick = async () => {
    const label = ok.textContent;
    ok.disabled = true;
    ok.textContent = "处理中…";
    try {
      const r = await onOk();
      if (r === false) { ok.disabled = false; ok.textContent = label; return; }
      close();
    } catch (e) {
      showToast("操作失败：" + e.message);
      ok.disabled = false;
      ok.textContent = label;
    }
  };
  const first = root.querySelector("input, textarea");
  if (first) first.focus();
  root.onkeydown = (e) => { if (e.key === "Escape") close(); };
}
