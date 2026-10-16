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


class RoundtableSeat(Base):
    """圆桌讨论 · AI 席位（长期配置，可增删）。

    mode: manual=手动回填（页面复制提示词→外部咨询→粘贴反馈）；
          auto=自动席位（配置 API Key 后由后端直调 LLM 生成）。
    """
    __tablename__ = "roundtable_seats"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)           # 席位名，如「稳健配置」
    persona = Column(Text, default="")                  # 人格 / 风格提示词
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


class DailyReport(Base):
    """每日持仓/收益日报 → 论坛帖的锚点。

    存在理由：日报由定时任务每天自动发布，重复执行（手动重跑 / 当天补数后又跑一次）
    必须落到**同一个主题帖**，否则论坛会被同日多帖刷满。date 作主键天然保证「每天一篇」。
    """
    __tablename__ = "daily_reports"
    date = Column(String(10), primary_key=True)         # YYYY-MM-DD（北京时间）
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

    ai_* 三列为 P7b「AI 识别关键信息」预留，本期只建列不写入。
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
    ai_score = Column(Integer, nullable=True)           # P7b 预留：AI 评分
    ai_reason = Column(Text, default="")                # P7b 预留：推荐理由
    ai_tags = Column(String(200), default="")           # P7b 预留：标签（逗号分隔）


class Setting(Base):
    """站点级键值设置（如 LLM API Key：llm.<provider>.api_key / .base_url / .model）。"""
    __tablename__ = "settings"
    key = Column(String(128), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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

# roundtable_seats 补列（role）
_cols_rt = [c["name"] for c in _insp.get_columns("roundtable_seats")]
_for_add_rt = {
    "role": "ALTER TABLE roundtable_seats ADD COLUMN role VARCHAR(64) DEFAULT ''",
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
