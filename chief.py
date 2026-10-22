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
# 决策权永远在用户（Gemini 初稿 → ChatGPT 反向 → Gemini 终稿，人工桥接复制粘贴）。

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
    """把 chief_goals 按 kind 拆成 weight / rule / preference 三份。

    weight: {project_id: 百分比}；rule: [{scope, op, value, note}]；
    preference: {note, allow_rebalance}。
    可见性口径与全站一致：自己配的 + 无主配置（owner_id is null）。
    """
    rows = (db.query(ChiefGoal)
            .filter(or_(ChiefGoal.owner_id == u.id, ChiefGoal.owner_id.is_(None)))
            .all())
    weight, rule, pref = {}, [], {"note": "", "allow_rebalance": False}
    for g in rows:
        if g.kind == "weight":
            pid = int(str(g.key).replace("p:", "")) if str(g.key).startswith("p:") else None
            if pid:
                try:
                    weight[pid] = float(g.value or 0)
                except (TypeError, ValueError):
                    pass
        elif g.kind == "rule":
            try:
                val = float(g.value or 0)
            except (TypeError, ValueError):
                val = 0.0
            rule.append({"scope": g.key or "any_pos", "op": g.op or "le",
                         "value": val, "note": g.note or ""})
        elif g.kind == "preference":
            if g.key == "note":
                pref["note"] = g.value or ""
            elif g.key == "allow_rebalance":
                pref["allow_rebalance"] = (g.value or "0") == "1"
    return weight, rule, pref


def _scope_label(scope: str, positions) -> str:
    """作用范围文案（与前端 chiefScopeLabel 语义一致）：
    any_pos / any_cat / pos:<id> / cat:<名称>。"""
    s = str(scope or "any_pos")
    k = s.split(":")[0]
    arg = s.split(":", 1)[1] if ":" in s else ""
    if k == "any_pos":
        return "任一单一标的"
    if k == "any_cat":
        return "任一单一行业"
    if k == "pos":
        p = next((x for x in positions if str(x["id"]) == arg), None)
        return "标的 · " + (p["name"] if p else arg or "—")
    if k == "cat":
        return "行业 · " + (arg or "—")
    return s


# ---------------------------------------------------------------- 上下文头（三端一致）

def _latest_inspect_reply(u, db, sid=None) -> str:
    """取第 ① 步「仓位审视」的 Gemini 结论原文。

    优先当前会话；当前会话没做审视时退回该用户最近一期做过的——
    审视结论表达的是相对态度，跨期仍具参考价值，比「没有」强。
    """
    # 基础查询：任意非空的 inspect 轮答复
    q = db.query(ChiefTurn).filter(ChiefTurn.role == "inspect",
                                   ChiefTurn.reply_text.isnot(None),
                                   ChiefTurn.reply_text != "")
    if sid:
        t = q.filter(ChiefTurn.session_id == sid).first()
        if t:
            return t.reply_text
        # 当前会话没审视：顺延到用户最近一期（排除当前会话本身）
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


def build_context_header(u, db, sid=None) -> str:
    """上下文头：持仓全景 + 目标配置 + 最近一次会话执行结果。

    持仓/占比/规则当前值全部读实时数据（_build_overview / _chief_check_rules），
    因此每次生成提示词都是「当下真实状态」，不依赖任何缓存快照。
    sid：当前会话 id，用于取该期第 ① 步的 Gemini 审视结论（取不到就退回最近一期）。
    """
    from app import _build_overview, _chief_rule_projects, _chief_check_rules, _cn_today

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
    _, rule, pref = _goals_for(u, db)
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

    L.append("## 纪律规则（结构化条目，当前是否违反由平台实时判定）")
    if rule:
        checks = _chief_check_rules(rule, u, db)
        st_map = {"break": "【违反】", "warn": "【接近】", "ok": "【达标】", "none": "【未设阈值】"}
        for c in checks.get("items", []):
            op = "≥" if c["op"] == "ge" else "≤"
            hits = "、".join(h["name"] for h in (c.get("hits") or []))
            L.append(f"- {_scope_label(c['scope'], positions)} {op} {c['value']:.0f}%"
                     f" {st_map.get(c['status'], '')}当前 {c['current']:.1f}%"
                     f"{('（命中 ' + hits + '）') if hits else ''}")
    else:
        L.append("- 未配置纪律规则")

    L.append("## 风险偏好 / 约束（自由文本，承载无法结构化的约束）")
    L.append(f"- 约束说明：{pref['note'] or '（无特别约束）'}")
    L.append(f"- 允许主事建议换仓：{'是' if pref['allow_rebalance'] else '否'}")

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

