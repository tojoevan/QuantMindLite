"""SQLite 数据层：项目 / 行情 / 预测(建议) / 反馈(实际操作)。
独立 SQLite，不触碰 QuantMind 的 Postgres。"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, ForeignKey, DateTime, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 允许用环境变量指向别的库文件（离线脚本/副本试跑用；线上不设该变量 → 仍是项目内 quantweb.db）
DB_PATH = os.environ.get("QUANTWEB_DB") or os.path.join(BASE_DIR, "quantweb.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)          # 项目名称
    code = Column(String(32), default="")               # 标的代码，如 600519.SH
    market = Column(String(16), default="A")            # A / HK / US
    strategy = Column(Text, default="")                 # 策略说明（自由文本）
    strategy_type = Column(String(32), default="momentum")  # momentum / meanreversion / breakout / baseline
    strategy_config = Column(Text, default="{}")         # 策略参数（JSON 字符串）
    bias = Column(String(8), default="neutral")         # long / short / neutral 倾向
    position_shares = Column(Float, default=0.0)        # 当前持仓数量（用户输入的种子仓位）
    position_cost = Column(Float, default=0.0)          # 当前持仓成本价（用户输入）
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # 项目归属用户（多用户隔离）
    last_fetch_date = Column(String(10), default="")           # 游客自动拉取行情的“当天已拉取”标记（每天限一次）
    pinned = Column(Integer, default=0)                        # 置顶：1 置顶（列表排序优先）
    created_at = Column(DateTime, default=datetime.utcnow)


class MarketPrice(Base):
    """实际行情（真实线）。可由数据源同步或手动录入。"""
    __tablename__ = "market_prices"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    date = Column(String(10), nullable=False)           # YYYY-MM-DD
    close = Column(Float, nullable=False)


class Prediction(Base):
    """系统给出的操作建议（含预测价/区间/置信度）。"""
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    date = Column(String(10), nullable=False)
    signal = Column(String(8), nullable=False)          # BUY / SELL / HOLD
    predicted_price = Column(Float)
    predicted_low = Column(Float)
    predicted_high = Column(Float)
    confidence = Column(Float, default=0.5)
    model = Column(String(64), default="baseline")      # baseline / quantmind-qlib
    note = Column(Text, default="")


class Feedback(Base):
    """用户实际执行并回填的操作（建议→反馈闭环的核心）。"""
    __tablename__ = "feedbacks"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    prediction_id = Column(Integer, nullable=True)
    date = Column(String(10), nullable=False)
    action = Column(String(8), nullable=False)          # BUY / SELL / HOLD
    price = Column(Float, nullable=False)
    qty = Column(Float, default=0.0)
    note = Column(Text, default="")
    realized_pnl = Column(Float, default=0.0)           # 该笔卖出实现的盈亏


class User(Base):
    """站点用户：用户名为邮箱地址，首个注册用户为管理员。"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, default=0)               # 0 / 1
    disabled = Column(Integer, default=0)               # 0 / 1，禁用后无法登录
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    """登录会话：Bearer token ↔ 用户，支持登出。"""
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class PasswordResetRequest(Base):
    """“忘记密码”申请：用户提交后由管理员处理并重置为随机密码。"""
    __tablename__ = "password_reset_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    email = Column(String(255), nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(16), default="pending")      # pending / handled
    handled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    handled_at = Column(DateTime, nullable=True)


