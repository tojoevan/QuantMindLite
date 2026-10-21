"""Pydantic 请求/响应模型。"""
from typing import Optional, List
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str
    code: str = ""
    market: str = "A"
    strategy: str = ""
    strategy_type: str = "momentum"
    strategy_config: Optional[str] = "{}"
    bias: str = "neutral"
    position_shares: float = 0.0
    position_cost: float = 0.0
    category: str = ""


class ProjectOut(BaseModel):
    id: int
    name: str
    code: str = ""
    market: str = "A"
    strategy: str = ""
    strategy_type: str = "momentum"
    strategy_config: str = "{}"
    bias: str = "neutral"
    position_shares: float = 0.0
    position_cost: float = 0.0
    pinned: int = 0
    category: str = ""
    created_at: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    market: Optional[str] = None
    strategy: Optional[str] = None
    strategy_type: Optional[str] = None
    strategy_config: Optional[str] = None
    bias: Optional[str] = None
    position_shares: Optional[float] = None
    position_cost: Optional[float] = None
    pinned: Optional[int] = None
    category: Optional[str] = None


class PricePoint(BaseModel):
    date: str
    close: float


class PriceOut(BaseModel):
    date: str
    close: float


class FetchReq(BaseModel):
    code: Optional[str] = None      # 可选，缺省用项目的 code
    days: Optional[int] = None      # 不传则由后端按品种决定（基金 365 自然日 / 场内见 app.fetch_prices）
    force: Optional[bool] = False    # True=强制重新拉取；False=今日已拉取则跳过（自动拉取用）


class PredictionCreate(BaseModel):
    date: Optional[str] = None
    signal: Optional[str] = None      # BUY / SELL / HOLD（生成建议时通常由预测器产出）
    predicted_price: Optional[float] = None
    predicted_low: Optional[float] = None
    predicted_high: Optional[float] = None
    confidence: Optional[float] = None
    model: Optional[str] = None
    note: Optional[str] = None


class PredictionOut(PredictionCreate):
    id: int
    project_id: int
    date: str


class FeedbackCreate(BaseModel):
    date: str
    action: str                       # BUY / SELL / HOLD
    price: float
    qty: float = 0.0
    prediction_id: Optional[int] = None
    note: str = ""


class FeedbackOut(FeedbackCreate):
    id: int
    project_id: int
    realized_pnl: float = 0.0


class FeedbackUpdate(BaseModel):
    """编辑已提交的反馈（错误操作可改正确）。字段均可选，只更新传入的字段。"""
    date: Optional[str] = None
    action: Optional[str] = None          # BUY / SELL / HOLD
    price: Optional[float] = None
    qty: Optional[float] = None
    prediction_id: Optional[int] = None
    note: Optional[str] = None


class ChartData(BaseModel):
    market_prices: list[PricePoint]
    predictions: list[PredictionOut]
    feedbacks: list[FeedbackOut]
    pnl_curve: list[dict]            # [{date, pnl}]
    position: dict                   # {shares, avg, realized, ...}
    strategy_curve: list[dict] = [] # 贯穿历史的策略预测价曲线 [{date, predicted_price, predicted_low, predicted_high, signal}]


# ================= 圆桌讨论 =================
class RoundtableSeatCreate(BaseModel):
    name: str
    persona: str = ""
    style_id: Optional[int] = None   # 引用的风格模板；为空=自定义（用 persona）
    mode: str = "manual"             # 接入方式：manual 手动回填 / auto LLM Key 自动 / mcp（本期仅标注）
    provider: str = "deepseek"
    model: str = ""
    role: str = ""                   # AI 角色（豆包/DeepSeek/元宝等），手动席位身份标签


class RoundtableSeatUpdate(BaseModel):
    name: Optional[str] = None
    persona: Optional[str] = None
    style_id: Optional[int] = None
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    role: Optional[str] = None
    enabled: Optional[int] = None
    sort: Optional[int] = None


class RoundtableSeatOut(BaseModel):
    id: int
    name: str = ""
    persona: str = ""                # 席位**自身**的提示词（仅自定义分支用；不回传模板正文）
    style_id: Optional[int] = None   # 引用的风格模板 id；None=自定义
    style_name: str = ""             # 引用模板的名字（便于卡片/列表直接显示，省一次查询）
    mode: str = "manual"
    provider: str = ""
    model: str = ""
    role: str = ""
    enabled: int = 1
    has_key: bool = False            # 该席位 provider 是否已配置 Key（永不回传 Key 本身）
    sort: int = 0
    created_at: Optional[str] = None