ROUND_TAIL = {"inspect": INSPECT_TAIL, "draft": DRAFT_TAIL,
              "review": REVIEW_TAIL, "final": FINAL_TAIL}
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


def _apply_rule_action(a: ChiefAction, u: User, db: Session):
    """规则主线动作行被采纳 → 写回 chief_goals（立即生效，§12.3）并在 note 留痕。

    动作行的 target 编码作用范围（scope），new_value 编码阈值（如 "15" / "40"）。
    action=revoke 表示废止该规则（删除对应 kind=rule 行）。
    """
    scope = (a.target or "").strip()
    if not scope:
        return
    if a.action == "revoke":
        db.query(ChiefGoal).filter(
            ChiefGoal.kind == "rule", ChiefGoal.key == scope,
            or_(ChiefGoal.owner_id == u.id, ChiefGoal.owner_id.is_(None)),
        ).delete(synchronize_session=False)
        db.commit()
        return
    try:
        val = float(a.new_value or 0)
    except (TypeError, ValueError):
        val = 0.0
    if val <= 0:
        return
    # 复用已有的纪律规则默认值：op=le（上限）。若 target 含 "ge:" 前缀则取下限。
    op = "le"
    if scope.startswith("ge:"):
        op = "ge"; scope = scope[3:]
    existing = (db.query(ChiefGoal).filter(
        ChiefGoal.kind == "rule", ChiefGoal.key == scope,
        or_(ChiefGoal.owner_id == u.id, ChiefGoal.owner_id.is_(None)),
    ).first())
    trace = f"依据第 {a.session_id} 期主事终稿采纳"
    if existing:
        existing.op = op
        existing.value = f"{val:g}"
        existing.note = (existing.note or "").strip() + (f"；{trace}" if trace else "")
    else:
        db.add(ChiefGoal(owner_id=u.id, kind="rule", key=scope, op=op,
                         value=f"{val:g}", note=trace))
    db.commit()


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

        body: {session_id, round: inspect|draft|review|final}
        review 内嵌初稿原文、final 内嵌审查原文、draft 内嵌仓位审视结论
        （均取自已保存的 chief_turns）。
        """
        sid = body.get("session_id")
        rnd = body.get("round") or "draft"
        if rnd not in ROUND_TAIL:
            raise HTTPException(400, "round 必须为 inspect / draft / review / final")
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
        """保存某一轮的提示词 + 主事答复原文（upsert by session_id+role），并推进会话状态。"""
        sid = body.get("session_id")
        role = body.get("role") or "draft"
        if role not in ROUND_TAIL:
            raise HTTPException(400, "role 必须为 inspect / draft / review / final")
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
        try:
            cur = _STATUS_ORDER.index(s.status) if s.status in _STATUS_ORDER else 0
            nxt = _STATUS_ORDER.index(role) if role in _STATUS_ORDER else cur
        except ValueError:
            cur, nxt = 0, 0
        if nxt > cur:
            s.status = role
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
            a.status = body["status"]
            if a.kind == "rule" and a.status == "adopted":
                _apply_rule_action(a, u, db)
        db.commit()
        return {"ok": True, "action": _action_out(a)}