class RoundtableStyle(Base):
    """圆桌讨论 · 风格模板（人格 / 风格提示词的可复用模板）。

    与席位**解耦**：席位用 style_id 引用模板，改模板 → 所有引用它的席位即时生效；
    席位上的 persona 退化为「自定义」分支（style_id 为空时才使用，兼容历史数据）。
    生效值的解析唯一落在 app._resolve_persona()，别在别处另写一份。

    builtin_key：内置模板的**稳定标识**（如 value / trend），自定义模板为空串。
    内置模板靠它判断幂等播种——用户改了模板名也不会被重新播种出一份重复的。
    """
    __tablename__ = "roundtable_styles"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)           # 模板名，如「价值投资」
    summary = Column(String(200), default="")           # 一句话定位（列表里显示，不必展开正文）
    persona = Column(Text, default="")                  # 提示词正文
    builtin = Column(Integer, default=0)                # 1=内置（不可删除，可改名改内容）
    builtin_key = Column(String(32), default="")        # 内置标识；自定义为空
    sort = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class RoundtableSeat(Base):
    """圆桌讨论 · AI 席位（长期配置，可增删）。

    mode: manual=手动回填（页面复制提示词→外部咨询→粘贴反馈）；
          auto=自动席位（配置 API Key 后由后端直调 LLM 生成）。
    """
    __tablename__ = "roundtable_seats"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)           # 席位名，如「稳健配置」
    persona = Column(Text, default="")                  # 人格 / 风格提示词（仅 style_id 为空时生效）
    style_id = Column(Integer, nullable=True)           # 引用的风格模板 id；NULL=自定义（用上面的 persona）
    mode = Column(String(8), default="manual")          # 接入方式：manual / auto / mcp（mcp 本期仅标注，行为同 manual）
    provider = Column(String(32), default="deepseek")   # LLM 提供方（auto 席位用）
    model = Column(String(64), default="")              # 模型名（留空用提供方默认）
    enabled = Column(Integer, default=1)                # 0 / 1
    sort = Column(Integer, default=0)                   # 展示顺序
    role = Column(String(64), default="")               # AI 角色（豆包/DeepSeek/元宝等），手动席位身份标签
    created_at = Column(DateTime, default=datetime.utcnow)


class RoundtablePost(Base):
    """圆桌讨论 · 每日观点（按天归档，每席位每天一条，重复提交覆盖更新）。"""
    __tablename__ = "roundtable_posts"
    __table_args__ = (UniqueConstraint("date", "seat_id", name="uq_rt_post_date_seat"),)
    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)           # YYYY-MM-DD（讨论日期）
    seat_id = Column(Integer, ForeignKey("roundtable_seats.id"), nullable=False)
    content = Column(Text, default="")                  # 观点内容（结构化纯文本）
    source = Column(String(8), default="manual")        # manual=人工粘贴 / auto=LLM 生成
    model = Column(String(64), default="")              # auto 时记录实际模型名
    thread_id = Column(Integer, nullable=True)          # 同步到论坛的存档主题帖（forum_threads.id），写入时自动创建
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RtReview(Base):
    """圆桌观点复盘（G3，2026-10-17）：把每日观点里的「标的 + 方向」判断抽取成结构化
    记录，事后用真实行情（MarketPrice，股票/基金同一张表）机械判定兑现/落空。

    设计纪律：**LLM 只负责文本→结构化抽取，判定全部机械**（方向 × T+N 实际收益），
    否则复盘只是让模型再编一个故事，违背平台「可解释、人在回路」的底线。
    判定四态：hit=兑现 / miss=落空 / flat=无效波动（|收益|<0.5%，不计入命中率分母）/
    pending=行情未到期或净值未入库。hold/observe 方向无法用收益判对错，一律 flat。
    """
    __tablename__ = "rt_reviews"
    __table_args__ = (UniqueConstraint("date", "seat_id", "code",
                                       name="uq_rt_review_date_seat_code"),)
    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)            # 观点日期 YYYY-MM-DD
    seat_id = Column(Integer, ForeignKey("roundtable_seats.id"), nullable=False)
    code = Column(String(16), nullable=False)            # 归一化代码（newsai.norm_code）
    name = Column(String(64), default="")
    direction = Column(String(8), default="")            # up / down / hold / observe
    basis_news_ids = Column(String(80), default="")      # 判断依据的资讯条目 id（逗号分隔）
    confidence = Column(String(16), default="")          # 观点自述置信度（可空）
    ret_t1 = Column(Float, nullable=True)                # 基准日→T+1 收益 %
    ret_t5 = Column(Float, nullable=True)                # 基准日→T+5 收益 %
    verdict_t1 = Column(String(8), default="")           # hit / miss / flat / pending
    verdict_t5 = Column(String(8), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RtAnalysis(Base):
    """圆桌观点分析（2026-10-19）：把某日全部启用席位的观点汇总，交给「AI 设置」
    配置的 LLM（默认 DeepSeek，与观点复用的抽取同源）做交叉分析——
    共识要点 / 分歧焦点 / 风险提示 / 综合结论。

    与观点复盘（RtReview）的分工：复盘是「单条判断 × 真实行情」的机械兑现
    （事后、判对错），本表是「多条观点之间」的横向汇总，不做对错判断、
    不依赖事后行情。每日期一条，重复生成覆盖同日期行（upsert）——
    它是衍生品，历史版本没有回看价值，源观点始终在 roundtable_posts。
    """
    __tablename__ = "rt_analyses"
    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)           # 观点日期 YYYY-MM-DD
    content = Column(Text, default="")                  # 分析正文（行式结构，前端 prewrap 渲染）
    provider = Column(String(32), default="")           # 实际使用的服务商（news.llm_provider）
    model = Column(String(64), default="")              # 实际模型名
    seat_count = Column(Integer, default=0)             # 汇总的席位观点条数
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ForumThread(Base):
    """论坛 · 主题帖（发起人可为用户或 Agent）。

    author_type: user=用户（author_id 为 users.id）/ agent=AI 席位（author_id 为 roundtable_seats.id）。
    显示名在读取时按当前用户/席位实时解析，因此席位改名或用户变更会同步体现，不做快照。
    """
    __tablename__ = "forum_threads"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)          # 主题标题
    author_type = Column(String(8), default="user")      # user / agent
    author_id = Column(Integer, nullable=True)           # user: users.id / agent: roundtable_seats.id
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)  # 有新跟帖时刷新


