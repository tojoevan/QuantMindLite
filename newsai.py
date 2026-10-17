"""资讯 AI 结构化加工（newsai）—— 给圆桌 Agent 喂资讯的「加工层」。

职责边界（与 newsfetch.py 的分工）：
    newsfetch.py  抓取入库（源站字段，纯管道）
    newsai.py     AI 加工（本地派生字段 ai_*：重要度 / 理由 / 标签 / 关联标的 / 同题合并）
                 + 选条注入席位提示词 + 生成「市场概要」正文

三条消费出口，同一份数据：
    1. 席位提示词注入（select_news_for_prompt + news_prompt_block）
    2. 资讯页徽标（ai_score / ai_reason / ai_tags / ai_codes 由 item_out 直接下发）
    3. 论坛「市场概要」帖（build_digest 正文，发布动作在 app._publish_news_digest）

为什么 LLM 配置由调用方（app 层）传入而不是自己读库：
    本模块不 import app（app 要 import 本模块，循环依赖），Provider 默认值
    （PROVIDER_DEFAULTS）与出站路由（app._llm_route）也在 app 里。调用方用
    app.news_llm_conf(db) 拿好 conf 传进来，键位与 llm.chat_complete 一致：
        {api_key, base_url, model, via_relay, relay_key, disable_thinking}

LLM 可达性（2026-10-17 实测）：quant-web 所在 VPS 只有 IPv6 出站，
api.deepseek.com 无 AAAA 记录，直连必败（DNS Name or service not known）。
app._llm_route 探测不可达时自动改走 rss-relay 的 POST 通道
（relay.kapibala.icu，白名单已含 api.deepseek.com），调用方无感。
⚠️ 中继域名在 Cloudflare 后面，UA 太朴素会被 1010 拦（llm.py 已用浏览器 UA）。
"""
import json
import os
import re
from datetime import datetime, timedelta

from db import NewsItem, NewsInjectLog, NewsDigest, ForumThread, ForumPost

# ---- 可调参数 ----
SCORE_CHUNK = 30            # 每次送 LLM 的条目数（30 条 × ~120 字摘要 ≈ 5K 字符，单次调用稳）
SCORE_WINDOW_DAYS = 5       # 加工窗口：只处理最近 N 天发布且未加工的条目
SCORE_MIN = 3               # 注入/概要的最低重要度（1-5 分制）
PROMPT_DAYS = 3             # 注入窗口：近 72 小时（资讯影响有滞后，当天太窄）
PROMPT_TOPK = 10            # 单次提示词最多注入几条（持仓相关优先，再补市场级大事）
MARKET_MIN = 3              # 其中至少留几条市场级（非持仓相关）——科技重仓下行业新闻
                            # 会占满注入位，没这条规则大盘大事一条都进不去
DIGEST_MIN_SCORE = 4        # 进「市场概要」的最低重要度
DIGEST_MAX_ITEMS = 10       # 概要喂给编辑模型的素材上限（与席位注入 PROMPT_TOPK 对齐）
DIGEST_MAX_TOKENS = 2200    # 正文生成上限。旧值 1600 时实测只输出 755 字——不是被截断，
                            # 是模型自己写短；提上限必须配合 _DIGEST_SPEC 的规格加严才有效
DIGEST_REVIEW = True        # 生成后同一把 Key 再调一次做审稿（交叉验证，不依赖第二个
                            # provider）；审阅失败/解析失败一律降级用原稿，不阻塞发布
SUMMARY_SNIP = 150          # 送评分时摘要截断长度（原文可能几千字，烧 token）

SCORE_SYSTEM = (
    "你是严谨的A股投研资讯分析助手。你的任务是把一批资讯条目结构化："
    "评估其对 A 股市场 / 投资者决策的重要度，提取关联标的代码，并识别多源同题报道。"
    "只基于给定内容判断，不臆测条目之外的信息。严格按要求的 JSON 格式输出。"
)

_SCORE_SPEC = """# 输出格式（只输出 JSON，不要任何解释文字）
{"items": [{"id": 12, "score": 4, "reason": "一句话理由", "tags": ["政策", "新能源"], "codes": ["600519"], "dup_of": null}]}

# 字段规则
- id：条目编号，原样带回
- score：重要度 1-5 的整数。5=影响 A 股整体或多数板块的重大政策/事件；4=与具体行业或个股直接相关的重要消息；3=有一定信息量的行业动态；2=关联度低的资讯；1=与投资无关
- reason：不超过 40 字的一句话，说清「这条为什么重要」或「为什么不重要」
- tags：2-4 个中文标签，如 政策 / 业绩 / 行业 / 公司 / 宏观 / 国际
- codes：条文中提及的 A股/港股/美股标的代码，只填 6 位代码（如 600519、00700、AAPL 这类原样保留）；没有则空数组 []
- dup_of：若本条与列表中另一条是**同一事件的不同来源报道**，填那条的 id（保留信息更全的为主）；否则 null

# 纪律
- 不确定的字段给保守值：拿不准是不是同题就填 null，拿不准代码就不填
- 只输出一个 JSON 对象，能被 json.loads 直接解析"""

