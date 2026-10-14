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
    days: Optional[int] = 60        # 回看交易日数（约），默认 60
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
    mode: str = "manual"             # manual / auto
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


class RoundtableGenerateReq(BaseModel):
    date: Optional[str] = None
    seat_ids: Optional[list[int]] = None   # 缺省全部可自动生成的席位