class ForumPost(Base):
    """论坛 · 跟帖（首帖与主题同时创建；每条带身份：用户 / Agent 角色）。"""
    __tablename__ = "forum_posts"
    id = Column(Integer, primary_key=True)
    thread_id = Column(Integer, ForeignKey("forum_threads.id"), nullable=False)
    author_type = Column(String(8), default="user")      # user / agent
    author_id = Column(Integer, nullable=True)
    content = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)         # 编辑后刷新；NULL = 从未编辑
    edited = Column(Integer, default=0)                 # 编辑次数（>0 表示改过，前端标「已编辑」）


class SettleReport(Base):
    """周期结算帖（日 / 周 / 月 / 季 / 半年 / 年）→ 论坛帖的锚点。

    存在理由：结算帖由定时任务自动发布，重复执行（手动重跑 / 当天补数后又跑一次）
    必须落到**同一个主题帖**，否则论坛会被同周期多帖刷满。
    (period, pkey) 唯一约束天然保证「每个周期一篇」。

    period: day / week / month / quarter / half / year
    pkey:   2026-10-17 / 2026-W42 / 2026-10 / 2026-Q4 / 2026-H2 / 2026

    本表取代了早期的 daily_reports（只有日粒度）—— 旧表数据在文件尾部
    由 _migrate_daily_reports() 幂等搬进来，旧表本身保留不动。
    """
    __tablename__ = "settle_reports"
    __table_args__ = (UniqueConstraint("period", "pkey", name="uq_settle_period_key"),)
    id = Column(Integer, primary_key=True)
    period = Column(String(16), nullable=False)         # day / week / month / quarter / half / year
    pkey = Column(String(32), nullable=False)           # 周期键，见类文档
    thread_id = Column(Integer, nullable=True)          # 对应论坛主题帖（forum_threads.id）
    updates = Column(Integer, default=0)                # 已写入层数（含首帖）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NewsFeed(Base):
    """资讯工作台 · 订阅源（RSS 2.0 / Atom）。

    抓取走 Cloudflare 中继（见 newsfetch.py）：本机 VPS 只有 IPv6 出站，
    而多数资讯源仅有 A 记录，直连必然失败。
    """
    __tablename__ = "news_feeds"
    __table_args__ = (UniqueConstraint("url", name="uq_news_feed_url"),)
    id = Column(Integer, primary_key=True)
    title = Column(String(200), default="")             # 源名称（抓取后由 feed 自报覆盖）
    url = Column(String(600), nullable=False)           # Feed URL（唯一）
    backup_urls = Column(Text, default="")               # 备用 URL（逗号分隔，多实例容灾；主 URL 失败时依次尝试）
    site_url = Column(String(600), default="")          # 站点主页
    category = Column(String(32), default="")           # 人工分类：AI 动态 / 科技 / 政策 …
    enabled = Column(Integer, default=1)                # 0 / 1
    sort = Column(Integer, default=0)                   # 展示顺序
    last_fetched = Column(DateTime, nullable=True)      # 最近一次抓取时间（UTC）
    last_status = Column(String(16), default="")        # ok / empty / error
    last_error = Column(Text, default="")               # 失败原因或空源说明（截断存）
    created_at = Column(DateTime, default=datetime.utcnow)


