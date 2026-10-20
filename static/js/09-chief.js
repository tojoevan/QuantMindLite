// 09-chief —— 主事工作台（v2：信息中枢 + 执行跟踪器，不做决策 / 不自动下单）
// 设计来源：docs/v2-主事工作台设计方案.md §12 / §13；步骤见 docs/v2-开发步骤拆分.md。
//
// S2 阶段：仅页面骨架与入口。目标与规则审视 / 三轮提示词 / 执行跟踪 / 历史回溯
// 等卡片随 A / B / C / D 各步逐步填充到 #chief-cards 内。
// 全部样式复用现有 .panel / h3 / .muted 等类，暂不新增 CSS。

function renderChief() {
  const main = document.getElementById("main");
  if (!main) return;
  main.innerHTML = `
    <div class="chief-page">
      <div class="panel">
        <h3>本期会话 <span class="muted">未开始</span></h3>
        <div id="chief-cards">
          <p class="muted">目标与规则审视、三轮提示词、执行跟踪、历史回溯等卡片将在后续步骤逐步落地。</p>
        </div>
      </div>
    </div>`;
}