DIGEST_SYSTEM = (
    "你是严谨的A股财经编辑，为投资者每日梳理市场要点。只基于给定的今日资讯与数据写作，"
    "不臆测资讯之外的数据，不给出具体买卖指令。"
)

# 发布时点语境（2026-10-17 加深）：旧版没有这段，模型把隔夜美股当「今日大事」写
# （首篇实测就有这问题）。21:20 发布时 A 股已收盘、美股未开盘，必须让模型知道。
_PUBLISH_CONTEXT = """# 发布时点
本概要于每日晚间自动发布（A 股收盘后、美股开盘前）：下方「今日数据速览」为当日收盘口径；
凡涉及美股的内容均为隔夜（美东时间）情况，表述时注明「隔夜」，不要写成今日发生。"""

_DIGEST_SPEC = """# 输出格式（纯文本 markdown，不要表格，不要代码围栏）
■ 昨日关注复盘（仅当上方给出了「昨日关注点」；没有则整节省略）
1. <昨日关注点原文> → <兑现了 / 落空了 / 进行中>：<一句依据，引用今日资讯编号或数据速览>
■ 今日大事
1. <一句话标题>：<2-3 句解读，说清这对市场或相关行业意味着什么>（来源：<来源名>）
■ 数据看点
1. <基于「今日数据速览」的一两句话：只陈述数据事实与直接含义，不编造原因；没有数据则整节省略>
■ 持仓影响
<结合上方持仓**逐只**给出：方向（正面/负面/中性/无直接影响）+ 一句理由 + 置信度（高/中/低）。
确实没有直接影响就明说「今日资讯对持仓无直接影响」，不要硬编>
■ 明日关注
1. <接下来值得留意的点 + 跟踪指标（看什么数据/什么信号来判断）>

# 纪律
- 昨日复盘逐条对应不遗漏；今日大事 3-5 条按重要度从高到低，每条必须来自上方资讯并标注来源
- 数字必须与「今日数据速览」完全一致，不得改写、不得外推没有给的数据
- 持仓影响逐只覆盖，未提及的持仓不要臆测受益或受损
- 总长 1000-1400 字，说人话，不堆术语
- 不给出「买入/卖出」指令，只做事实梳理与影响分析"""

# 交叉审阅（2026-10-17 加深）：同一把 Key 二次调用，审稿编辑角色。
# 为什么不用第二个 provider：交叉验证的价值在「不同角色/不同 prompt 的第二次判断」，
# 换 provider 只是顺带；单独为它配第二个 Key 不划算（且 Moonshot Key 用户尚未配）。
DIGEST_REVIEW_SYSTEM = (
    "你是苛刻的A股财经审稿编辑。你的职责是校对一篇市场概要草稿："
    "只基于给定的素材与数据校对，不引入素材之外的新信息，不改变原稿的事实结论方向。"
)

_DIGEST_REVIEW_SPEC = """# 审稿清单（逐项检查，发现问题记入 issues）
1. 过度推断：草稿中的因果/影响是否都有素材或数据支撑？无依据的推断要指出
2. 时点错误：涉及美股的是否注明「隔夜」？有没有把昨夜美股写成今日发生的事？
3. 素材遗漏：重要度 4-5 分的资讯是否在「今日大事」有对应条目？
4. 持仓覆盖：「持仓影响」是否逐只覆盖持仓清单？有没有把没提到的持仓说成受益/受损？
5. 数字一致：数据速览里的数字是否被准确引用？改写/外推/算错的要纠正
6. 昨日复盘：每条昨日关注点是否都有对应复盘结论？

# 输出格式（只输出 JSON）
{"issues": ["问题1", "问题2"], "revised": "修订后的完整正文"}

# 纪律
- revised 是修订后的**完整正文**（保持原结构与「■」段落标记），不是补丁也不是差异
- 没有问题也要输出 revised（= 原稿或仅做文字润色），issues 可为空数组
- 只输出一个 JSON 对象，能被 json.loads 直接解析"""