class NewsItem(Base):
    """资讯工作台 · 条目。按 (feed_id, guid) 去重，重复抓取只更新不新增。

    ai_* 列为「AI 结构化加工」（newsai.py）的落点：抓取后由定时任务批量送 LLM，
    产出重要度评分 / 推荐理由 / 标签 / 关联标的代码 / 同题合并关系。
    源站字段刷新时**绝不覆盖** ai_*（见 newsfetch.refresh_feed）。
    """
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("feed_id", "guid", name="uq_news_item_feed_guid"),)
    id = Column(Integer, primary_key=True)
    feed_id = Column(Integer, ForeignKey("news_feeds.id"), nullable=False)
    guid = Column(String(255), nullable=False)          # 源内唯一 id（缺省用 link 或标题哈希）
    title = Column(String(500), default="")
    link = Column(String(900), default="")
    author = Column(String(200), default="")            # 原始 author 字段
    source_name = Column(String(200), default="")       # 从 author 括号中提取的原始来源名
    category = Column(String(64), default="")           # 条目自带分类（如 论文 / AI 产品）
    summary = Column(Text, default="")                  # 去 HTML 的纯文本摘要
    content = Column(Text, default="")                  # 原始 HTML（留档）
    published_at = Column(DateTime, nullable=True, index=True)  # 发布时间（UTC naive）
    fetched_at = Column(DateTime, default=datetime.utcnow)
    star = Column(Integer, default=0)                   # 收藏：0 / 1（站点级，单用户为主）
    ai_score = Column(Integer, nullable=True)           # AI 重要度 1-5（NULL = 未加工）
    ai_reason = Column(Text, default="")                # AI 一句话推荐理由
    ai_tags = Column(String(200), default="")           # AI 标签（逗号分隔）
    ai_codes = Column(String(200), default="")          # AI 提取的关联标的代码（逗号分隔，已归一化）
    ai_dup_of = Column(Integer, nullable=True)          # 同题主条目 id（多源报道合并；NULL = 主条目）


class NewsInjectLog(Base):
    """资讯注入席位提示词的日志 —— 防同一事件连续多日重复喂。

    规则：某条目**上次注入日期 < 今天**才允许再次注入（同一天重复生成/试跑仍带同一批），
    第二天起让位给新条目。没有这张表就会出现「一个政策连读三天」。

    pos：该条目当日注入的序号（提示词里「资讯 N」的 N，1 起）。rtreview 复盘时
    靠它把观点里的「资讯 N」还原成真实条目——没有顺序信息，引用就无法追溯。
    """
    __tablename__ = "news_inject_log"
    __table_args__ = (UniqueConstraint("date", "item_id", name="uq_news_inject_date_item"),)
    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False, index=True)   # 北京时间 YYYY-MM-DD
    item_id = Column(Integer, ForeignKey("news_items.id"), nullable=False)
    pos = Column(Integer, default=0)                        # 当日注入序号（提示词「资讯 N」）
    created_at = Column(DateTime, default=datetime.utcnow)


