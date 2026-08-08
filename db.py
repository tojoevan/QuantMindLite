"""SQLite 数据层：项目 / 行情 / 预测(建议) / 反馈(实际操作)。
独立 SQLite，不触碰 QuantMind 的 Postgres。"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, ForeignKey, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "quantweb.db")

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
