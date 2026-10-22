# chief.py —— v2 主事工作台（半人工决策闭环）后端装配层
#
# 职责（设计见 docs/v2-主事工作台设计方案.md §4 / §13）：
#   1. 提示词装配：上下文头（持仓全景 + 目标配置 A1/A2/A3 + 最近会话执行结果）
#                    + 素材体（近期资讯 + 圆桌席位结论按标的聚合）
#                    + 指令尾（三轮各一套：初稿 / 审查 / 终稿）
#   2. 会话 / 三轮原文 / 动作行 的 CRUD（chief_sessions / chief_turns / chief_actions）
#
# 设计纪律（§13.2）：本文件是**全新文件**，只对 app.py 做「import + register_chief(app)」两处追加，
# 现有端点 / 现有表结构零改动。所有对 app 内 helper 的依赖都在 register_chief 内局部 import，
# 避免顶层循环依赖（app 在底部 import 本模块时，其内部符号已定义完毕）。
#
# 平台定位（§0）：**不做决策、不自动下单**——只负责「归集 → 出提示词 → 回收原文 → 跟踪」，
# 决策权永远在用户。第 ① 步两张卡并行（Gemini 审仓位 / ChatGPT 审纪律→产出「目标·计划·规则·纪律」
# 四件套），之后 Gemini 初稿 → ChatGPT 反向 → Gemini 终稿，全程人工桥接复制粘贴。

import json
from datetime import datetime

from fastapi import HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_

import newsai
from db import (
    ChiefGoal, ChiefSession, ChiefTurn, ChiefAction,
    Project, RoundtablePost, RoundtableSeat, RtReview, User,
)


# ---------------------------------------------------------------- 目标配置读取

def _goals_for(u, db):
    """把 chief_goals 拆成 weight / preference 两份。

    weight: {project_id: 百分比}；preference: {note, allow_rebalance}。
    可见性口径与全站一致：自己配的 + 无主配置（owner_id is null）。

    ⚠️ 原 kind=rule（平台侧结构化纪律规则）已于 2026-10-23 **下线**：
    结构化阈值 + 实时违反判定那一套被移除，纪律改由第 ① 步「纪律审视
    （→ChatGPT）」产出的四件套（目标 / 计划 / 规则 / 纪律）承载。
    不要再在此处恢复 rule 分支。
    """
    rows = (db.query(ChiefGoal)
            .filter(or_(ChiefGoal.owner_id == u.id, ChiefGoal.owner_id.is_(None)))
            .all())
    weight, pref = {}, {"note": "", "allow_rebalance": False}
    for g in rows:
        if g.kind == "weight":
            pid = int(str(g.key).replace("p:", "")) if str(g.key).startswith("p:") else None
            if pid:
                try:
                    weight[pid] = float(g.value or 0)
                except (TypeError, ValueError):
                    pass
        elif g.kind == "preference":
            if g.key == "note":
                pref["note"] = g.value or ""
            elif g.key == "allow_rebalance":
                pref["allow_rebalance"] = (g.value or "0") == "1"
    return weight, pref


# 注：原 `_scope_label`（结构化规则的作用范围文案）已随 kind=rule 一并下线。


# ---------------------------------------------------------------- 上下文头（三端一致）

def _latest_reply(u, db, role: str, sid=None) -> str:
    """取某一轮（role）最近一次的主事答复原文。

    优先当前会话；当前会话没做时退回该用户最近一期做过的——
    审视类结论（inspect / ruleinspect）表达的是相对态度与上位纲领，
    跨期仍具参考价值，比「没有」强。
    """
    # 基础查询：任意非空的该轮答复
    q = db.query(ChiefTurn).filter(ChiefTurn.role == role,
                                   ChiefTurn.reply_text.isnot(None),
                                   ChiefTurn.reply_text != "")
    if sid:
        t = q.filter(ChiefTurn.session_id == sid).first()
        if t:
            return t.reply_text
        # 当前会话没做：顺延到用户最近一期（排除当前会话本身）
        rows = (q.join(ChiefSession, ChiefSession.id == ChiefTurn.session_id)
                 .filter(or_(ChiefSession.owner_id == u.id,
                             ChiefSession.owner_id.is_(None)))
                 .filter(ChiefTurn.session_id != sid)
                 .order_by(ChiefSession.created_at.desc()).all())
        return rows[0].reply_text if rows else ""
    rows = (q.join(ChiefSession, ChiefSession.id == ChiefTurn.session_id)
             .filter(or_(ChiefSession.owner_id == u.id,
                         ChiefSession.owner_id.is_(None)))
             .order_by(ChiefSession.created_at.desc()).all())
    return rows[0].reply_text if rows else ""