# ---------------- 工具 ----------------
def norm_code(s: str) -> str:
    """标的代码归一化：sh600519 / 600519.SH / 上证 600519 → 600519。

    ai_codes（LLM 输出）与持仓代码（project.code）两边都过这个函数再比对，
    避免「sh 前缀 vs 纯数字」这种格式差异导致关联永远匹配不上。
    """
    s = (s or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    m = re.match(r"^(?:sh|sz|bj)(\d{6})$", s)          # sh600519
    if m:
        return m.group(1)
    m = re.match(r"^(\d{6})\.(?:sh|sz|bj)$", s)        # 600519.SH
    if m:
        return m.group(1)
    if re.match(r"^[0-9a-z]{4,8}$", s):                # 600519 / 00700 / aapl
        return s
    return ""


def norm_codes(raw) -> list:
    """把 LLM 返回的 codes（列表或逗号串）归一化成去重后的代码列表。"""
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,，;；\s]+", raw) if x]
    out = []
    for x in raw or []:
        c = norm_code(str(x))
        if c and c not in out:
            out.append(c)
    return out


# ---------------- 0) 持仓行业画像（覆盖场外基金） ----------------
# 新闻几乎不出现场外基金代码，ai_codes 精确匹配对 12/16 个持仓（基金）恒不命中。
# 用一份「标的 → 行业 + 关键词」画像把匹配扩展到关键词层：新闻
# 标题/摘要/标签命中某持仓的行业词，即算「与持仓相关」。
#
# 数据文件：quant-web/fund_profiles.json（与代码同目录，部署随仓库同步，
# 改完即生效——按 mtime 缓存，无需重启）。格式：
#     {"600406.SH": {"industry": "电力设备", "keywords": ["电网", "特高压"]}, ...}
# 只收录**当前持仓**（匹配时会与用户真实持仓求交，已清仓的留着也不会误亮）。
# 宽基（沪深300/A500 等）刻意只收指数全名，不收「A股/大盘」这类泛词——
# 市场级噪音由选条的「市场大事补位」机制覆盖，不占 ★ 优先级。
FUND_PROFILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fund_profiles.json")
_PROFILE_CACHE = {"mtime": None, "data": {}}


