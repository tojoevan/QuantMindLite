"""Pydantic 请求/响应模型。"""
from typing import Optional
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
    mode: str = "manual"             # 接入方式：manual 手动回填 / auto LLM Key 自动 / mcp（本期仅标注）
    provider: str = "deepseek"
    model: str = ""
    role: str = ""                   # AI 角色（豆包/DeepSeek/元宝等），手动席位身份标签


class RoundtableSeatUpdate(BaseModel):
    name: Optional[str] = None
    persona: Optional[str] = None
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    role: Optional[str] = None
    enabled: Optional[int] = None
    sort: Optional[int] = None


class RoundtableSeatOut(BaseModel):
    id: int
    name: str = ""
    persona: str = ""
    mode: str = "manual"
    provider: str = ""
    model: str = ""
    role: str = ""
    enabled: int = 1
    has_key: bool = False            # 该席位 provider 是否已配置 Key（永不回传 Key 本身）
    sort: int = 0
    created_at: Optional[str] = None


class RoundtablePostIn(BaseModel):
    seat_id: int
    content: str
    date: Optional[str] = None       # 缺省今天（北京时间）


class RoundtablePostOut(BaseModel):
    seat_id: int
    seat_name: str = ""
    date: str = ""
    content: str = ""
    source: str = "manual"           # manual / auto
    model: str = ""
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


class ForumPostOut(BaseModel):
    id: int
    thread_id: int
    author_type: str = "user"
    author_id: Optional[int] = None
    author_name: str = ""            # 用户=邮箱前缀 / Agent=席位名
    author_role: str = ""            # Agent 的 AI 角色（用户为空）
    content: str = ""
    created_at: Optional[str] = None
    can_delete: bool = False         # 本人或管理员可删


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


class ForumThreadDetail(BaseModel):
    thread: ForumThreadOut
    posts: list[ForumPostOut] = []


# ================= 资讯工作台 =================
class NewsFeedCreate(BaseModel):
    url: str
    title: str = ""
    category: str = ""


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


class NewsFeedOut(BaseModel):
    id: int
    title: str = ""
    url: str = ""
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
    ai_score: Optional[int] = None     # P7b 预留
    ai_reason: str = ""
    ai_tags: str = ""


class NewsFetchReq(BaseModel):
    feed_id: Optional[int] = None      # 缺省抓取全部启用的源
    limit: Optional[int] = 60          # 每源最多入库条数（防止首抓灌爆）


class NewsSettingsIn(BaseModel):
    relay_url: Optional[str] = None    # 中继地址，如 https://relay.kapibala.icu
    relay_key: Optional[str] = None    # 中继密钥（传空串表示清除）