def _latest_inspect_reply(u, db, sid=None) -> str:
    """取第 ① 步「仓位审视」的 Gemini 结论原文。"""
    return _latest_reply(u, db, "inspect", sid)


def _latest_policy_reply(u, db, sid=None) -> str:
    """取第 ① 步「纪律审视」的 ChatGPT 产出：投资目标 / 计划 / 规则 / 纪律 四件套。"""
    return _latest_reply(u, db, "ruleinspect", sid)


def build_context_header(u, db, sid=None) -> str:
    """上下文头：持仓全景 + 目标配置 + 最近一次会话执行结果。

    持仓/占比全部读实时数据（_build_overview），
    因此每次生成提示词都是「当下真实状态」，不依赖任何缓存快照。
    sid：当前会话 id，用于取该期第 ① 步的 Gemini 审视结论（取不到就退回最近一期）。
    """
    from app import _build_overview, _chief_rule_projects, _cn_today

    projects = _chief_rule_projects(u, db)
    ov = _build_overview(projects, db)
    totals = ov["totals"] or {}
    positions = ov["positions"] or []
    total_mv = sum(float(x.get("market_value") or 0) for x in positions) or 1.0
    positions_sorted = sorted(positions, key=lambda x: -(float(x.get("market_value") or 0)))

    L = [f"# 一、我的持仓全景（截至 {_cn_today()}）", "## 账户"]
    L.append(f"- 总市值 {float(totals.get('market_value') or 0):,.2f} 元"
             f" · 浮动盈亏 {float(totals.get('unrealized') or 0):+,.2f}"
             f" · 累计已实现 {float(totals.get('realized') or 0):+,.2f}")

    L.append("## 持仓明细（按市值降序，占比 = 占总市值 %）")
    if not positions_sorted:
        L.append("- 当前无持仓（本轮只点评大盘即可）")
    else:
        top = positions_sorted[:15]
        cats = {}
        for x in positions_sorted[15:]:
            c = (x.get("category") or "").strip()
            if c:
                cats[c] = cats.get(c, 0.0) + (float(x.get("market_value") or 0))
        for x in top:
            mv = float(x.get("market_value") or 0)
            pct = mv / total_mv * 100
            pctv = x.get("pct")
            seg = (f"- {x['name']}（{x.get('code') or x.get('market')}）："
                   f"占比 {pct:.1f}% · 市值 {mv:,.2f} 元"
                   f" · 收益率 {(pctv if pctv is not None else '—')}")
            if x.get("category"):
                seg += f" · 领域 {x['category']}"
            L.append(seg)
        if cats:
            cat_lines = "、".join(f"{c} {v/total_mv*100:.1f}%" for c, v in cats.items())
            L.append(f"- （其余 {len(positions_sorted)-15} 只按行业聚合：{cat_lines}）")

    # 目标配置（A1→审视 / A2 / A3）
    # 注：A1「逐标的精确目标权重」已废止（多标的场景下过于僵化且从未被配置过），
    # 改用第 0 轮 inspect 的 Gemini 审视结论承载「相对态度 + 大致区间」。
    _, pref = _goals_for(u, db)
    L.append("## 本期持仓审视结论（第 ① 步，由 Gemini 给出、用户已采纳）")
    ins = _latest_inspect_reply(u, db, sid)
    if ins:
        L.append(ins)
    else:
        L.append("- 尚未做仓位审视（跳过 ① 直接调仓时，主事请自行判断仓位合理性）")
    top3 = positions_sorted[:3]
    if top3:
        s3 = sum(float(x.get("market_value") or 0) for x in top3) / total_mv * 100
        L.append(f"- Top3 集中度 {s3:.1f}%（市值前三：{'、'.join(x['name'] for x in top3)}）")

    # ⚠️ 原「纪律规则（结构化条目）」段已随 kind=rule 下线：纪律约束现由下方
    # 「本期投资纲领」的四件套承载，不再有平台侧结构化阈值。
    L.append("## 风险偏好 / 约束（自由文本，承载无法结构化的约束）")
    L.append(f"- 约束说明：{pref['note'] or '（无特别约束）'}")
    L.append(f"- 允许主事建议换仓：{'是' if pref['allow_rebalance'] else '否'}")

    # 第 ① 步第二张卡：由 ChatGPT 审视纪律后产出的四件套，是本期的最上位纲领。
    # ②③④ 的提示词都会带上它，使其实际约束到调仓与规则变更，而非只存档。
    L.append("## 本期投资纲领（第 ① 步「纪律审视」，由 ChatGPT 给出、用户已采纳）")
    pol = _latest_policy_reply(u, db, sid)
    if pol:
        L.append(pol)
    else:
        L.append("- 尚未做纪律审视（未产出投资目标 / 计划 / 规则 / 纪律四件套；"
                 "请以上述「风险偏好」为当前执行依据，不要自行编造纲领）")

    L.append("## 最近一次主事会话与执行结果")
    last = (db.query(ChiefSession)
            .filter(or_(ChiefSession.owner_id == u.id, ChiefSession.owner_id.is_(None)))
            .order_by(ChiefSession.created_at.desc()).first())
    if last:
        actions = db.query(ChiefAction).filter(ChiefAction.session_id == last.id).all()
        rb = [a for a in actions if a.kind == "rebalance"]
        rl = [a for a in actions if a.kind == "rule"]
        L.append(f"- 期号 {last.title or last.id} · 状态 {last.status}"
                 f" · 调仓 {len(rb)} 条（已完成 {sum(1 for a in rb if a.status == 'done')}）"
                 f" · 规则变更 {len(rl)} 条（已采纳 {sum(1 for a in rl if a.status == 'adopted')}）")
    else:
        L.append("- 暂无历史会话（本期为首次）")
    return "\n".join(L)