class RoundtableStyleCreate(BaseModel):
    """新增风格模板（自定义）。builtin 只能由内置播种产生，不接受外部传入。"""
    name: str
    summary: str = ""
    persona: str = ""
    sort: Optional[int] = None


class RoundtableStyleUpdate(BaseModel):
    name: Optional[str] = None
    summary: Optional[str] = None
    persona: Optional[str] = None
    sort: Optional[int] = None


class RoundtableStyleOut(BaseModel):
    id: int
    name: str = ""
    summary: str = ""
    persona: str = ""
    builtin: int = 0                 # 1=内置（前端不可删除）
    sort: int = 0
    used_by: int = 0                 # 引用该模板的席位数（删除前提示用）
    created_at: Optional[str] = None


class RoundtablePostIn(BaseModel):
    seat_id: int
    content: str
    date: Optional[str] = None       # 缺省今天（北京时间）


class RoundtableArchiveReq(BaseModel):
    """补建论坛存档：指定席位（可选日期，缺省今天）把已录入的观点重新同步一次。"""
    seat_id: int
    date: Optional[str] = None


class RoundtablePostOut(BaseModel):
    seat_id: int
    seat_name: str = ""
    date: str = ""
    content: str = ""
    source: str = "manual"           # manual / auto
    model: str = ""
    thread_id: Optional[int] = None  # 同步到论坛的存档主题帖 id（无则为 None）
    updated_at: Optional[str] = None


class RoundtableSettingsIn(BaseModel):
    provider: str
    api_key: Optional[str] = None    # 传空串表示清除已配置的 Key
    base_url: Optional[str] = None
    model: Optional[str] = None
    delete: bool = False             # True = 删除该提供方的全部配置（仅自定义提供方，内置项拒绝）


class RoundtableGenerateReq(BaseModel):
    date: Optional[str] = None
    seat_ids: Optional[list[int]] = None   # 缺省全部可自动生成的席位


class RoundtableReviewReq(BaseModel):
    """手动触发观点复盘：date 指定单日；days 指定补批回溯天数；都不给则跑每日（昨日+近窗补算）。"""
    date: Optional[str] = None
    days: Optional[int] = None


class RoundtableAnalyzeReq(BaseModel):
    """手动触发观点分析：date 指定日期（缺省当日）——汇总该日全部席位观点交 LLM 交叉分析。"""
    date: Optional[str] = None


# ================= 论坛 =================
# 身份：user=用户本人（author_type=user）/ agent=AI 席位（author_type=agent + seat_id，仅管理员）
class ForumThreadCreate(BaseModel):
    title: str
    content: str = ""                # 首帖内容（可为空，仅发标题）
    author_type: str = "user"
    seat_id: Optional[int] = None    # author_type=agent 时的席位 id


class ForumPostCreate(BaseModel):
    content: str
    author_type: str = "user"
    seat_id: Optional[int] = None


class ForumPostEdit(BaseModel):
    content: str                    # 编辑后的正文（Markdown 原文）


class ForumThreadEdit(BaseModel):
    title: str                      # 编辑后的标题


class ForumPostOut(BaseModel):
    id: int
    thread_id: int
    author_type: str = "user"
    author_id: Optional[int] = None
    author_name: str = ""            # 用户=邮箱前缀 / Agent=席位名
    author_role: str = ""            # Agent 的 AI 角色（用户为空）
    content: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    edited: bool = False             # 改过（updated_at 非 NULL）即标「已编辑」
    can_delete: bool = False         # 本人或管理员可删
    can_edit: bool = False           # 本人或管理员可改


class ForumThreadOut(BaseModel):
    id: int
    title: str = ""
    author_type: str = "user"
    author_name: str = ""
    author_role: str = ""
    excerpt: str = ""                # 首帖摘要（列表预览用，截断 120 字）
    reply_count: int = 0             # 跟帖数（不含首帖）
    agent_count: int = 0             # 参与讨论的 Agent 数（按席位去重）
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    can_delete: bool = False
    can_edit: bool = False


class ForumThreadDetail(BaseModel):
    thread: ForumThreadOut
    posts: list[ForumPostOut] = []


# ================= 周期结算帖（日 / 周 / 月 / 季 / 半年 / 年） =================
class DailyReportReq(BaseModel):
    date: Optional[str] = None       # 缺省今天（北京时间）；补发历史日报时才传


class SettleReportReq(BaseModel):
    period: str = "day"              # day / week / month / quarter / half / year
    key: Optional[str] = None        # 周期键（2026-W42 / 2026-09 / 2026-Q3 …）；留空 = 最近一个已结束周期


# ================= 资讯工作台 =================
class NewsFeedCreate(BaseModel):
    url: str
    title: str = ""
    category: str = ""
    backup_urls: str = ""