class NewsDigest(Base):
    """市场概要帖锚点（每天一条 → 论坛主题帖），与 SettleReport 同构。"""
    __tablename__ = "news_digests"
    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False, unique=True)  # 北京时间 YYYY-MM-DD
    thread_id = Column(Integer, nullable=True)          # 对应论坛主题帖（forum_threads.id）
    updates = Column(Integer, default=0)                # 已写入层数（含首帖）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Setting(Base):
    """站点级键值设置（如 LLM API Key：llm.<provider>.api_key / .base_url / .model）。"""
    __tablename__ = "settings"
    key = Column(String(128), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LLMCallLog(Base):
    """LLM 调用日志（圆桌自动生成 / 测试入口），一次调用一行，供「生成健康度」统计。

    error_kind 分类（app._llm_error_kind）：
      ok / budget        —— 正文为空且 finish=length（推理模型思考耗尽 max_tokens）
      empty              —— 正文为空但 finish 不是 length
      http4xx / http5xx  —— 源站或中继的 HTTP 错误（403 forbidden 多为中继密钥错）
      network            —— 连接 / DNS / 超时（本机 IPv6-only 环境下的高发类）
      other              —— 其余
    thinking_fallback=1 表示触发了「超预算自动关思考兜底」（llm.py 内实现）。
    """
    __tablename__ = "llm_call_log"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    seat_id = Column(Integer, nullable=True)
    seat_name = Column(String(200), default="")
    provider = Column(String(64), default="")
    model = Column(String(128), default="")
    ok = Column(Integer, default=0)                     # 0 / 1
    error_kind = Column(String(32), default="")         # 见 docstring
    error = Column(Text, default="")                    # 原始错误串（截断 500）
    finish_reason = Column(String(16), default="")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    reasoning_tokens = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    via_relay = Column(Integer, default=0)              # 是否走了中继
    thinking = Column(String(16), default="")           # off / on
    thinking_fallback = Column(Integer, default=0)      # 是否触发超预算兜底


Base.metadata.create_all(engine)

# ---- 兼容已存在的数据库：补加新列（SQLite 不支持 ALTER ADD COLUMN 进 create_all 自动迁移）----
from sqlalchemy import inspect, text
_insp = inspect(engine)
_cols = [c["name"] for c in _insp.get_columns("projects")]
_for_add = {
    "strategy_type": "ALTER TABLE projects ADD COLUMN strategy_type VARCHAR(32) DEFAULT 'momentum'",
    "strategy_config": "ALTER TABLE projects ADD COLUMN strategy_config TEXT DEFAULT '{}'",
    "position_shares": "ALTER TABLE projects ADD COLUMN position_shares FLOAT DEFAULT 0.0",
    "position_cost": "ALTER TABLE projects ADD COLUMN position_cost FLOAT DEFAULT 0.0",
    "owner_id": "ALTER TABLE projects ADD COLUMN owner_id INTEGER",
    "last_fetch_date": "ALTER TABLE projects ADD COLUMN last_fetch_date VARCHAR(10) DEFAULT ''",
    "pinned": "ALTER TABLE projects ADD COLUMN pinned INTEGER DEFAULT 0",
}
for _cname, _ddl in _for_add.items():
    if _cname not in _cols:
        with engine.connect() as _conn:
            _conn.execute(text(_ddl))
            _conn.commit()

# news_items 的 ai_* 加工列（2026-10-17 资讯 AI 结构化加工）：老库补两列，
# 已有的 ai_score/ai_reason/ai_tags 三列早已存在（P7b 预留），不用补。
_news_cols = [c["name"] for c in _insp.get_columns("news_items")]
for _cname, _ddl in {
    "ai_codes": "ALTER TABLE news_items ADD COLUMN ai_codes VARCHAR(200) DEFAULT ''",
    "ai_dup_of": "ALTER TABLE news_items ADD COLUMN ai_dup_of INTEGER",
}.items():
    if _cname not in _news_cols:
        with engine.connect() as _conn:
            _conn.execute(text(_ddl))
            _conn.commit()

# news_inject_log 的 pos 列（2026-10-17 G3 复盘）：老库注入记录无顺序信息，
# 补列后 pos=0（复盘时按 pos 排序，0 视为未知序，仍可用 id 近似）。
_inj_cols = [c["name"] for c in _insp.get_columns("news_inject_log")]
if "pos" not in _inj_cols:
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE news_inject_log ADD COLUMN pos INTEGER DEFAULT 0"))
        _conn.commit()

# roundtable_seats 补列（role / style_id）
_cols_rt = [c["name"] for c in _insp.get_columns("roundtable_seats")]
_for_add_rt = {
    "role": "ALTER TABLE roundtable_seats ADD COLUMN role VARCHAR(64) DEFAULT ''",
    # 风格模板引用：NULL = 自定义（沿用席位自身 persona，历史数据即此状态）
    "style_id": "ALTER TABLE roundtable_seats ADD COLUMN style_id INTEGER",
}
for _cname, _ddl in _for_add_rt.items():
    if _cname not in _cols_rt:
        with engine.connect() as _conn:
            _conn.execute(text(_ddl))
            _conn.commit()

# roundtable_posts 补列（thread_id：观点 → 论坛存档帖的关联）
_cols_rtp = [c["name"] for c in _insp.get_columns("roundtable_posts")]
_for_add_rtp = {
    "thread_id": "ALTER TABLE roundtable_posts ADD COLUMN thread_id INTEGER",
}
for _cname, _ddl in _for_add_rtp.items():
    if _cname not in _cols_rtp:
        with engine.connect() as _conn:
            _conn.execute(text(_ddl))
            _conn.commit()

# forum_posts 补列（编辑功能：updated_at / edited）——旧行两列均为 NULL/0，
# _forum_post_out 以 `edited or 0` 兜底，不会因旧行缺列而报错。
_cols_fp = [c["name"] for c in _insp.get_columns("forum_posts")]
_for_add_fp = {
    "updated_at": "ALTER TABLE forum_posts ADD COLUMN updated_at TIMESTAMP",
    "edited": "ALTER TABLE forum_posts ADD COLUMN edited INTEGER DEFAULT 0",
}
for _cname, _ddl in _for_add_fp.items():
    if _cname not in _cols_fp:
        with engine.connect() as _conn:
            _conn.execute(text(_ddl))
            _conn.commit()


# ---- 迁移：早期 daily_reports（只有日粒度）→ settle_reports(period='day') ----
# 为什么必须搬：settle_reports 是「同周期只认一个帖」的唯一依据，不搬的话
# 已发过的日报会**再发一遍**（同一天多帖）。
# 幂等：靠 (period, pkey) 唯一约束 INSERT OR IGNORE，重复启动无副作用。
# 旧表**保留不动**（不删不改），确认无误后可人工清理。
def _migrate_daily_reports() -> int:
    _now = datetime.utcnow().isoformat(sep=" ", timespec="microseconds")
    with engine.connect() as conn:
        legacy = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_reports'"
        )).fetchone()
        if not legacy:
            return 0
        rows = conn.execute(text(
            "SELECT date, thread_id, updates FROM daily_reports")).fetchall()
        moved = 0
        for d, tid, upd in rows:
            r = conn.execute(text(
                "INSERT OR IGNORE INTO settle_reports"
                "(period, pkey, thread_id, updates, created_at, updated_at) "
                "VALUES ('day', :k, :t, :u, :now, :now)"
            ), {"k": d, "t": tid, "u": int(upd or 0), "now": _now})
            moved += r.rowcount or 0
        conn.commit()
        return moved


try:
    _MIGRATED_DAILY = _migrate_daily_reports()
except Exception:            # 迁移失败不阻断启动：settle_reports 缺记录只会导致同一天多帖，不会丢数据
    _MIGRATED_DAILY = 0


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