# ---------------------------------------------------------------- 素材体（三端一致）

def build_material_body(u, db) -> str:
    """素材体：近期资讯（已评分去重）+ 圆桌席位结论按标的聚合。

    圆桌未跑过时只带资讯、不阻塞（§4.3 商议点）。
    """
    from app import _chief_rule_projects, _cn_today

    L = ["# 二、近期资讯（已评分去重，★ = 与我的持仓直接相关）"]
    try:
        codes = [p.code for p in _chief_rule_projects(u, db) if p.code]
        items = newsai.select_news_for_prompt(db, codes, today=_cn_today())
    except Exception as e:
        items = []
        L.append(f"（资讯选条失败：{e}）")
    L.append(newsai.news_prompt_block(items or []))

    L.append("# 三、圆桌席位分析结论（按标的聚合：共识方向 + 分歧点）")
    reviews = (db.query(RtReview).order_by(RtReview.date.desc()).limit(300).all())
    if not reviews:
        L.append("- 暂无圆桌观点数据（可先跑圆桌；本模块不影响主事调仓主线）")
        return "\n".join(L)
    latest = reviews[0].date
    day = [r for r in reviews if r.date == latest]
    by_code = {}
    for r in day:
        by_code.setdefault(r.code, []).append(r)
    L.append(f"（基于 {latest} 各席位观点抽取，共 {len(day)} 条判断）")
    for code, rs in by_code.items():
        ups = sum(1 for r in rs if r.direction == "up")
        downs = sum(1 for r in rs if r.direction == "down")
        holds = sum(1 for r in rs if r.direction in ("hold", "observe"))
        if ups > downs:
            cons = "看多/加仓"
        elif downs > ups:
            cons = "看空/减仓"
        else:
            cons = "中性 / 分歧"
        name = rs[0].name or code
        L.append(f"- {name}（{code}）：共识 = {cons}（看多 {ups} / 看空 {downs} / 观望 {holds}）")
        opposite = "down" if ups >= downs else "up"
        divs = [r for r in rs if r.direction == opposite]
        if divs:
            names = "、".join((d.name or code) for d in divs[:3])
            L.append(f"    ▸ 分歧：{names} 持相反看法")
    return "\n".join(L)