def load_fund_profiles() -> dict:
    """读持仓行业画像。文件缺失/损坏 → {}（徽标降级为原来的代码精确匹配）。"""
    try:
        mt = os.path.getmtime(FUND_PROFILE_FILE)
        if _PROFILE_CACHE["mtime"] != mt:
            with open(FUND_PROFILE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _PROFILE_CACHE["mtime"] = mt
            _PROFILE_CACHE["data"] = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return _PROFILE_CACHE["data"]


def profile_hits(text: str, profiles: dict = None) -> set:
    """返回行业关键词命中 text 的持仓代码集合（已归一化）。

    关键词大小写不敏感；任一关键词命中即算该标的命中（子串匹配）。
    profiles 默认读文件缓存；传 {} 表示关闭画像（纯代码匹配）。
    """
    if profiles is None:
        profiles = load_fund_profiles()
    if not text or not profiles:
        return set()
    low = str(text).lower()
    hits = set()
    for code, p in profiles.items():
        for kw in ((p or {}).get("keywords") or []):
            if kw and str(kw).lower() in low:
                c = norm_code(code)
                if c:
                    hits.add(c)
                break
    return hits


def profile_industry(code: str, profiles: dict = None) -> str:
    """取某标的的行业标签（画像里没有返回空串）。"""
    if profiles is None:
        profiles = load_fund_profiles()
    return ((profiles.get(code) or {}).get("industry") or "")


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _extract_json(text: str):
    """从模型输出里抠出 JSON 对象（容忍 ```json 围栏 / 前后废话）。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None


# ---------------- 1) 批量评分（AI 结构化加工） ----------------
def build_score_prompt(rows: list) -> str:
    """rows: [{id, title, summary, source, time}] → 用户提示词。"""
    L = ["# 待加工资讯条目（共 %d 条）" % len(rows), ""]
    for r in rows:
        head = f"{r['id']}. 【{r['time']} · {r['source']}】{r['title']}"
        L.append(head)
        if r.get("summary"):
            L.append(f"   摘要：{_clip(r['summary'], SUMMARY_SNIP)}")
    L += ["", _SCORE_SPEC]
    return "\n".join(L)


def parse_score_result(text: str) -> list:
    """解析评分结果 → [{id, score, reason, tags, codes, dup_of}]；解析失败返回 []。"""
    j = _extract_json(text)
    if not isinstance(j, dict):
        return []
    items = j.get("items")
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("id") is None:
            continue
        try:
            sid = int(it["id"])
        except (TypeError, ValueError):
            continue
        try:
            score = int(it.get("score"))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            score = max(1, min(5, score))
        dup_of = it.get("dup_of")
        try:
            dup_of = int(dup_of) if dup_of is not None else None
        except (TypeError, ValueError):
            dup_of = None
        tags = it.get("tags") or []
        if isinstance(tags, str):
            tags = [x for x in re.split(r"[,，]", tags) if x.strip()]
        out.append({
            "id": sid,
            "score": score,
            "reason": _clip(str(it.get("reason") or ""), 120),
            "tags": [str(t).strip()[:12] for t in tags if str(t).strip()][:4],
            "codes": norm_codes(it.get("codes")),
            "dup_of": dup_of,
        })
    return out


def score_items(db, conf: dict, limit: int = 60, days: int = SCORE_WINDOW_DAYS,
                dry_run: bool = False, log=print) -> dict:
    """批量加工未评分的资讯条目。返回统计 dict。

    - 选条：published_at 在最近 days 天内、ai_score 为空（未加工）、按时间倒序取 limit 条；
    - 分块送 LLM（SCORE_CHUNK 条/次），逐块提交，单块失败不影响其它块；
    - 同题合并：模型给出 dup_of 时写库；注入/概要选条时再据此去重。
    """
    from llm import chat_complete

    start = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(NewsItem)
            .filter(NewsItem.ai_score.is_(None), NewsItem.published_at.isnot(None),
                    NewsItem.published_at >= start)
            .order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
            .limit(max(1, limit)).all())
    if not rows:
        return {"picked": 0, "scored": 0, "chunks": 0, "failed": 0, "dry_run": dry_run}

    payload = [{
        "id": r.id, "title": r.title or "", "summary": r.summary or "",
        "source": r.source_name or "", "time": _fmt_cn(r.published_at),
    } for r in rows]
    by_id = {r.id: r for r in rows}

    stats = {"picked": len(rows), "scored": 0, "chunks": 0, "failed": 0, "dry_run": dry_run}
    for i in range(0, len(payload), SCORE_CHUNK):
        chunk = payload[i:i + SCORE_CHUNK]
        try:
            text = chat_complete(
                conf["base_url"], conf["api_key"], conf["model"],
                [{"role": "system", "content": SCORE_SYSTEM},
                 {"role": "user", "content": build_score_prompt(chunk)}],
                timeout=90, max_tokens=2400, temperature=0.2,
                via_relay=conf.get("via_relay") or "",
                relay_key=conf.get("relay_key") or "",
                disable_thinking=bool(conf.get("disable_thinking")))
        except Exception as e:
            stats["failed"] += 1
            log(f"  FAIL 评分块 #{i // SCORE_CHUNK + 1}（{len(chunk)} 条）：{e}")
            continue
        parsed = parse_score_result(text)
        if not parsed:
            stats["failed"] += 1
            log(f"  FAIL 评分块 #{i // SCORE_CHUNK + 1} 解析失败：{_clip(text, 120)}")
            continue
        stats["chunks"] += 1
        if dry_run:
            for p in parsed:
                log(f"  [dry-run] #{p['id']} {p['score']}分 "
                    f"{','.join(p['tags'])} codes={p['codes']} dup_of={p['dup_of']} {p['reason']}")
            continue
        n = 0
        for p in parsed:
            it = by_id.get(p["id"])
            if it is None:
                continue
            it.ai_score = p["score"]
            it.ai_reason = p["reason"]
            it.ai_tags = ",".join(p["tags"])
            it.ai_codes = ",".join(p["codes"])
            if p["dup_of"] and p["dup_of"] != it.id and p["dup_of"] in by_id:
                it.ai_dup_of = p["dup_of"]
            n += 1
        db.commit()
        stats["scored"] += n
        log(f"  OK 评分块 #{i // SCORE_CHUNK + 1}：{n} 条落库")
    return stats


# ---------------- 2) 注入选条 + 提示词块 ----------------
def last_inject_dates(db, item_ids) -> dict:
    """{item_id: 最近一次注入日期}。没有记录的不出现。"""
    if not item_ids:
        return {}
    rows = (db.query(NewsInjectLog.item_id, NewsInjectLog.date)
            .filter(NewsInjectLog.item_id.in_(list(item_ids))).all())
    out = {}
    for iid, d in rows:
        if iid not in out or (d or "") > out[iid]:
            out[iid] = d or ""
    return out


def log_injections(db, date_s: str, item_ids) -> int:
    """记录今日注入（幂等：已存在的 (date, item) 跳过）。返回新增条数。"""
    ids = [i for i in (item_ids or []) if i]
    if not ids:
        return 0
    have = {(r.item_id) for r in
            db.query(NewsInjectLog.item_id).filter(NewsInjectLog.date == date_s).all()}
    n = 0
    for iid in ids:
        if iid in have:
            continue
        db.add(NewsInjectLog(date=date_s, item_id=iid))
        have.add(iid)
        n += 1
    db.commit()
    return n


def select_news_for_prompt(db, holding_codes, today: str,
                           days: int = PROMPT_DAYS, top_k: int = PROMPT_TOPK,
                           min_score: int = SCORE_MIN,
                           market_min: int = MARKET_MIN) -> list:
    """选注入席位提示词的资讯：持仓相关的排最前，再按重要度补市场级大事。

    - 窗口：近 days 天发布、已加工且重要度 >= min_score；
    - 去重：同题副本（ai_dup_of 指向的主条目也在候选里）跳过；
    - 防重复喂：**上次注入日期 < 今天**的才排除（同一天重复生成/试跑仍带同一批，
      第二天起让位给新条目——否则一个政策会连读三天）。
    - 配额：市场级至少保底 market_min 条（候选里不够就有多少给多少）——否则
      持仓偏科技时行业新闻会占满 top_k，大盘大事一条都进不去。
    返回 [{id, title, source, date, time, score, reason, codes, link, related}]。
    """
    codes = {norm_code(c) for c in (holding_codes or []) if norm_code(c)}
    profiles = load_fund_profiles()
    start = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(NewsItem)
            .filter(NewsItem.ai_score.isnot(None), NewsItem.ai_score >= min_score,
                    NewsItem.published_at.isnot(None), NewsItem.published_at >= start)
            .order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
            .all())
    if not rows:
        return []
    cand_ids = {r.id for r in rows}
    injected = last_inject_dates(db, cand_ids)

    picked, seen = [], set()
    for r in rows:
        if r.id in seen:
            continue
        # 同题去重：主条目在候选集中 → 这是副本，跳过；主条目不在（被窗口裁掉）→ 保留自己
        if r.ai_dup_of and r.ai_dup_of in cand_ids and r.ai_dup_of != r.id:
            continue
        last = injected.get(r.id, "")
        if last and last < today:
            continue                      # 更早的日期已注入过 → 不重复喂；今天注入过仍带上
        seen.add(r.id)
        item_codes = [norm_code(c) for c in (r.ai_codes or "").split(",") if norm_code(c)]
        # 持仓相关 = 代码精确命中 ∪ 行业画像关键词命中（后者覆盖场外基金：
        # 新闻不出现基金代码，但会出现「半导体」「医药」这类行业词）
        text = f"{r.title or ''} {r.summary or ''} {r.ai_tags or ''}"
        picked.append({
            "id": r.id, "title": r.title or "", "source": r.source_name or "",
            "date": _fmt_cn(r.published_at, "%m-%d"), "time": _fmt_cn(r.published_at, "%H:%M"),
            "score": r.ai_score, "reason": r.ai_reason or "",
            "codes": item_codes, "link": r.link or "",
            "related": bool(codes & (set(item_codes) | profile_hits(text, profiles))),
        })

    related = sorted([p for p in picked if p["related"]],
                     key=lambda x: (-(x["score"] or 0), x["id"]))
    market = sorted([p for p in picked if not p["related"]],
                    key=lambda x: (-(x["score"] or 0), x["id"]))
    # 市场级保底：候选里有几条市场级就保几条（上限 market_min），剩余名额给持仓相关
    market_quota = min(market_min, len(market))
    head = related[:max(top_k - market_quota, 0)]
    return (head + market)[:top_k]


def news_prompt_block(items: list) -> str:
    """把选中的资讯渲染成提示词里的一节。空列表也给一节（明示「没有」，任务规格要靠它）。"""
    L = ["## 近期相关资讯（近 %d 小时 · 共 %d 条 · ★ = 与我的持仓直接相关）"
         % (PROMPT_DAYS * 24, len(items))]
    if not items:
        L.append("- 近期暂无重要度达标的资讯")
        return "\n".join(L)
    for i, it in enumerate(items, 1):
        star = "★" if it["related"] else ""
        L.append(f"{i}. {star}【重要度 {it['score']}】{it['title']}"
                 f"（{it['source']} · {it['date']} {it['time']}）")
        if it.get("reason"):
            L.append(f"   要点：{it['reason']}")
        if it.get("codes"):
            L.append(f"   关联标的：{'、'.join(it['codes'])}")
    L.append("")
    L.append("注意：以上是已由系统评分去重后的资讯。给出建议时**必须引用条目编号**作为依据；"
             "与这些资讯无关的判断，明确标注「纯技术面判断」。")
    return "\n".join(L)


# ---------------- 3) 市场概要正文 ----------------

# 与 app._UPDATE_PREFIX_RE 保持一致（本模块不 import app——循环依赖）；改那边记得同步这边
_UPDATE_PREFIX_RE = re.compile(r"^（第 \d+ 次更新 · [^）]*）\n")
# 「■ 明日关注」标题行：容忍 ■/##/【/冒号等修饰（模型输出格式会漂移，别写死）
_WATCH_HEAD_RE = re.compile(r"^\s*(?:■|#|##|【)?\s*明日关注\s*(?:】)?\s*[:：]?\s*$")
_WATCH_ITEM_RE = re.compile(r"^\s*\d+\s*[.、]\s*(.+?)\s*$")


def _digest_stats(db, date_s: str) -> dict:
    """当日资讯入库统计（北京日 24h 窗口，与 select_digest_items 同口径）。"""
    day = datetime.strptime(date_s, "%Y-%m-%d")
    start = day - timedelta(hours=8)
    end = start + timedelta(days=1)
    rows = (db.query(NewsItem.ai_score)
            .filter(NewsItem.published_at.isnot(None),
                    NewsItem.published_at >= start, NewsItem.published_at < end).all())
    scores = [r[0] for r in rows if r[0] is not None]
    return {"total": len(rows), "scored": len(scores),
            "avg": round(sum(scores) / len(scores), 1) if scores else None}


def _data_brief_lines(snapshot: dict, stats: dict) -> list:
    """拼「今日数据速览」节（确定性数据，纯字符串拼装，提示词里禁止模型改写）。

    snapshot 来自 app._build_snapshot（indices / totals / holdings 富字段）；
    stats 来自 _digest_stats。任何一段缺失都优雅降级——该段跳过，全空返回 []
    （整节省略，不影响其余节）。
    """
    L = []
    snap = snapshot or {}
    idx = [i for i in (snap.get("indices") or []) if i.get("close") and i.get("chg_pct") is not None]
    if idx:
        parts = [f"{i['name']} {i['close']:.2f}（{i['chg_pct']:+.2f}%）" for i in idx]
        L.append("指数（收盘）：" + "；".join(parts))
    tot = snap.get("totals") or {}
    if tot.get("market_value"):
        mv = float(tot["market_value"])
        un = float(tot.get("unrealized") or 0)
        cost = mv - un
        pct = f"{un / cost * 100:+.2f}%" if cost > 1e-9 else "—"
        L.append(f"账户：总市值 {mv:,.2f} 元 · 浮动盈亏 {un:+,.2f} 元（{pct}）"
                 f" · 累计已实现 {float(tot.get('realized') or 0):+,.2f} 元")
    holds = [h for h in (snap.get("holdings") or []) if h.get("day_chg_pct") is not None]
    if holds:
        up = sum(1 for h in holds if h["day_chg_pct"] > 0)
        dn = sum(1 for h in holds if h["day_chg_pct"] < 0)
        flat = len(holds) - up - dn
        # 领涨只从上涨的里取、领跌只从下跌的里取——按全量排序切片会在不足 2 只时
        # 把对立面的股票凑进来（实测：1 跌时「领跌」第 2 名是 +1.85% 的上涨股）
        top_up = sorted([h for h in holds if h["day_chg_pct"] > 0],
                        key=lambda h: -h["day_chg_pct"])[:2]
        top_dn = sorted([h for h in holds if h["day_chg_pct"] < 0],
                        key=lambda h: h["day_chg_pct"])[:2]
        fmt = lambda h: f"{h['name']} {h['day_chg_pct']:+.2f}%"
        seg = f"持仓今日：{up} 涨 {dn} 跌 {flat} 平"
        if top_up:
            seg += f"；领涨：{'、'.join(fmt(h) for h in top_up)}"
        if top_dn:
            seg += f"；领跌：{'、'.join(fmt(h) for h in top_dn)}"
        L.append(seg)
    if stats and stats.get("total"):
        avg = stats["avg"] if stats.get("avg") is not None else "—"
        L.append(f"资讯入库：今日 {stats['total']} 条"
                 f"（已评分 {stats['scored']} 条 · 均分 {avg}）")
    return L


def _extract_watch_items(content: str) -> list:
    """从昨日概要正文解析「明日关注」节的编号条目。解析不出返回 []（该节从略）。"""
    lines = (content or "").splitlines()
    items, in_sec = [], False
    for ln in lines:
        if _WATCH_HEAD_RE.match(ln):
            in_sec = True
            continue
        if in_sec:
            if re.match(r"^\s*(?:■|#)", ln):         # 下一个段落标题 → 本节结束
                break
            m = _WATCH_ITEM_RE.match(ln)
            if m:
                items.append(m.group(1).strip())
    return [x for x in items if x][:6]


def _yesterday_watch(db, date_s: str) -> list:
    """昨日概要的「明日关注」条目。昨日没发过（首次运行/断更）或解析失败 → []。

    昨日正文走 NewsDigest.thread_id → ForumPost 最后一层（剥「第 N 次更新」前缀）——
    NewsDigest 表本身不存正文（date/thread_id/updates 三列），正文的唯一出处是论坛帖。
    """
    try:
        prev = (datetime.strptime(date_s, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        rec = db.query(NewsDigest).filter(NewsDigest.date == prev).first()
        if not (rec and rec.thread_id):
            return []
        t = db.get(ForumThread, rec.thread_id)
        if not t:
            return []
        last = (db.query(ForumPost).filter(ForumPost.thread_id == t.id)
                .order_by(ForumPost.id.desc()).first())
        if not last:
            return []
        return _extract_watch_items(_UPDATE_PREFIX_RE.sub("", last.content or "", count=1))
    except Exception:
        return []


def select_digest_items(db, date_s: str, holding_codes=None,
                        min_score: int = DIGEST_MIN_SCORE,
                        max_items: int = DIGEST_MAX_ITEMS) -> list:
    """选当日进「市场概要」的资讯：北京日 24h 窗口、重要度达标、同题去重、
    **持仓相关优先**（与席位注入 select_news_for_prompt 同一判定：持仓代码精确命中
    ∪ 行业画像关键词命中），同级按重要度从高到低。

    ⚠️ 「持仓相关优先」自 2026-10-17 才真正实现——docstring 早就这么写，但旧实现
    只按分数平铺（持仓相关混在里面），加深改造时补上。
    """
    day = datetime.strptime(date_s, "%Y-%m-%d")
    start = day - timedelta(hours=8)                 # 北京零点 → UTC naive
    end = start + timedelta(days=1)
    rows = (db.query(NewsItem)
            .filter(NewsItem.ai_score.isnot(None), NewsItem.ai_score >= min_score,
                    NewsItem.published_at.isnot(None),
                    NewsItem.published_at >= start, NewsItem.published_at < end)
            .order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).all())
    cand_ids = {r.id for r in rows}
    codes = {norm_code(c) for c in (holding_codes or []) if norm_code(c)}
    profiles = load_fund_profiles()
    out = []
    for r in rows:
        if r.ai_dup_of and r.ai_dup_of in cand_ids and r.ai_dup_of != r.id:
            continue
        item_codes = [norm_code(c) for c in (r.ai_codes or "").split(",") if norm_code(c)]
        text = f"{r.title or ''} {r.summary or ''} {r.ai_tags or ''}"
        out.append({
            "id": r.id, "title": r.title or "", "source": r.source_name or "",
            "time": _fmt_cn(r.published_at), "score": r.ai_score,
            "reason": r.ai_reason or "", "link": r.link or "",
            "codes": item_codes,
            "related": bool(codes and (codes & (set(item_codes) | profile_hits(text, profiles)))),
        })
    # 持仓相关排前（not related → False 优先），同级按重要度、再按 id 稳定
    out.sort(key=lambda x: (not x["related"], -(x["score"] or 0), x["id"]))
    return out[:max_items]


def _review_digest(conf: dict, materials: str, content: str, log=print):
    """同 Key 二次审阅（交叉验证）。返回 (修订后正文, issues)；任何失败返回 (原稿, [])。

    审阅是增强不是闸门：调用失败 / 返回非 JSON / revised 为空，一律用原稿发布——
    绝不让审阅环节把发布链路搞挂（宁可少一层校对，不能没有每日概要）。
    """
    from llm import chat_complete
    user = f"{materials}\n\n# 待审草稿\n{content}\n\n{_DIGEST_REVIEW_SPEC}"
    try:
        text = chat_complete(
            conf["base_url"], conf["api_key"], conf["model"],
            [{"role": "system", "content": DIGEST_REVIEW_SYSTEM},
             {"role": "user", "content": user}],
            timeout=120, max_tokens=2600, temperature=0.2,
            via_relay=conf.get("via_relay") or "",
            relay_key=conf.get("relay_key") or "",
            disable_thinking=bool(conf.get("disable_thinking")))
        j = _extract_json(text)
        if not isinstance(j, dict):
            raise ValueError(f"审阅结果不是 JSON 对象: {type(j).__name__}")
        revised = re.sub(r"^```(?:markdown)?\s*|\s*```$", "",
                         str(j.get("revised") or ""), flags=re.S).strip()
        issues = j.get("issues") or []
        if isinstance(issues, str):
            issues = [issues]
        if not revised:
            log("  审阅未给出修订稿（采用原稿）")
            return content, issues
        log(f"  审阅完成：{len(issues)} 个问题，采用修订稿（{len(revised)} 字）")
        for it in issues[:8]:
            log(f"    - {str(it)[:100]}")
        return revised, issues
    except Exception as e:
        log(f"  审阅失败（采用原稿）：{type(e).__name__}: {str(e)[:150]}")
        return content, []


def build_digest(db, conf: dict, date_s: str, holdings: list,
                 snapshot: dict = None, dry_run: bool = False, log=print):
    """生成当日「市场概要」正文。返回 (title, content)；无达标资讯返回 (None, None)。

    holdings: [{name, code}]，用于让编辑模型谈「对持仓的影响」。
    snapshot: app._build_snapshot 的产出（可选）。带上则 prompt 多一节
        「今日数据速览」（指数/账户/持仓真实数据）；不带或取数失败则跳过该节。

    2026-10-17 加深（五处，动机是「对复杂金融市场，三段式资讯复读太单薄」）：
      1. 数据速览：确定性数据（指数/账户/持仓涨跌/入库统计）直接引用，禁改写
      2. 昨日复盘：昨日「明日关注」逐条对 today（兑现/落空/进行中）——概要从孤品日报
         变成连续剧；首次运行自动跳过
      3. 时点语境：写明「A 股收盘后、美股开盘前」，美股内容须注「隔夜」
      4. 素材 6→10 条且持仓相关优先；规格加严（逐持仓方向+理由+置信度、总长 1000-1400 字）
      5. 同 Key 二次审阅：过度推断/时点错误/素材遗漏/持仓覆盖/数字一致性六项校对
    """
    from llm import chat_complete

    items = select_digest_items(
        db, date_s, holding_codes=[h.get("code") or "" for h in (holdings or [])])
    if not items:
        return None, None
    L = [_PUBLISH_CONTEXT]
    watch = _yesterday_watch(db, date_s)
    if watch:
        L += ["", "# 昨日关注点（请逐条复盘：兑现了 / 落空了 / 进行中）"]
        L += [f"{i}. {w}" for i, w in enumerate(watch, 1)]
    L += ["", f"# 今日重要资讯（{date_s} · 系统已评分，重要度 {DIGEST_MIN_SCORE}-5）", ""]
    for i, it in enumerate(items, 1):
        star = "★" if it.get("related") else ""
        L.append(f"{i}. {star}【重要度 {it['score']}】{it['title']}"
                 f"（来源：{it['source']} · {it['time']}）")
        if it["reason"]:
            L.append(f"   要点：{it['reason']}")
        if it["codes"]:
            L.append(f"   关联标的：{'、'.join(it['codes'])}")
    L += ["", "# 我的持仓"]
    profiles = load_fund_profiles()
    if holdings:
        for h in holdings:
            # 带上行业画像：基金名称对编辑模型不透明（「中欧医疗健康混合C」≠ 医药），
            # 行业标签让它能谈「对持仓的影响」
            ind = profile_industry(h.get("code") or "", profiles)
            suffix = f" · {ind}" if ind else ""
            L.append(f"- {h.get('name') or ''}（{h.get('code') or '—'}{suffix}）")
    else:
        L.append("- 当前无持仓")
    brief = _data_brief_lines(snapshot, _digest_stats(db, date_s))
    if brief:
        L += ["", "# 今日数据速览（确定性数据，直接引用，禁止改写）"] + brief
    materials = "\n".join(L)
    prompt = materials + "\n\n" + _DIGEST_SPEC

    if dry_run:
        log(f"[dry-run] 素材 {len(items)} 条，提示词 {len(prompt)} 字符，不调 LLM")
        return f"【市场概要】{date_s}", prompt

    text = chat_complete(
        conf["base_url"], conf["api_key"], conf["model"],
        [{"role": "system", "content": DIGEST_SYSTEM},
         {"role": "user", "content": prompt}],
        timeout=120, max_tokens=DIGEST_MAX_TOKENS, temperature=0.3,
        via_relay=conf.get("via_relay") or "",
        relay_key=conf.get("relay_key") or "",
        disable_thinking=bool(conf.get("disable_thinking")))
    content = (text or "").strip()
    content = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", content, flags=re.S).strip()
    if not content:
        raise RuntimeError("模型返回为空")
    if DIGEST_REVIEW:
        content, _issues = _review_digest(conf, materials, content, log=log)
    return f"【市场概要】{date_s}", content


# ---------------- 时间格式 ----------------
def _fmt_cn(dt, fmt: str = "%m-%d %H:%M") -> str:
    """UTC naive → 北京时间字符串（服务器时区是 UTC，不能直接用本地时间）。"""
    if not dt:
        return ""
    return (dt + timedelta(hours=8)).strftime(fmt)
