"""圆桌观点复盘（G3，2026-10-17）——「建议 → 结果」的最后一环。

平台核心理念是「建议 → 反馈 → 再预测」的闭环：策略信号侧由 Feedback 回放完成，
资讯侧此前缺一环——席位每天引用资讯给出的判断，事后对不对无从回看。本模块补上：

    RoundtablePost（每日观点）──LLM 抽取──▶ RtReview（结构化判断记录）
                                                │ 机械比对（MarketPrice 真实行情）
                                                ▼
                                     席位命中率 / 趋势 / 引用资讯的信息量

设计纪律（不可妥协）：
- **LLM 只负责「文本 → 结构化」抽取，判定全部机械**（方向 × T+N 实际收益）。
  让模型事后自己判「对错」等于让它再编一个故事，违背「可解释、人在回路」。
- 基准日 = 观点日期起首个有行情的日子（观点收盘后发布，基于当日收盘）；
  T+N = 基准后第 N 个交易日。基金净值当晚/次日晚才入库（QDII 更慢），
  未到期或净值缺失一律 pending，由每日任务渐进补算，不做一次性判定。
- hold / observe 方向无法用收益判对错（「不动作」没有对错），一律 flat，
  与「无效波动」（|收益| < FLAT_BAND）一样不计入命中率分母。
"""

import json
import re
from datetime import datetime, timedelta, timezone

from db import (MarketPrice, NewsInjectLog, NewsItem, Project, RtReview,
                RoundtablePost, RoundtableSeat)
from newsai import norm_code

# ---------------- 常量 ----------------
FLAT_BAND = 0.5            # |收益| 小于此值判「无效波动」，不计命中率分母
HORIZON = 5                # 主判定窗（与策略外推 5 个交易日同尺度）
MIN_SAMPLE = 3             # 席位命中徽标的最小有效样本（防小样本误导）
REVIEW_DAYS = 20           # 统计窗口（交易日观点的近 N 天）
BACKFILL_DAYS = 14         # 首跑补批回溯天数
DIRECTIONS = ("up", "down", "hold", "observe")

EXTRACT_SYSTEM = ("你是严谨的数据整理员，只做结构化抽取，不做任何市场判断，"
                  "不补充观点里没有的信息。")

_EXTRACT_SPEC = """# 任务
把上面观点中「明确标的 + 明确方向」的判断抽取为 JSON 数组，每项格式：
{"code": "代码表中的代码", "name": "标的名称", "direction": "up|down|hold|observe",
 "basis_news_ids": [该判断依据的资讯条目编号，没有则空数组],
 "confidence": "高|中|低或空字符串"}

规则：
- direction 语义：up=看多（建议买入/加仓/持有偏多）；down=看空（建议减仓/回避）；
  hold=维持现状；observe=提及但未给方向
- 只抽取能在代码表中找到标的的判断；泛谈大盘/宏观、无具体标的的一律不抽取
- 观点里「依据资讯 7」「（资讯7）」这类表述，数字就是条目编号；逐条判断都要
  检查该标的相关段落里有没有这类引用，有就填进 basis_news_ids
- 没有任何可抽取判断时输出 []
- 只输出 JSON 数组本身，不要解释、不要围栏"""