# ---------------------------------------------------------------- 指令尾（各轮不同）

# 第 0 轮：仓位审视。刻意不要求 Gemini 输出精确目标百分比——
# 多标的场景下让用户逐填精确权重过于僵化（17 只标的 = 17 个必须自洽的数字，
# 且市场一动就天天偏离）。改由主事给出「相对态度 + 大致区间」，用户据此终裁。
INSPECT_TAIL = (
    "你是「主事」，现在只做**第一步：审视当前持仓仓位**，先不要给完整调仓指令。\n"
    "请针对上面的持仓全景逐项指出：\n"
    "1. **仓位观感**：哪些标的明显偏重 / 偏轻？集中度、行业分布是否合理？\n"
    "2. **相对态度**：对每只标的给出**定性判断**（核心持有 / 维持 / 逐步减持 / 择机加仓 / 考虑替换），"
    "并说明依据（估值、景气度、资讯、圆桌结论，引用上方编号）。\n"
    "3. **建议区间**：如需给出配置比例，请给**大致区间**（如「10–15%」）而非精确单点值；"
    "用户会自行终裁，不必凑合计数到 100%。\n"
    "4. **存疑项**：哪些标的信息不足、你判断不了？明确列出，不要硬编理由。\n"
    "输出用自然语言分点。**不要输出买卖指令**，那是后续轮次的事。"
)

# 第 ① 步第二张卡：纪律审视（→ ChatGPT）。与上面的仓位审视并列，但不是同一件事——
# 仓位审视问「现在摆得对不对」，纪律审视问「我据以决策的那套规矩本身还成不成立」，
# 并把它落成「投资目标 / 投资计划 / 规则 / 纪律」四件套，作为本期最上位纲领。
# 刻意要求**四个固定标题**：一是便于用户整段复制留档，二是防止模型漏写某一项。
RULEINSPECT_TAIL = (
    "你是我的「投资纪律顾问」，现在只做**第一步：审视我的投资纪律**，"
    "并在此基础上产出我整套投资纲领。先不要给任何买卖指令。\n"
    "输入里有我的持仓全景、Top3 集中度、风险偏好与约束、上一版纲领（若有）。"
    "请据此判断：**这套纪律在当前市场与我的持仓结构下还成不成立**，"
    "再按下面**四个固定标题**输出一份修订后可直接执行的纲领：\n\n"
    "## 一、投资目标\n"
    "我要的是什么：投资期限、收益预期、可接受的最大回撤、资金性质约束。"
    "给**区间而非单点**，并说明它与我「风险偏好 / 约束」是否自洽；不自洽要指出来。\n\n"
    "## 二、投资计划\n"
    "为达成上述目标的执行路径：总仓位与现金的经营范围、分批建仓 / 加减仓的节奏、"
    "资金如何在标的间分配、以及**什么情况下不做任何操作**。每条写清触发条件。\n\n"
    "## 三、交易规则\n"
    "可量化、可被平台机械校验的规则：单一标的 / 单一行业占比上限、单笔金额上限、止损止盈线等。"
    "每条按「**规则名 → 适用范围 → 阈值 → 理由**」四要素给，"
    "**阈值必须是一个数字**（百分比或金额，不要写「视情况而定」），便于我原样录入平台。\n\n"
    "## 四、交易纪律\n"
    "规则之外的行为红线：什么情况下**必须**动手、什么情况下**禁止**动手、"
    "面对亏损 / 踏空 / 噪音信息时的自我约束。用祈使句列点，短而硬。\n\n"
    "硬性约束：\n"
    "- 四个标题**一个都不能少，顺序、措辞照抄**，便于我整段复制留档；\n"
    "- 有上一版纲领时，针对它做**增补与修订**（说明改了哪一条、为什么），不要另起炉灶；"
    "没有上一版时按当前持仓与风险偏好从头拟定；现有条款合理就明说「建议保留」；\n"
    "- 判断不了 / 信息不足的项，直接写「建议保留现状，暂不调整」，不要硬编理由；\n"
    "- **不要输出买卖指令**，那是后续轮次的事。"
)