class NewsOpmlIn(BaseModel):
    """OPML 导入：由前端读取文件/粘贴后把**原文**发上来。

    刻意不走「传 OPML 地址让服务端去取」——本机只有 IPv6 出站，任何 OPML 地址都得
    先进中继白名单才能取；而浏览器侧读文件不受此限，绕开了白名单与网络两重耦合。
    """
    content: str
    category: str = ""
    fetch: bool = True          # 导入后立即实抓一次，当场暴露僵尸源


class NewsFeedUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    enabled: Optional[int] = None
    sort: Optional[int] = None
    backup_urls: Optional[str] = None


class NewsFeedOut(BaseModel):
    id: int
    title: str = ""
    url: str = ""
    backup_urls: str = ""
    site_url: str = ""
    category: str = ""
    enabled: int = 1
    sort: int = 0
    last_fetched: Optional[str] = None
    last_status: str = ""
    last_error: str = ""
    item_count: int = 0                # 该源已入库条目数（实时统计）
    created_at: Optional[str] = None


class NewsItemOut(BaseModel):
    id: int
    feed_id: int
    feed_title: str = ""
    title: str = ""
    link: str = ""
    source_name: str = ""              # 原始来源名（如 MIT News）
    category: str = ""                 # 条目分类（如 论文 / AI 产品）
    summary: str = ""
    date: str = ""                     # 北京时间 YYYY-MM-DD（服务端算好，前端直接分组）
    time: str = ""                     # 北京时间 HH:MM
    published_at: Optional[str] = None # UTC naive ISO（留档用）
    star: int = 0
    ai_score: Optional[int] = None     # AI 重要度 1-5（NULL = 未加工）
    ai_reason: str = ""
    ai_tags: str = ""
    ai_codes: str = ""                 # AI 提取的关联标的代码（逗号分隔）
    ai_dup_of: Optional[int] = None    # 同题主条目 id（多源合并）
    related: int = 0                   # 是否与当前用户持仓相关（后端：代码 ∩ ai_codes ∪ 行业画像关键词）


class NewsDigestReq(BaseModel):
    date: Optional[str] = None         # 北京日期 YYYY-MM-DD，留空 = 今天
    dry_run: bool = False              # 只生成正文不发布


class NewsScoreReq(BaseModel):
    limit: int = 40                    # 本次最多加工多少条（定时脚本用 --limit 覆盖）


class NewsFetchReq(BaseModel):
    feed_id: Optional[int] = None      # 缺省抓取全部启用的源
    limit: Optional[int] = 60          # 每源最多入库条数（防止首抓灌爆）


class NewsSettingsIn(BaseModel):
    relay_url: Optional[str] = None    # 中继地址，如 https://relay.kapibala.icu
    relay_key: Optional[str] = None    # 中继密钥（传空串表示清除）


# ---------------- v2 主事工作台：目标配置层 ----------------
class ChiefWeightItem(BaseModel):
    """标的级目标权重：weight 为百分比，0 表示「该标的未配置目标」。"""
    project_id: int
    weight: float = 0.0


class ChiefWeightsIn(BaseModel):
    items: List[ChiefWeightItem] = []


class ChiefGoalOut(BaseModel):
    id: int
    kind: str = ""
    key: str = ""
    value: str = ""
    op: str = ""
    note: str = ""
    source_quote: str = ""


# ---------------- v2 主事工作台：纪律规则（A2）----------------
class ChiefRuleItem(BaseModel):
    """一条纪律规则：**结构化条目**（作用范围 + 比较符 + 阈值）。

    scope 编码与作用一致：any_pos / any_cat / pos:<id> / cat:<名称>
    op：le=不超过（占比上限），ge=不低于（占比下限）
    value：阈值百分比，<=0 视为未配置（不落库）
    自由文本类约束走 kind=preference（A3），不进这张表。
    """
    scope: str = "any_pos"
    op: str = "le"
    value: float = 0.0
    note: str = ""


class ChiefRulesIn(BaseModel):
    items: List[ChiefRuleItem] = []
    sort: int = 0


# ---------------- v2 主事工作台：风险偏好 / 约束（A3）----------------
class ChiefPrefIn(BaseModel):
    """风险偏好 / 约束（A3）：自由文本偏好 + 是否允许换仓开关。

    自由文本承载「最大回撤容忍」「风格偏好」「不接受杠杆」等无法结构化的约束；
    开关控制主事能否在建议里提出新增/减持标的（关闭则只评估当前持仓）。
    这两个也作为主事提示词的上下文头（B 阶段装配）。
    """
    note: str = ""
    allow_rebalance: bool = False