# ---------------- 工具 ----------------
def _cn_today() -> str:
    """北京日期（与 RoundtablePost.date 同口径）。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _days_ago(days: int) -> str:
    return (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)).strftime("%Y-%m-%d")


def _extract_array(text: str):
    """从模型输出抠出 JSON 数组（容忍 ```json 围栏 / 前后废话 / 包在对象里）。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    for cand in (t,):
        try:
            v = json.loads(cand)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for k in ("calls", "items", "results", "data"):
                    if isinstance(v.get(k), list):
                        return v[k]
        except Exception:
            pass
    i, j = t.find("["), t.rfind("]")
    if i >= 0 and j > i:
        try:
            v = json.loads(t[i:j + 1])
            if isinstance(v, list):
                return v
        except Exception:
            return None
    return None


def verdict_of(direction: str, ret) -> str:
    """方向 × 收益 → 判定。ret=None（未到期/净值缺失）→ pending。"""
    if ret is None:
        return "pending"
    if abs(ret) < FLAT_BAND:
        return "flat"
    if direction == "up":
        return "hit" if ret > 0 else "miss"
    if direction == "down":
        return "hit" if ret < 0 else "miss"
    return "flat"          # hold / observe 无法用收益判对错


def _injected_news(db, date_s: str) -> dict:
    """当日注入提示词的资讯：{pos: NewsItem}。pos = 提示词里「资讯 N」的 N。

    只对 auto 帖有意义（manual 帖是用户从外部 AI 复制的，「资讯 N」属于
    另一套编号体系，强行关联会张冠李戴）。
    """
    rows = (db.query(NewsInjectLog).filter(NewsInjectLog.date == date_s)
            .order_by(NewsInjectLog.pos, NewsInjectLog.item_id).all())
    out = {}
    for i, r in enumerate(rows, 1):
        it = db.get(NewsItem, r.item_id)
        if it:
            out[r.pos or i] = it
    return out


def _project_by_code(db, code: str):
    """归一化代码 → Project（同代码多项目取 id 最小，防御重复建档）。"""
    c = norm_code(code)
    if not c:
        return None
    for p in db.query(Project).order_by(Project.id).all():
        if norm_code(p.code) == c:
            return p
    return None


def _price_series(db, project_id: int, from_date: str) -> list:
    """该标的 from_date 起（含）的 [(date, close)]，按日期升序。"""
    rows = (db.query(MarketPrice)
            .filter(MarketPrice.project_id == project_id,
                    MarketPrice.date >= from_date)
            .order_by(MarketPrice.date).all())
    return [(r.date, r.close) for r in rows if r.close]


def compute_returns(db, code: str, date_s: str):
    """(ret_t1, ret_t5)：基准 = date_s 起首个有行情日；T+N = 基准后第 N 个交易日。

    行情不足（未到期）或该标的无项目档案 → (None, None)（调用方记 pending）。
    """
    p = _project_by_code(db, code)
    if not p:
        return None, None
    series = _price_series(db, p.id, date_s)
    if not series:
        return None, None
    base_date, base = series[0]
    out = []
    for n in (1, HORIZON):
        if len(series) > n:
            out.append(round((series[n][1] / base - 1) * 100, 2))
        else:
            out.append(None)              # 未到期
    return out[0], out[1]


# ---------------- 1) LLM 抽取 ----------------
def extract_calls(db, conf: dict, date_s: str, log=print) -> dict:
    """把某日全部观点抽取成结构化判断并 upsert 进 RtReview。

    返回 {posts, calls, skipped}。已存在的 (date, seat, code) 只更新
    方向/依据/置信度，**不清收益与判定**（那是 review_date 的职责）。
    """
    posts = (db.query(RoundtablePost).filter(RoundtablePost.date == date_s)
             .order_by(RoundtablePost.seat_id).all())
    if not posts:
        return {"posts": 0, "calls": 0, "skipped": []}
    code_lines = [f"- {p.name} → {p.code}" for p in db.query(Project).order_by(Project.id).all()]
    if not code_lines:
        log("无项目档案，无法做名称→代码映射，跳过抽取")
        return {"posts": len(posts), "calls": 0, "skipped": ["no-projects"]}

    from llm import chat_complete
    # 当日注入的资讯（pos→条目）：只有 auto 帖需要——manual 帖是用户从外部
    # AI 复制的，「资讯 N」属于另一套编号体系，强行关联会张冠李戴，恒不溯源。
    news_map = _injected_news(db, date_s)
    n_calls, skipped = 0, []
    for post in posts:
        seat = db.get(RoundtableSeat, post.seat_id)
        seat_name = (seat.name if seat else "") or f"席位{post.seat_id}"
        prompt = (f"# {seat_name} 的每日观点（{date_s}）\n{post.content or ''}\n\n"
                  f"# 可识别的标的（名称 → 代码）\n" + "\n".join(code_lines)
                  + f"\n\n{_EXTRACT_SPEC}")
        try:
            text = chat_complete(
                conf["base_url"], conf["api_key"], conf["model"],
                [{"role": "system", "content": EXTRACT_SYSTEM},
                 {"role": "user", "content": prompt}],
                timeout=120, max_tokens=1500, temperature=0.1,
                via_relay=conf.get("via_relay") or "",
                relay_key=conf.get("relay_key") or "",
                disable_thinking=bool(conf.get("disable_thinking")))
        except Exception as e:
            log(f"  抽取失败（{seat_name}）：{type(e).__name__}: {e}")
            skipped.append(seat_name)
            continue
        calls = _extract_array(text) or []
        # basis 溯源（仅 auto 帖）：观点全文的「资讯 N」是提示词序号，经 pos 映射
        # 成真实条目 id。逐判断粒度太细（观点级引用常常支撑的是整体判断而非某个
        # 标的），故 LLM 给了判断级依据就映射着用，没给就挂本帖观点级引用集合；
        # manual 帖的编号体系不同，一律不溯源（如实：依据不可追溯）。
        post_refs = []
        if post.source == "auto" and news_map:
            seen = set()
            for n in re.findall(r"资讯\s*#?\s*(\d+)", post.content or ""):
                it = news_map.get(int(n))
                if it and it.id not in seen:
                    seen.add(it.id)
                    post_refs.append(it.id)
        for c in calls:
            if not isinstance(c, dict):
                continue
            code = norm_code(str(c.get("code") or ""))
            if not code:
                continue
            direction = str(c.get("direction") or "").strip().lower()
            if direction not in DIRECTIONS:
                direction = "observe"
            basis = c.get("basis_news_ids") or []
            if not isinstance(basis, list):
                basis = [basis]
            basis_ids = ""
            if post.source == "auto" and news_map:
                llm_ids = []
                seen = set()
                for x in basis:
                    if not str(x).strip().isdigit():
                        continue
                    it = news_map.get(int(x))
                    if it and it.id not in seen:
                        seen.add(it.id)
                        llm_ids.append(it.id)
                basis_ids = ",".join(str(x) for x in (llm_ids or post_refs))
            name = str(c.get("name") or "")[:64]
            conf_s = str(c.get("confidence") or "").strip()[:16]
            row = (db.query(RtReview)
                   .filter(RtReview.date == date_s, RtReview.seat_id == post.seat_id,
                           RtReview.code == code).first())
            if row:
                row.direction, row.name = direction, name or row.name
                row.basis_news_ids, row.confidence = basis_ids, conf_s
            else:
                db.add(RtReview(date=date_s, seat_id=post.seat_id, code=code,
                                name=name, direction=direction,
                                basis_news_ids=basis_ids, confidence=conf_s))
            n_calls += 1
        db.commit()
        log(f"  {seat_name}: 抽取 {len(calls)} 条判断")
    return {"posts": len(posts), "calls": n_calls, "skipped": skipped}


# ---------------- 2) 机械判定 ----------------
def review_date(db, date_s: str, log=None) -> int:
    """对某观点日的全部记录重算收益与判定（幂等，可反复跑）。返回记录数。"""
    rows = db.query(RtReview).filter(RtReview.date == date_s).all()
    for r in rows:
        r.ret_t1, r.ret_t5 = compute_returns(db, r.code, date_s)
        r.verdict_t1 = verdict_of(r.direction, r.ret_t1)
        r.verdict_t5 = verdict_of(r.direction, r.ret_t5)
    if rows:
        db.commit()
    if log and rows:
        eff = [r for r in rows if r.verdict_t5 in ("hit", "miss")]
        log(f"  {date_s}: {len(rows)} 条判断，有效样本 {len(eff)}"
            f"（兑现 {sum(1 for r in eff if r.verdict_t5 == 'hit')}）")
    return len(rows)


def run_review(db, conf: dict, date_s: str, log=print) -> dict:
    """抽一天 + 判一天。"""
    ex = extract_calls(db, conf, date_s, log=log)
    n = review_date(db, date_s, log=log)
    return {"date": date_s, "posts": ex["posts"], "calls": ex["calls"],
            "records": n, "skipped": ex["skipped"]}


def backfill(db, conf: dict, days: int = BACKFILL_DAYS, log=print) -> dict:
    """补批：逐日抽取代观点（缺哪抽哪），再对近 HORIZON 日未满窗记录补算。

    已抽过的日期重跑会再送一次 LLM（upsert 幂等）——补批是一次性动作，
    日常 cron 只跑昨天 + 近窗补算，不重抽历史。
    """
    today = _cn_today()
    dates = []
    for i in range(days, 0, -1):
        d = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=i)).strftime("%Y-%m-%d")
        dates.append(d)
    total = {"dates": 0, "posts": 0, "calls": 0}
    for d in dates:
        has = db.query(RtReview).filter(RtReview.date == d).count()
        posts = db.query(RoundtablePost).filter(RoundtablePost.date == d).count()
        if posts and not has:
            r = run_review(db, conf, d, log=log)
            total["dates"] += 1
            total["posts"] += r["posts"]
            total["calls"] += r["calls"]
    # 近 HORIZON 日未满窗的补算（T+5 到期后 verdict 才最终化）
    for i in range(HORIZON + 1):
        d = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=i)).strftime("%Y-%m-%d")
        if db.query(RtReview).filter(RtReview.date == d).count():
            review_date(db, d)
    return total


def daily(db, conf: dict, log=print) -> dict:
    """每日 cron 入口：复盘昨日观点 + 补算近窗未满窗记录。"""
    yesterday = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)).strftime("%Y-%m-%d")
    r = run_review(db, conf, yesterday, log=log)
    fixed = 0
    for i in range(HORIZON + 1):
        d = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=i)).strftime("%Y-%m-%d")
        if db.query(RtReview).filter(RtReview.date == d).count():
            fixed += review_date(db, d)
    return {"reviewed": r, "refreshed": fixed}


# ---------------- 3) 统计（消费方：面板 / 徽标） ----------------
def seat_stats(db, days: int = REVIEW_DAYS) -> list:
    """各席位命中率（verdict_t5 口径，有效样本 = hit+miss）。"""
    start = _days_ago(days)
    seats = {s.id: (s.name or "") for s in db.query(RoundtableSeat).all()}
    rows = db.query(RtReview).filter(RtReview.date >= start).all()
    out = []
    for sid, name in seats.items():
        rs = [r for r in rows if r.seat_id == sid]
        eff = [r for r in rs if r.verdict_t5 in ("hit", "miss")]
        hit = sum(1 for r in eff if r.verdict_t5 == "hit")
        out.append({
            "seat_id": sid, "name": name,
            "hit": hit, "miss": len(eff) - hit,
            "rate": round(hit / len(eff) * 100) if eff else None,
            "pending": sum(1 for r in rs if not r.verdict_t5),
            "flat": sum(1 for r in rs if r.verdict_t5 == "flat"),
            "total": len(rs), "min_ok": len(eff) >= MIN_SAMPLE,
        })
    return sorted(out, key=lambda x: (x["rate"] is None, -(x["rate"] or 0)))


def trend_stats(db, days: int = REVIEW_DAYS) -> list:
    """按观点日期的整体命中率趋势：[{date, hit, miss, rate, n}]。"""
    start = _days_ago(days)
    rows = db.query(RtReview).filter(RtReview.date >= start).all()
    by = {}
    for r in rows:
        if r.verdict_t5 not in ("hit", "miss"):
            continue
        a = by.setdefault(r.date, {"date": r.date, "hit": 0, "miss": 0})
        a["hit" if r.verdict_t5 == "hit" else "miss"] += 1
    out = []
    for d in sorted(by):
        a = by[d]
        n = a["hit"] + a["miss"]
        out.append({"date": d, "hit": a["hit"], "miss": a["miss"],
                    "n": n, "rate": round(a["hit"] / n * 100)})
    return out


def news_hit_stats(db, days: int = REVIEW_DAYS) -> list:
    """引用资讯的信息量：按 basis_news_ids 聚合各条目被引用后的判断兑现情况。"""
    start = _days_ago(days)
    rows = db.query(RtReview).filter(RtReview.date >= start).all()
    agg = {}
    for r in rows:
        for iid in [x for x in (r.basis_news_ids or "").split(",") if x.strip().isdigit()]:
            a = agg.setdefault(int(iid), {"cited": 0, "hit": 0, "miss": 0})
            a["cited"] += 1
            if r.verdict_t5 == "hit":
                a["hit"] += 1
            elif r.verdict_t5 == "miss":
                a["miss"] += 1
    titles = {}
    if agg:
        for it in db.query(NewsItem).filter(NewsItem.id.in_(list(agg))).all():
            titles[it.id] = (it.title or "")[:60]
    out = []
    for iid, a in agg.items():
        eff = a["hit"] + a["miss"]
        out.append({"item_id": iid, "title": titles.get(iid, f"条目 {iid}"),
                    "cited": a["cited"], "hit": a["hit"], "miss": a["miss"],
                    "rate": round(a["hit"] / eff * 100) if eff else None})
    return sorted(out, key=lambda x: (-x["cited"], x["item_id"]))


def samples(db, days: int = REVIEW_DAYS, limit: int = 30) -> list:
    """样本明细（面板表格用）：含席位名与依据资讯标题。"""
    start = _days_ago(days)
    rows = (db.query(RtReview).filter(RtReview.date >= start)
            .order_by(RtReview.date.desc(), RtReview.id.desc()).limit(limit).all())
    seats = {s.id: (s.name or "") for s in db.query(RoundtableSeat).all()}
    news_ids = {int(x) for r in rows for x in (r.basis_news_ids or "").split(",")
                if x.strip().isdigit()}
    titles = {}
    if news_ids:
        for it in db.query(NewsItem).filter(NewsItem.id.in_(list(news_ids))).all():
            titles[it.id] = (it.title or "")[:50]
    out = []
    for r in rows:
        basis = [titles.get(int(x), f"#{x}") for x in (r.basis_news_ids or "").split(",")
                 if x.strip().isdigit()]
        out.append({
            "id": r.id, "date": r.date, "seat": seats.get(r.seat_id, ""),
            "code": r.code, "name": r.name, "direction": r.direction,
            "confidence": r.confidence, "basis": basis,
            "ret_t1": r.ret_t1, "ret_t5": r.ret_t5,
            "verdict_t1": r.verdict_t1, "verdict_t5": r.verdict_t5,
        })
    return out