DRAFT_TAIL = (
    "你是「主事」，负责给出**组合级**的调控方案。请基于以上信息，分**两条并列主线**输出，"
    "均由你给建议、由用户终裁：\n"
    "1. **调仓主线**：当前组合是否需要调仓？逐标的给出调整指南——减 / 加 / 换 + 目标比例区间"
    " + 理由 + 触发条件；请尊重上方「持仓审视结论」中的相对态度，并说明每条建议所依据的"
    "纪律规则 / 资讯 / 圆桌结论（引用上方编号）。\n"
    "2. **规则主线**：评估近期市场 / 政策变化是否要求调整纪律规则（如单一标的/行业上限）。"
    "若需调整，给出**规则变更建议**：改哪条、改成什么、理由；不改也明确说「无需调整」及原因。\n"
    "输出用自然语言 + 分点，不强制表格；调仓与规则变更**并列呈现**，同等重要。"
)

REVIEW_TAIL = (
    "你是「反向审查者」，只对上面的主事初稿做审查、**不另起方案**。逐条指出：\n"
    "1. **调仓主线审查**：初稿中每条调仓建议的问题 / 风险 / 修正建议；有无违反用户纪律规则或风险偏好之处。\n"
    "2. **规则主线审查**：初稿的规则变更建议是否过度反应 / 是否遗漏重要政策或市场影响 / 阈值是否合理。\n"
    "输出为「审查意见」清单，明确指出同意 / 反对 / 补充的点，供主事终稿吸收。"
)

FINAL_TAIL = (
    "你是「主事（终裁）」，请综合吸收上面的审查意见，给出**最终方案**，仍分两条主线：\n"
    "1. **调仓终稿**：修订后的最终调仓方案（含目标权重、理由、触发条件）。\n"
    "2. **规则变更终稿**：最终规则变更建议（改哪条 / 改成什么 / 理由）。\n"
    "必须**显式说明采纳 / 未采纳哪些审查意见及原因**（可追溯）。这是执行依据。"
)

ROUND_TAIL = {"inspect": INSPECT_TAIL, "ruleinspect": RULEINSPECT_TAIL,
              "draft": DRAFT_TAIL, "review": REVIEW_TAIL, "final": FINAL_TAIL}
# 工作流主顺序：ruleinspect 不单独占一步——它与 inspect 同属第 ① 步的两张卡，
# 两者平行（可以先审仓位也可以先审纪律），谁也不依赖谁。
ROUND_ORDER = ("inspect", "draft", "review", "final")


def build_prompt(round_name: str, header: str, body: str, embed: str = "") -> str:
    """组装完整提示词：上下文头 + 素材体 + 任务尾。

    round_name ∈ {inspect, draft, review, final}。
    review 内嵌初稿原文、final 内嵌审查原文、draft 内嵌仓位审视结论（embed）。
    """
    L = [header, "", body, "", "# 四、你的任务"]
    tail = ROUND_TAIL.get(round_name, DRAFT_TAIL)
    if round_name == "review":
        L += ["以下是主事初稿：", "-----", embed or "（无初稿原文）", "-----", "", tail]
    elif round_name == "final":
        L += ["以下是审查意见：", "-----", embed or "（无审查意见）", "-----", "", tail]
    elif round_name == "draft" and embed:
        # ① 审视结论作为 draft 的软约束：提供相对态度，但不要求照抄具体数字
        L += ["以下是你（或上一轮）对仓位的审视结论：", "-----", embed, "-----", "", tail]
    else:
        L += [tail]
    return "\n".join(L)


# ---------------------------------------------------------------- 序列化

def _session_out(s: ChiefSession, db: Session) -> dict:
    actions = db.query(ChiefAction).filter(ChiefAction.session_id == s.id).all()
    rb = sum(1 for a in actions if a.kind == "rebalance")
    rl = sum(1 for a in actions if a.kind == "rule")
    return {
        "id": s.id, "title": s.title or "", "status": s.status or "inspect",
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "rebalance_count": rb, "rule_count": rl,
    }


def _action_out(a: ChiefAction) -> dict:
    return {
        "id": a.id, "kind": a.kind or "rebalance", "action": a.action or "",
        "target": a.target or "", "target_weight": a.target_weight,
        "new_value": a.new_value or "", "reason": a.reason or "",
        "trigger": a.trigger or "", "source_quote": a.source_quote or "",
        "status": a.status or "planned",
    }


def _session_detail(s: ChiefSession, db: Session) -> dict:
    turns = db.query(ChiefTurn).filter(ChiefTurn.session_id == s.id).all()
    actions = db.query(ChiefAction).filter(ChiefAction.session_id == s.id).all()
    return {
        "id": s.id, "title": s.title or "", "status": s.status or "inspect",
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "turns": {t.role: {"prompt": t.prompt_text or "", "reply": t.reply_text or ""}
                  for t in turns},
        "actions": [_action_out(a) for a in actions],
    }


# ---------------------------------------------------------------- 规则采纳写回

# 状态推进：每存一轮原文，会话状态推进到该轮对应状态；只进不退（重新保存同轮不回退）。
_STATUS_ORDER = ["inspect", "draft", "review", "final", "executing", "done"]

# role → 会话状态。ruleinspect 与 inspect 同属第 ① 步，映射到同一个状态。
_ROLE_STATUS = {"inspect": "inspect", "ruleinspect": "inspect",
                "draft": "draft", "review": "review", "final": "final"}


# ⚠️ 原 `_apply_rule_action`（规则主线动作行标 adopted → 写回 chief_goals kind=rule）
# 已随结构化纪律规则一并下线：平台不再持有结构化阈值，规则改动由第 ① 步四件套承载。
# kind=rule 的**动作行本身仍然保留**（终稿的规则变更建议仍要能被记录/标注是否采纳），
# 只是「标采纳」不再产生任何写库副作用。


# ---------------------------------------------------------------- 路由注册

def register_chief(app):
    """在 app.py 挂载静态文件之前调用：注册全部 chief 端点。

    所有对 app 内 helper 的依赖都在本函数内局部 import，避免顶层循环依赖。
    """
    from app import get_db, get_current_user, _cn_today  # noqa: F401

    @app.get("/api/chief/news")
    def chief_news(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
        """A5 区块：近期政策/市场变化（复用资讯选条逻辑，★ 标相关）。"""
        from app import _chief_rule_projects
        codes = [p.code for p in _chief_rule_projects(u, db) if p.code]
        try:
            items = newsai.select_news_for_prompt(db, codes, today=_cn_today())
        except Exception as e:
            return {"items": [], "error": str(e)}
        return {"items": items or []}

    @app.post("/api/chief/sessions")
    def chief_create_session(body: dict, u: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
        """新建一期调控会话（状态 inspect）。title 缺省用今日日期。"""
        title = (body.get("title") or "").strip() or _cn_today()
        s = ChiefSession(owner_id=u.id, title=title, status="inspect")
        db.add(s); db.commit(); db.refresh(s)
        return _session_out(s, db)

    @app.get("/api/chief/sessions")
    def chief_list_sessions(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
        rows = (db.query(ChiefSession)
                .filter(or_(ChiefSession.owner_id == u.id, ChiefSession.owner_id.is_(None)))
                .order_by(ChiefSession.created_at.desc()).all())
        return {"items": [_session_out(s, db) for s in rows]}

    @app.get("/api/chief/sessions/{sid}")
    def chief_get_session(sid: int, u: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
        s = db.get(ChiefSession, sid)
        if not s:
            raise HTTPException(404, "会话不存在")
        return _session_detail(s, db)

    @app.post("/api/chief/prompt")
    def chief_build_prompt(body: dict, u: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
        """装配某一轮的完整提示词（不落库）。

        body: {session_id, round: inspect|ruleinspect|draft|review|final}
        review 内嵌初稿原文、final 内嵌审查原文、draft 内嵌仓位审视结论
        （均取自已保存的 chief_turns）。ruleinspect 不内嵌任何前序原文——
        它审视的是纪律规则本身（已由上下文头带上）。
        """
        sid = body.get("session_id")
        rnd = body.get("round") or "draft"
        if rnd not in ROUND_TAIL:
            raise HTTPException(400, "round 必须为 inspect / ruleinspect / draft / review / final")
        header = build_context_header(u, db, sid=sid)
        body_text = build_material_body(u, db)
        embed = ""
        if rnd == "review":
            need_role = "draft"
        elif rnd == "draft":
            need_role = "inspect"
        elif rnd == "final":
            need_role = "review"
        else:
            need_role = ""
        if need_role:
            t = (db.query(ChiefTurn)
                 .filter(ChiefTurn.session_id == sid, ChiefTurn.role == need_role).first()
                 if sid else None)
            embed = t.reply_text if t else ""
        prompt = build_prompt(rnd, header, body_text, embed)
        return {"prompt": prompt, "round": rnd}

    @app.post("/api/chief/turns")
    def chief_save_turn(body: dict, u: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
        """保存某一轮的提示词 + 主事答复原文（upsert by session_id+role），并推进会话状态。

        role ∈ {inspect, ruleinspect, draft, review, final}。
        """
        sid = body.get("session_id")
        role = body.get("role") or "draft"
        if role not in ROUND_TAIL:
            raise HTTPException(400, "role 必须为 inspect / ruleinspect / draft / review / final")
        s = db.get(ChiefSession, sid)
        if not s:
            raise HTTPException(404, "会话不存在")
        t = (db.query(ChiefTurn)
             .filter(ChiefTurn.session_id == sid, ChiefTurn.role == role).first())
        if not t:
            t = ChiefTurn(session_id=sid, role=role)
            db.add(t)
        t.prompt_text = body.get("prompt_text") or ""
        t.reply_text = body.get("reply_text") or ""
        t.snapshot_json = json.dumps({"saved_at": datetime.utcnow().isoformat()})
        # 状态推进：只进不退。ruleinspect 映射到 inspect（第 ① 步），
        # 故会话已走到第 ④ 步时补存它不会被拉回开头。
        if s.status not in _STATUS_ORDER:
            s.status = "inspect"
        cur = _STATUS_ORDER.index(s.status)
        tgt = _ROLE_STATUS.get(role)
        nxt = _STATUS_ORDER.index(tgt) if tgt in _STATUS_ORDER else cur
        if nxt > cur:
            s.status = tgt
        db.commit(); db.refresh(t)
        return {"ok": True, "role": role,
                "turn": {"prompt": t.prompt_text, "reply": t.reply_text},
                "status": s.status}

    @app.post("/api/chief/sessions/{sid}/actions")
    def chief_save_actions(sid: int, body: dict, u: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
        """从终稿提取的动作行候选，整体覆盖保存为该期动作行（C1 落库）。

        同一期多次提取 = 整体替换（用户会反复调整候选）。
        """
        s = db.get(ChiefSession, sid)
        if not s:
            raise HTTPException(404, "会话不存在")
        db.query(ChiefAction).filter(ChiefAction.session_id == sid).delete(
            synchronize_session=False)
        for it in (body.get("items") or []):
            db.add(ChiefAction(
                session_id=sid,
                kind=(it.get("kind") or "rebalance"),
                action=(it.get("action") or ""),
                target=(it.get("target") or ""),
                target_weight=(it.get("target_weight") if it.get("target_weight") not in (None, "")
                              else None),
                new_value=(it.get("new_value") or ""),
                reason=(it.get("reason") or ""),
                trigger=(it.get("trigger") or ""),
                source_quote=(it.get("source_quote") or ""),
                status=(it.get("status") or "planned"),
            ))
        s.status = "executing" if s.status == "final" else s.status
        db.commit()
        return {"ok": True}

    @app.patch("/api/chief/actions/{aid}")
    def chief_update_action(aid: int, body: dict, u: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
        """更新单条动作行（标注状态 / 权重 / 新值 / 理由）。

        规则主线（kind=rule）被标为 adopted → 立即写回 chief_goals 留痕（§12.3）。
        """
        a = db.get(ChiefAction, aid)
        if not a:
            raise HTTPException(404, "动作行不存在")
        for f in ("action", "target", "new_value", "reason", "trigger", "source_quote"):
            if f in body and body[f] is not None:
                setattr(a, f, body[f])
        if "target_weight" in body and body["target_weight"] not in (None, ""):
            try:
                a.target_weight = float(body["target_weight"])
            except (TypeError, ValueError):
                pass
        if "status" in body and body["status"] is not None:
            # 规则动作行标 adopted 现在是纯标注：平台侧已无结构化阈值可写回。
            a.status = body["status"]
        db.commit()
        return {"ok": True, "action": _action_out(a)}
