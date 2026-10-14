"""慢量化个人操作 Web —— 建议→反馈→再预测 决策台后端。
FastAPI + SQLite，独立运行，对接 QuantMind 预测（可插拔）。
"""
import os
import re
import json
import logging
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from pydantic import BaseModel

logger = logging.getLogger("quant-web")

from db import (
    engine, SessionLocal, Project, MarketPrice, Prediction, Feedback,
    User, Session as SessionModel, PasswordResetRequest,
    RoundtableSeat, RoundtablePost, Setting,
)
from models import (
    ProjectCreate, ProjectOut, ProjectUpdate, PricePoint, PredictionCreate, PredictionOut,
    FeedbackCreate, FeedbackOut, ChartData, FetchReq, PriceOut,
    RoundtableSeatCreate, RoundtableSeatUpdate, RoundtableSeatOut,
    RoundtablePostIn, RoundtablePostOut, RoundtableSettingsIn, RoundtableGenerateReq,
)
from predictor import run_strategy, STRATEGY_CATALOG, rolling_predict, forecast_future
from datasource import fetch_a_hist, fetch_stock_name, fetch_index_quotes
from llm import chat_complete

app = FastAPI(title="慢量化操作台", version="0.1.0")


# ---- 静态资源禁用缓存：部署后浏览器立即拉取新前端，避免旧 app.js 缓存 ----
@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path in ("/app.js", "/style.css", "/index.html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ---- 多用户鉴权（邮箱为用户名，首个注册用户为管理员）----
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 游客模式：公开只读展示的标的（仅展示该代码的项目，无法增删/编辑）
GUEST_STOCK_CODE = "000002.SZ"

# 北京时间（UTC+8），用于“每天仅拉取一次行情”的日期判定（A股交易日按北京时间）
_CN_TZ_OFFSET = timedelta(hours=8)
def _cn_today():
    return (datetime.utcnow() + _CN_TZ_OFFSET).strftime("%Y-%m-%d")


# ---------- 密码哈希（pbkdf2，无第三方依赖） ----------
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return f"pbkdf2$100000${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), h)
    except Exception:
        return False


def gen_random_pw() -> str:
    chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    g = lambda: "".join(secrets.choice(chars) for _ in range(4))
    return f"{g()}-{g()}-{g()}"


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- 鉴权依赖（定义于 get_db 之后，见文件下方） ----------
def _issue_token(u: User, db: Session):
    token = secrets.token_hex(32)
    exp = datetime.utcnow() + timedelta(days=7)
    db.add(SessionModel(token=token, user_id=u.id, expires_at=exp))
    db.commit()
    return {"token": token, "email": u.email, "is_admin": bool(u.is_admin)}


# ---------- 鉴权请求模型 ----------
class AuthReq(BaseModel):
    email: str
    password: str


class ForgotReq(BaseModel):
    email: str


class MeOut(BaseModel):
    id: int
    email: str
    is_admin: bool


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- 鉴权依赖 ----------
def get_current_user(request: Request, db: Session = Depends(get_db)):
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        raise HTTPException(401, "unauthorized")
    token = h[7:]
    s = db.query(SessionModel).filter(SessionModel.token == token).first()
    if not s or s.expires_at < datetime.utcnow():
        if s:
            db.delete(s)
            db.commit()
        raise HTTPException(401, "unauthorized")
    u = db.get(User, s.user_id)
    if not u or u.disabled:
        raise HTTPException(401, "unauthorized")
    return u


def _project_or_404(pid, u, db):
    """取项目；普通用户只能访问自己的，管理员可访问全部。"""
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(404, "project not found")
    if not u.is_admin and p.owner_id != u.id:
        raise HTTPException(404, "project not found")
    return p


def _guest_project_guard(pid, db):
    """游客访问：仅允许访问代码为 GUEST_STOCK_CODE 的项目，其余一律 404。"""
    p = db.get(Project, pid)
    if not p or (p.code or "").strip().upper() != GUEST_STOCK_CODE:
        raise HTTPException(404, "project not found")
    return p


def _proj_out(p):
    return ProjectOut(
        id=p.id, name=p.name, code=p.code, market=p.market,
        strategy=p.strategy or "", strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias or "neutral",
        position_shares=p.position_shares, position_cost=p.position_cost,
        pinned=p.pinned or 0,
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


# ---------------- 盈亏回放 ----------------
def replay(feedbacks, seed_shares=0.0, seed_cost=0.0):
    """回放反馈序列，计算持仓与盈亏。
    seed_shares / seed_cost 为用户输入的“当前仓位”种子，
    可让已在持股、想接入系统跟踪的场景从该成本起算盈亏。"""
    fb = sorted(feedbacks, key=lambda f: (f.date, f.id))
    shares = float(seed_shares or 0.0)
    avg = float(seed_cost or 0.0)
    realized = 0.0
    out = []
    for f in fb:
        r = 0.0
        if f.action == "BUY" and f.qty > 0:
            ns = shares + f.qty
            if ns > 0:
                avg = (avg * shares + f.price * f.qty) / ns
            shares = ns
        elif f.action == "SELL" and f.qty > 0:
            sq = min(f.qty, shares)
            r = (f.price - avg) * sq
            realized += r
            shares -= sq
            if shares <= 1e-9:
                shares = 0.0
                avg = 0.0
        out.append((f, round(r, 2)))
    return out, dict(shares=round(shares, 4), avg=round(avg, 2), realized=round(realized, 2))


# ---------------- 健康检查 ----------------
@app.get("/api/health")
def health():
    return {"status": "healthy", "service": "quant-web"}


# ---------------- 多用户鉴权 ----------------
@app.post("/api/auth/register")
def register(body: AuthReq, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "邮箱格式不正确")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少需要 6 位")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "该邮箱已注册")
    is_first = db.query(User).count() == 0
    u = User(email=email, password_hash=hash_password(body.password), is_admin=1 if is_first else 0)
    db.add(u)
    db.flush()
    if is_first:
        # 首位注册用户（管理员）继承此前已有的全部项目
        db.query(Project).filter(Project.owner_id.is_(None)).update({Project.owner_id: u.id})
    db.commit()
    db.refresh(u)
    return _issue_token(u, db)


@app.post("/api/auth/login")
def login(body: AuthReq, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    u = db.query(User).filter(User.email == email).first()
    if not u or not verify_password(body.password, u.password_hash) or u.disabled:
        raise HTTPException(401, "邮箱或密码错误")
    return _issue_token(u, db)


@app.post("/api/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        db.query(SessionModel).filter(SessionModel.token == h[7:]).delete()
        db.commit()
    return {"ok": True}


@app.get("/api/auth/me", response_model=MeOut)
def me(u: User = Depends(get_current_user)):
    return MeOut(id=u.id, email=u.email, is_admin=bool(u.is_admin))


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotReq, db: Session = Depends(get_db)):
    """用户提交重置申请；不暴露邮箱是否存在，管理员在后台处理。"""
    email = body.email.strip().lower()
    u = db.query(User).filter(User.email == email).first()
    if u and not u.disabled:
        db.add(PasswordResetRequest(user_id=u.id, email=u.email))
        db.commit()
    return {"ok": True, "message": "已提交，请等待管理员重置密码"}


# ---------------- 管理员：用户与重置管理 ----------------
@app.get("/api/auth/admin/users")
def admin_list_users(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    rows = db.query(User).order_by(User.created_at).all()
    return [
        {"id": x.id, "email": x.email, "is_admin": bool(x.is_admin),
         "disabled": bool(x.disabled),
         "created_at": x.created_at.isoformat() if x.created_at else None}
        for x in rows
    ]


@app.get("/api/auth/admin/reset-requests")
def admin_list_requests(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    rows = (db.query(PasswordResetRequest)
            .filter(PasswordResetRequest.status == "pending")
            .order_by(PasswordResetRequest.requested_at.desc()).all())
    return [
        {"id": r.id, "user_id": r.user_id, "email": r.email,
         "requested_at": r.requested_at.isoformat() if r.requested_at else None}
        for r in rows
    ]


@app.post("/api/auth/admin/reset-password")
def admin_reset_password(body: dict, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    target = db.get(User, body.get("user_id"))
    if not target or target.disabled:
        raise HTTPException(404, "用户不存在")
    new_pw = gen_random_pw()
    target.password_hash = hash_password(new_pw)
    db.query(PasswordResetRequest).filter(
        PasswordResetRequest.user_id == target.id,
        PasswordResetRequest.status == "pending",
    ).update({"status": "handled", "handled_by": u.id, "handled_at": datetime.utcnow()})
    db.commit()
    return {"ok": True, "email": target.email, "new_password": new_pw}


@app.delete("/api/auth/admin/users/{uid}")
def admin_delete_user(uid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员删除用户：连带清理会话、密码重置申请，以及其名下项目与行情/预测/反馈。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    if uid == u.id:
        raise HTTPException(400, "不能删除当前登录的账户")
    target = db.get(User, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    # 防止删掉唯一的管理员，否则站点将无法再被管理
    if target.is_admin and db.query(User).filter(User.is_admin == 1).count() <= 1:
        raise HTTPException(400, "不能删除唯一的管理员账户")
    # 清理会话与密码重置申请
    db.query(SessionModel).filter(SessionModel.user_id == uid).delete()
    db.query(PasswordResetRequest).filter(PasswordResetRequest.user_id == uid).delete()
    # 清理名下项目及其子表（无级联约束，需手动删）
    proj_ids = [p.id for p in db.query(Project).filter(Project.owner_id == uid).all()]
    for pid in proj_ids:
        db.query(Feedback).filter(Feedback.project_id == pid).delete()
        db.query(Prediction).filter(Prediction.project_id == pid).delete()
        db.query(MarketPrice).filter(MarketPrice.project_id == pid).delete()
    db.query(Project).filter(Project.owner_id == uid).delete()
    db.delete(target)
    db.commit()
    return {"ok": True, "deleted": target.email}


# ---------------- 策略目录（前端配置 UI 用）----------------
@app.get("/api/strategies")
def strategies():
    return STRATEGY_CATALOG


# ---------------- 更新项目（含策略配置）----------------
@app.patch("/api/projects/{pid}", response_model=ProjectOut)
def update_project(pid: int, body: ProjectUpdate, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _project_or_404(pid, u, db)
    for field in ("name", "code", "market", "strategy",
                  "strategy_type", "strategy_config", "bias",
                  "position_shares", "position_cost", "pinned"):
        v = getattr(body, field)
        if v is not None:
            if field == "strategy_config" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            setattr(p, field, v)
    db.commit()
    db.refresh(p)
    return ProjectOut(
        id=p.id, name=p.name, code=p.code, market=p.market,
        strategy=p.strategy or "", strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias or "neutral",
        position_shares=p.position_shares, position_cost=p.position_cost,
        pinned=p.pinned or 0,
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


# ---------------- 项目 ----------------
@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Project)
    if not u.is_admin:
        q = q.filter(Project.owner_id == u.id)
    rows = q.order_by(Project.pinned.desc(), Project.created_at.desc()).all()
    return [
        ProjectOut(
            id=p.id, name=p.name, code=p.code, market=p.market,
            strategy=p.strategy or "", strategy_type=p.strategy_type,
            strategy_config=p.strategy_config, bias=p.bias or "neutral",
            position_shares=p.position_shares, position_cost=p.position_cost,
            pinned=p.pinned or 0,
            created_at=p.created_at.isoformat() if p.created_at else None,
        )
        for p in rows
    ]


@app.get("/api/projects/{pid}", response_model=ProjectOut)
def get_project(pid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _project_or_404(pid, u, db)
    return ProjectOut(
        id=p.id, name=p.name, code=p.code, market=p.market,
        strategy=p.strategy or "", strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias or "neutral",
        position_shares=p.position_shares, position_cost=p.position_cost,
        pinned=p.pinned or 0,
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cfg = body.strategy_config
    if isinstance(cfg, dict):
        cfg = json.dumps(cfg, ensure_ascii=False)
    p = Project(name=body.name, code=body.code, market=body.market,
                strategy=body.strategy, strategy_type=body.strategy_type,
                strategy_config=cfg if cfg is not None else "{}", bias=body.bias,
                position_shares=body.position_shares, position_cost=body.position_cost,
                owner_id=u.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return ProjectOut(
        id=p.id, name=p.name, code=p.code, market=p.market,
        strategy=p.strategy or "", strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias or "neutral",
        position_shares=p.position_shares, position_cost=p.position_cost,
        pinned=p.pinned or 0,
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _project_or_404(pid, u, db)
    db.query(Feedback).filter(Feedback.project_id == pid).delete()
    db.query(Prediction).filter(Prediction.project_id == pid).delete()
    db.query(MarketPrice).filter(MarketPrice.project_id == pid).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---------------- 行情（实际线） ----------------
@app.post("/api/projects/{pid}/prices", status_code=201)
def add_price(pid: int, body: PricePoint, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project_or_404(pid, u, db)
    mp = MarketPrice(project_id=pid, date=body.date, close=body.close)
    db.add(mp)
    db.commit()
    return {"ok": True}


@app.get("/api/projects/{pid}/prices", response_model=list[PriceOut])
def list_prices(pid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project_or_404(pid, u, db)
    mps = (db.query(MarketPrice)
           .filter(MarketPrice.project_id == pid)
           .order_by(MarketPrice.date).all())
    return [PriceOut(date=m.date, close=m.close) for m in mps]


@app.post("/api/projects/{pid}/fetch")
def fetch_prices(pid: int, body: Optional[FetchReq] = None,
                 u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """从行情数据源自动拉取 A股日线并写入实际线（覆盖式）。

    - force=False（进入项目时自动拉取）：若今日已拉取过或行情已是最新，则跳过外部
      调用，避免频繁请求数据源；
    - force=True（手动强制刷新）：始终重新拉取。
    """
    p = _project_or_404(pid, u, db)
    code = (body.code if body and body.code else p.code) or ""
    if not code:
        raise HTTPException(
            400, "项目未设置股票代码：新建/编辑项目时填写 code，例如 600519.SH")
    force = bool(body and body.force)
    today_s = _cn_today()
    # 自动拉取（非强制）：今日已拉取或行情已是最新 → 跳过外部调用
    if not force:
        latest = db.query(MarketPrice.date).filter(
            MarketPrice.project_id == pid).order_by(MarketPrice.date.desc()).first()
        latest_date = latest[0] if latest else None
        if latest_date and latest_date >= today_s:
            return {"ok": False, "skipped": True, "already_today": True,
                    "latest_date": latest_date, "name": p.name,
                    "message": f"今日行情已是最新（{latest_date}），无需重复获取"}
        if (p.last_fetch_date or "") == today_s:
            return {"ok": False, "skipped": True, "already_today": True,
                    "latest_date": latest_date, "name": p.name,
                    "message": f"今日已拉取行情（最新 {latest_date}），无需重复获取"}
    days = (body.days if body and body.days else 60)
    try:
        rows, source = fetch_a_hist(code, days)
    except Exception as e:
        raise HTTPException(502, f"行情拉取失败：{e}")
    if not rows:
        raise HTTPException(502, "未获取到任何行情数据")
    # 覆盖式：清空旧行情后写入
    db.query(MarketPrice).filter(MarketPrice.project_id == pid).delete()
    for d, c in rows:
        db.add(MarketPrice(project_id=pid, date=d, close=c))
    # 同步成功后，自动把项目名更新为股票名称（查询失败则保留原名）
    stock_name = None
    try:
        stock_name = fetch_stock_name(code)
        if stock_name:
            p.name = stock_name
    except Exception as e:
        logger.warning("股票名称查询失败（保留原项目名）：%s", e)
    # 记录今日已拉取，供自动拉取限频（每天最多实际调用一次数据源）
    p.last_fetch_date = today_s
    db.commit()
    return {
        "ok": True,
        "count": len(rows),
        "start": rows[0][0],
        "end": rows[-1][0],
        "latest_close": rows[-1][1],
        "name": stock_name or p.name,
        "source": {"tencent": "腾讯", "sina": "新浪",
                   "akshare": "akshare", "eastmoney": "东方财富"}.get(source, source),
    }


# ---------------- 生成建议（预测） ----------------
@app.post("/api/projects/{pid}/predict", response_model=PredictionOut, status_code=201)
def predict(pid: int, body: Optional[PredictionCreate] = None,
            u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _project_or_404(pid, u, db)
    recents = (db.query(MarketPrice)
               .filter(MarketPrice.project_id == pid)
               .order_by(MarketPrice.date).all())
    recent_prices = [(m.date, m.close) for m in recents]
    pred = run_strategy(p.strategy_type, p.strategy_config, recent_prices)
    if body:
        if body.date:
            pred["date"] = body.date
        if body.model:
            pred["model"] = body.model
        if body.note:
            pred["note"] = body.note
    if not pred.get("date"):
        pred["date"] = datetime.utcnow().strftime("%Y-%m-%d")
    pr = Prediction(
        project_id=pid, date=pred["date"], signal=pred["signal"],
        predicted_price=pred.get("predicted_price"),
        predicted_low=pred.get("predicted_low"),
        predicted_high=pred.get("predicted_high"),
        confidence=pred.get("confidence", 0.5),
        model=pred.get("model", p.strategy_type or "baseline"), note=pred.get("note", ""),
    )
    db.add(pr)
    db.commit()
    db.refresh(pr)
    return PredictionOut(
        id=pr.id, project_id=pr.project_id, date=pr.date, signal=pr.signal,
        predicted_price=pr.predicted_price, predicted_low=pr.predicted_low,
        predicted_high=pr.predicted_high, confidence=pr.confidence,
        model=pr.model, note=pr.note,
    )


# ---------------- 提交反馈（实际操作） ----------------
@app.post("/api/projects/{pid}/feedback", response_model=FeedbackOut, status_code=201)
def submit_feedback(pid: int, body: FeedbackCreate, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _project_or_404(pid, u, db)
    # 计算该笔实现的盈亏：回放已有 + 本笔（以用户输入仓位为种子）
    existing = db.query(Feedback).filter(Feedback.project_id == pid).all()
    new_fb = Feedback(project_id=pid, date=body.date, action=body.action,
                      price=body.price, qty=body.qty,
                      prediction_id=body.prediction_id, note=body.note)
    replayed, _ = replay(existing + [new_fb], p.position_shares or 0.0, p.position_cost or 0.0)
    realized = replayed[-1][1]
    new_fb.realized_pnl = realized
    db.add(new_fb)
    db.commit()
    db.refresh(new_fb)
    return FeedbackOut(
        id=new_fb.id, project_id=new_fb.project_id, date=new_fb.date,
        action=new_fb.action, price=new_fb.price, qty=new_fb.qty,
        prediction_id=new_fb.prediction_id, note=new_fb.note,
        realized_pnl=new_fb.realized_pnl,
    )


# ---------------- 图表数据（预测线 vs 实际线 vs 盈亏） ----------------
def _build_chart(p, db):
    mps = db.query(MarketPrice).filter(MarketPrice.project_id == p.id).order_by(MarketPrice.date).all()
    preds = db.query(Prediction).filter(Prediction.project_id == p.id).order_by(Prediction.date).all()
    fbs = db.query(Feedback).filter(Feedback.project_id == p.id).all()

    replayed, pos = replay(fbs, p.position_shares or 0.0, p.position_cost or 0.0)
    realized_map = {f.id: r for f, r in replayed}

    # pnl 曲线：按行情日期 mark-to-market（以用户输入仓位为种子）
    shares = float(p.position_shares or 0.0)
    avg = float(p.position_cost or 0.0)
    realized = 0.0
    fb_sorted = sorted(fbs, key=lambda f: (f.date, f.id))
    fi = 0
    pnl_curve = []
    for mp in mps:
        while fi < len(fb_sorted) and fb_sorted[fi].date <= mp.date:
            f = fb_sorted[fi]
            if f.action == "BUY" and f.qty > 0:
                ns = shares + f.qty
                if ns > 0:
                    avg = (avg * shares + f.price * f.qty) / ns
                shares = ns
            elif f.action == "SELL" and f.qty > 0:
                sq = min(f.qty, shares)
                realized += (f.price - avg) * sq
                shares -= sq
                if shares <= 1e-9:
                    shares, avg = 0.0, 0.0
            fi += 1
        equity = realized + shares * (mp.close - avg)
        pct = round(((mp.close - avg) / avg) * 100, 2) if shares > 1e-9 and avg > 1e-9 else None
        pnl_curve.append({"date": mp.date, "pnl": round(equity, 2), "pct": pct})

    # 持仓面板补充：最新价、市值、浮动盈亏、种子仓位
    latest_close = mps[-1].close if mps else None
    pos["unrealized"] = round((latest_close - pos["avg"]) * pos["shares"], 2) if latest_close is not None else 0.0
    pos["market_value"] = round(pos["shares"] * (latest_close or 0.0), 2)
    pos["seed_shares"] = round(float(p.position_shares or 0.0), 4)
    pos["seed_cost"] = round(float(p.position_cost or 0.0), 2)

    # 策略预测价曲线（贯穿历史 + 外推未来），随策略/参数实时变化
    strat_cfg = p.strategy_config or "{}"
    strat_curve = []
    if mps:
        mp_dates = [m.date for m in mps]
        mp_closes = [m.close for m in mps]
        hist = rolling_predict(mp_dates, mp_closes, p.strategy_type, strat_cfg)
        fut = forecast_future(mp_dates, mp_closes, p.strategy_type, strat_cfg, days=5)
        for d, pred, low, high, sig in hist + fut:
            strat_curve.append({
                "date": d,
                "predicted_price": pred,
                "predicted_low": low,
                "predicted_high": high,
                "signal": sig,
            })

    return ChartData(
        market_prices=[PricePoint(date=m.date, close=m.close) for m in mps],
        predictions=[
            PredictionOut(id=pr.id, project_id=pr.project_id, date=pr.date,
                         signal=pr.signal, predicted_price=pr.predicted_price,
                         predicted_low=pr.predicted_low, predicted_high=pr.predicted_high,
                         confidence=pr.confidence, model=pr.model, note=pr.note)
            for pr in preds
        ],
        feedbacks=[
            FeedbackOut(id=f.id, project_id=f.project_id, date=f.date, action=f.action,
                        price=f.price, qty=f.qty, prediction_id=f.prediction_id,
                        note=f.note, realized_pnl=realized_map.get(f.id, 0.0))
            for f in sorted(fbs, key=lambda x: x.date)
        ],
        pnl_curve=pnl_curve,
        position=pos,
        strategy_curve=strat_curve,
    )


@app.get("/api/projects/{pid}/chart", response_model=ChartData)
def chart(pid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _project_or_404(pid, u, db)
    return _build_chart(p, db)


@app.get("/api/guest/projects/{pid}/chart", response_model=ChartData)
def guest_chart(pid: int, db: Session = Depends(get_db)):
    p = _guest_project_guard(pid, db)
    return _build_chart(p, db)


@app.get("/api/guest/projects", response_model=list[ProjectOut])
def guest_list_projects(db: Session = Depends(get_db)):
    """游客模式：仅返回代码为 GUEST_STOCK_CODE 的项目，其余不可见。"""
    rows = db.query(Project).filter(Project.code == GUEST_STOCK_CODE).order_by(Project.created_at.desc()).all()
    return [_proj_out(p) for p in rows]


@app.get("/api/guest/projects/{pid}", response_model=ProjectOut)
def guest_get_project(pid: int, db: Session = Depends(get_db)):
    p = _guest_project_guard(pid, db)
    return _proj_out(p)


@app.get("/api/guest/projects/{pid}/refresh")
def guest_refresh(pid: int, db: Session = Depends(get_db)):
    """游客模式：拉取 000002.SZ 最新行情，限频（每天最多实际调用一次数据源）。

    - 若项目最新行情日期已是今天（当天数据已存在）→ 直接提示无需重复拉取；
    - 若今天已拉取过（last_fetch_date==今天）→ 同样提示，避免频繁调用外部源；
    - 否则拉取并“增量”写入缺失交易日，更新 last_fetch_date 为今天。
    """
    p = _guest_project_guard(pid, db)
    today_s = _cn_today()
    latest = db.query(MarketPrice.date).filter(MarketPrice.project_id == p.id).order_by(MarketPrice.date.desc()).first()
    latest_date = latest[0] if latest else None
    if latest_date and latest_date >= today_s:
        return {"updated": False, "already_today": True, "latest_date": latest_date,
                "message": f"今日行情已是最新（{latest_date}），无需重复获取"}
    if (p.last_fetch_date or "") == today_s:
        return {"updated": False, "already_today": True, "latest_date": latest_date,
                "message": f"今日已拉取行情（最新 {latest_date}），无需重复获取"}
    try:
        rows, source = fetch_a_hist(p.code, 250)
    except Exception as e:
        return {"updated": False, "already_today": False, "latest_date": latest_date,
                "error": True, "message": f"行情拉取失败：{e}"}
    if not rows:
        return {"updated": False, "already_today": False, "latest_date": latest_date,
                "error": True, "message": "未获取到任何行情数据"}
    # 增量写入：仅插入尚未存在的交易日，避免重复与覆盖历史
    existing = {d for (d,) in db.query(MarketPrice.date).filter(MarketPrice.project_id == p.id).all()}
    added = 0
    for d, c in rows:
        if d not in existing:
            db.add(MarketPrice(project_id=p.id, date=d, close=c))
            added += 1
    p.last_fetch_date = today_s
    # 同步成功后，自动把项目名更新为股票名称（查询失败则保留原名）
    stock_name = None
    try:
        stock_name = fetch_stock_name(p.code)
        if stock_name:
            p.name = stock_name
    except Exception as e:
        logger.warning("游客股票名称查询失败（保留原项目名）：%s", e)
    db.commit()
    new_latest = max(r[0] for r in rows)
    src_name = {"tencent": "腾讯", "sina": "新浪", "akshare": "akshare", "eastmoney": "东方财富"}.get(source, source)
    name = stock_name or p.name
    if added == 0:
        return {"updated": False, "already_today": True, "latest_date": new_latest, "name": name,
                "message": f"行情已是最新（{new_latest}），无需更新，来源：{src_name}"}
    return {"updated": True, "already_today": new_latest >= today_s, "latest_date": new_latest,
            "count": added, "source": src_name, "name": name,
            "message": f"行情已更新至最新（{new_latest}），新增 {added} 个交易日，来源：{src_name}"}


# ---------------- 多策略一致性 ----------------
def _build_consensus(p, strategies, threshold, db):
    """多策略一致性：对历史上每个交易日，统计所选策略给出相同信号的个数；
    仅当 ≥ threshold 个策略意见一致时，才返回该一致信号（BUY/SELL/HOLD），否则为 null。

    用于图上只标注“多策略共振”的买/卖/HOLD 点。各策略用其默认参数计算。
    返回：{ strategies, threshold, dates, signals:[{date,signal,count,total}] }
    """
    mps = db.query(MarketPrice).filter(MarketPrice.project_id == p.id).order_by(MarketPrice.date).all()
    dates = [m.date for m in mps]
    closes = [m.close for m in mps]
    if not dates:
        return {"strategies": [], "threshold": threshold, "dates": [], "signals": []}

    selected = [s.strip() for s in strategies.split(",") if s.strip() in STRATEGY_CATALOG]
    # 每个策略：用默认参数跑 rolling_predict，得到“截至当日”的信号
    per_strat = {}
    for st in selected:
        meta = STRATEGY_CATALOG[st]
        cfg = {k: v["default"] for k, v in meta.get("params", {}).items()}
        curve = rolling_predict(dates, closes, st, cfg)
        per_strat[st] = {d: sig for (d, pp, lo, hi, sig) in curve if pp is not None}

    signals = []
    for d in dates:
        votes = [per_strat[st][d] for st in selected if d in per_strat[st]]
        # 每个策略当天的信号（数据不足/窗口未到则为 None —— 即“当天没有操作建议”）
        per = {st: per_strat[st].get(d) for st in selected}
        if not votes:
            signals.append({"date": d, "signal": None, "count": 0, "total": len(selected), "per": per})
            continue
        top_sig, top_cnt = Counter(votes).most_common(1)[0]
        agreed = top_cnt >= threshold
        signals.append({
            "date": d,
            "signal": top_sig if agreed else None,
            "count": top_cnt,
            "total": len(selected),
            "per": per,
        })
    return {"strategies": selected, "threshold": threshold, "dates": dates, "signals": signals}


@app.get("/api/projects/{pid}/consensus")
def consensus(pid: int, strategies: str = "momentum,meanreversion,breakout,baseline",
              threshold: int = 2, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _project_or_404(pid, u, db)
    return _build_consensus(db.get(Project, pid), strategies, threshold, db)


@app.get("/api/guest/projects/{pid}/consensus")
def guest_consensus(pid: int, strategies: str = "momentum,meanreversion,breakout,baseline",
                    threshold: int = 2, db: Session = Depends(get_db)):
    p = _guest_project_guard(pid, db)
    return _build_consensus(p, strategies, threshold, db)


# ================= 圆桌讨论（多 AI 席位 · 双通道） =================
# 默认席位：首次访问圆桌接口时自动播种（后续可在页面「席位管理」增删改）
DEFAULT_SEATS = [
    {"name": "稳健配置", "persona": "风格稳健保守：重视仓位控制与安全边际，偏好低频操作，"
     "对高估值与高波动标的保持谨慎，建议以持有观察为主、设定明确止损线。"},
    {"name": "趋势跟踪", "persona": "风格趋势跟踪：尊重价格与量能的方向，顺势而为，"
     "对突破关键位/均线的标的给予跟进建议，弱化主观判断、强调纪律执行。"},
    {"name": "风险控制", "persona": "风格风险优先：首先评估回撤与集中度风险，"
     "对单一标的占比过高或涨幅过大的持仓提示止盈/分散，宁可错过不可做错。"},
]

# LLM 提供方默认配置（OpenAI 兼容协议；用户可在「AI 设置」覆盖）
PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-air"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}

# 提示词输出格式约定（行式结构，前端 prewrap 渲染即整齐，避免表格）
PROMPT_OUTPUT_SPEC = """# 输出格式（严格遵守，不要使用表格）
## 总体判断
（一段话，120字以内，点评今日大盘对整体持仓的影响）
## 逐持仓点评
- 标的名：建议（持有/加仓/减仓）｜一句理由
## 风险提示
（一条当前最需警惕的风险）"""

_LLM_SYSTEM = "你是严谨的A股投研助手。严格基于用户给出的持仓与行情快照作答，不臆测快照之外的数据，并严格遵守用户指定的输出格式。"


def _ensure_default_seats(db):
    """圆桌表为空时播种 3 个默认席位（幂等）。"""
    if db.query(RoundtableSeat).count() == 0:
        for i, s in enumerate(DEFAULT_SEATS):
            db.add(RoundtableSeat(name=s["name"], persona=s["persona"],
                                  mode="manual", provider="deepseek", sort=i))
        db.commit()


def _get_llm_conf(db, provider: str) -> dict:
    """读取某提供方的 LLM 配置（含 api_key，仅服务端使用，绝不回传前端）。"""
    rows = dict(db.query(Setting.key, Setting.value)
                .filter(Setting.key.like(f"llm.{provider}.%")).all())
    dflt = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["deepseek"])
    return {
        "api_key": rows.get(f"llm.{provider}.api_key") or "",
        "base_url": rows.get(f"llm.{provider}.base_url") or dflt["base_url"],
        "model": rows.get(f"llm.{provider}.model") or "",
    }


def _seat_out(s: RoundtableSeat, db) -> RoundtableSeatOut:
    conf = _get_llm_conf(db, s.provider or "deepseek")
    return RoundtableSeatOut(
        id=s.id, name=s.name or "", persona=s.persona or "",
        mode=s.mode or "manual", provider=s.provider or "", model=s.model or "",
        enabled=1 if s.enabled else 0, has_key=bool(conf.get("api_key")),
        sort=s.sort or 0,
        created_at=s.created_at.isoformat() if s.created_at else None,
    )


def _effective_position(p, db):
    """项目有效持仓 = 用户种子仓位 + 反馈回放（与项目页盈亏曲线同源，保证三处口径一致）。
    返回 (shares, avg, feedbacks按时间正序)。"""
    fbs = (db.query(Feedback).filter(Feedback.project_id == p.id)
           .order_by(Feedback.date, Feedback.id).all())
    _, pos = replay(fbs, p.position_shares or 0.0, p.position_cost or 0.0)
    return float(pos["shares"] or 0.0), float(pos["avg"] or 0.0), fbs


def _build_snapshot(u, db) -> dict:
    """圆桌「今日快照」：大盘指数 + 当前用户可见持仓（市值/收益率/今日涨跌/最新建议）。"""
    q = db.query(Project)
    if not u.is_admin:
        q = q.filter(Project.owner_id == u.id)
    projects = q.order_by(Project.pinned.desc(), Project.created_at.desc()).all()

    holdings = []
    for p in projects:
        shares, cost, _fbs = _effective_position(p, db)
        if shares <= 0:
            continue          # 圆桌只点评当前有持仓的标的；历史标的见「历史标的与操作记录」
        mps = (db.query(MarketPrice).filter(MarketPrice.project_id == p.id)
               .order_by(MarketPrice.date.desc()).limit(2).all())
        latest, prev = (mps[0] if mps else None), (mps[1] if len(mps) > 1 else None)
        close = latest.close if latest else None
        h = {
            "id": p.id, "name": p.name, "code": p.code or "", "market": p.market or "A",
            "is_fund": (p.market == "fund"),
            "shares": round(shares, 4), "cost": cost,
            "latest_date": latest.date if latest else None,
            "latest_close": close,
            "prev_close": prev.close if prev else None,
            "day_chg_pct": (round((close - prev.close) / prev.close * 100, 2)
                            if (close and prev and prev.close) else None),
            "market_value": round(shares * close, 2) if close else None,
            "unrealized": round((close - cost) * shares, 2) if close else None,
            "return_pct": (round((close - cost) / cost * 100, 2)
                           if (close and cost > 0) else None),
            "latest_signal": None, "latest_signal_date": None,
        }
        pred = (db.query(Prediction).filter(Prediction.project_id == p.id)
                .order_by(Prediction.date.desc(), Prediction.id.desc()).first())
        if pred:
            h["latest_signal"] = pred.signal
            h["latest_signal_date"] = pred.date
        holdings.append(h)

    try:
        indices = fetch_index_quotes()
    except Exception as e:
        logger.warning("大盘指数获取失败：%s", e)
        indices = []
    return {
        "date": _cn_today(),
        "generated_at": datetime.utcnow().isoformat(),
        "indices": indices,
        "holdings": holdings,
    }


def _snapshot_prompt_text(snap: dict, seat: RoundtableSeat) -> str:
    """把今日快照 + 席位人格 + 输出格式组装成完整提示词（自动/手动通道共用）。"""
    L = ["# 角色", f"你是「{seat.name or '分析师'}」。{seat.persona or '语气专业、观点明确。'}",
         "", f"# 今日快照（{snap['date']}）", "## 大盘"]
    for idx in snap.get("indices") or []:
        if idx.get("close") is None:
            L.append(f"- {idx['name']}：数据获取失败")
        else:
            chg = f"{idx['chg_pct']:+.2f}%" if idx.get("chg_pct") is not None else "涨跌未知"
            L.append(f"- {idx['name']} {idx['close']}（{chg}，{idx.get('date') or ''}）")
    L.append("## 我的持仓")
    hs = snap.get("holdings") or []
    if not hs:
        L.append("- 当前无持仓（本轮只点评大盘即可）")
    for h in hs:
        seg = f"- {h['name']}（{h['code'] or h['market']}）："
        if h.get("latest_close") is None:
            seg += "暂无行情数据"
        else:
            parts = [f"最新价 {h['latest_close']}（{h.get('latest_date') or ''}）"]
            if h.get("market_value") is not None:
                parts.append(f"市值 {h['market_value']} 元")
            if h.get("return_pct") is not None:
                parts.append(f"收益率 {h['return_pct']:+.2f}%")
            if h.get("day_chg_pct") is not None:
                parts.append(f"今日 {h['day_chg_pct']:+.2f}%")
            if h.get("latest_signal"):
                parts.append(f"系统策略建议 {h['latest_signal']}（{h.get('latest_signal_date') or ''}）")
            seg += "，".join(parts)
        L.append(seg)
    L += ["", "# 任务",
          "1. 先点评今日大盘对整体持仓的影响；",
          "2. 逐个持仓给出持有/加仓/减仓建议与一句理由；",
          "3. 指出当前最需警惕的一个风险。",
          "", PROMPT_OUTPUT_SPEC]
    return "\n".join(L)


def _upsert_post(db, date_s: str, seat_id: int, content: str, source: str, model: str):
    """按 (date, seat_id) 唯一约束覆盖更新，返回该条观点。"""
    post = (db.query(RoundtablePost)
            .filter(RoundtablePost.date == date_s, RoundtablePost.seat_id == seat_id).first())
    if post:
        post.content = content
        post.source = source
        post.model = model
    else:
        post = RoundtablePost(date=date_s, seat_id=seat_id, content=content,
                              source=source, model=model)
        db.add(post)
    db.commit()
    db.refresh(post)
    return post


@app.get("/api/roundtable/seats", response_model=list[RoundtableSeatOut])
def rt_list_seats(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_default_seats(db)
    seats = db.query(RoundtableSeat).order_by(RoundtableSeat.sort, RoundtableSeat.id).all()
    return [_seat_out(s, db) for s in seats]


@app.post("/api/roundtable/seats", response_model=RoundtableSeatOut, status_code=201)
def rt_create_seat(body: RoundtableSeatCreate, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    if body.mode not in ("manual", "auto"):
        raise HTTPException(400, "mode 仅支持 manual / auto")
    max_sort = db.query(RoundtableSeat.sort).order_by(RoundtableSeat.sort.desc()).first()
    s = RoundtableSeat(name=body.name.strip() or "新席位", persona=body.persona,
                       mode=body.mode, provider=body.provider or "deepseek",
                       model=body.model, sort=(max_sort[0] + 1) if max_sort else 0)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _seat_out(s, db)


@app.patch("/api/roundtable/seats/{sid}", response_model=RoundtableSeatOut)
def rt_update_seat(sid: int, body: RoundtableSeatUpdate, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    s = db.get(RoundtableSeat, sid)
    if not s:
        raise HTTPException(404, "席位不存在")
    for field in ("name", "persona", "mode", "provider", "model", "enabled", "sort"):
        v = getattr(body, field)
        if v is not None:
            if field == "mode" and v not in ("manual", "auto"):
                raise HTTPException(400, "mode 仅支持 manual / auto")
            setattr(s, field, v)
    db.commit()
    db.refresh(s)
    return _seat_out(s, db)


@app.delete("/api/roundtable/seats/{sid}")
def rt_delete_seat(sid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    s = db.get(RoundtableSeat, sid)
    if not s:
        raise HTTPException(404, "席位不存在")
    db.query(RoundtablePost).filter(RoundtablePost.seat_id == sid).delete()
    db.delete(s)
    db.commit()
    return {"ok": True}


@app.get("/api/roundtable/snapshot")
def rt_snapshot(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _build_snapshot(u, db)


@app.get("/api/roundtable/history")
def rt_history(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """圆桌 · 历史标的与仓位操作记录。

    symbols：有持仓或有操作记录的项目（未建仓且无操作的不纳入），
    status=holding/cleared；operations：全部反馈操作按日期倒序（含标的信息）。
    与今日快照互补：快照只含有持仓标的，完整历史在这里承载。
    """
    q = db.query(Project)
    if not u.is_admin:
        q = q.filter(Project.owner_id == u.id)
    projects = q.order_by(Project.pinned.desc(), Project.created_at.desc()).all()

    symbols, operations = [], []
    for p in projects:
        shares, cost, fbs = _effective_position(p, db)
        if shares > 0:
            status = "holding"
        elif fbs:
            status = "cleared"
        else:
            continue          # 未建仓且无操作记录：不进历史面板
        symbols.append({
            "id": p.id, "name": p.name, "code": p.code or "", "market": p.market or "A",
            "is_fund": (p.market == "fund"),
            "shares": round(shares, 4), "cost": round(cost, 4),
            "status": status,
            "op_count": len(fbs),
            "realized_pnl": round(sum(float(f.realized_pnl or 0.0) for f in fbs), 2),
            "first_date": fbs[0].date if fbs else None,    # fbs 为时间正序，首条最早
            "last_date": fbs[-1].date if fbs else None,
        })
        for f in fbs:
            operations.append({
                "id": f.id, "date": f.date, "project_id": p.id,
                "name": p.name, "code": p.code or "",
                "action": f.action, "price": f.price, "qty": f.qty,
                "realized_pnl": round(float(f.realized_pnl or 0.0), 2),
                "note": f.note or "",
            })
    # 持仓中优先，其余维持项目原顺序（pinned/创建时间）；操作记录按日期倒序
    order = {"holding": 0, "cleared": 1, "untracked": 2}
    symbols.sort(key=lambda s: order.get(s["status"], 9))
    operations.sort(key=lambda o: (o["date"], o["id"]), reverse=True)
    return {"symbols": symbols, "operations": operations}


@app.get("/api/roundtable/prompt")
def rt_prompt(seat_id: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """生成某席位今日提示词（手动通道：用户复制后去外部 AI 网页咨询）。"""
    _ensure_default_seats(db)
    s = db.get(RoundtableSeat, seat_id)
    if not s:
        raise HTTPException(404, "席位不存在")
    snap = _build_snapshot(u, db)
    return {"seat_id": s.id, "seat_name": s.name, "date": snap["date"],
            "prompt": _snapshot_prompt_text(snap, s)}


@app.get("/api/roundtable/posts", response_model=list[RoundtablePostOut])
def rt_posts(date: Optional[str] = None, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    date_s = date or _cn_today()
    rows = (db.query(RoundtablePost, RoundtableSeat.name)
            .join(RoundtableSeat, RoundtableSeat.id == RoundtablePost.seat_id)
            .filter(RoundtablePost.date == date_s)
            .order_by(RoundtableSeat.sort, RoundtableSeat.id).all())
    return [RoundtablePostOut(
        seat_id=p.seat_id, seat_name=name or "", date=p.date, content=p.content or "",
        source=p.source or "manual", model=p.model or "",
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    ) for p, name in rows]


@app.post("/api/roundtable/posts", response_model=RoundtablePostOut, status_code=201)
def rt_submit_post(body: RoundtablePostIn, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """手动通道：把外部 AI 的回复粘贴回填到对应席位（覆盖当日该席位观点）。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    s = db.get(RoundtableSeat, body.seat_id)
    if not s:
        raise HTTPException(404, "席位不存在")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "反馈内容不能为空")
    if len(content) > 20000:
        raise HTTPException(400, "内容过长（上限 20000 字符）")
    date_s = body.date or _cn_today()
    post = _upsert_post(db, date_s, body.seat_id, content, "manual", "")
    return RoundtablePostOut(
        seat_id=post.seat_id, seat_name=s.name or "", date=post.date, content=post.content or "",
        source=post.source or "manual", model=post.model or "",
        updated_at=post.updated_at.isoformat() if post.updated_at else None,
    )


@app.get("/api/roundtable/settings")
def rt_get_settings(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """LLM 设置（脱敏）：只返回是否已配置 Key 与 base_url/model，绝不回传 Key 本身。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    providers = {}
    for name in PROVIDER_DEFAULTS:
        conf = _get_llm_conf(db, name)
        providers[name] = {
            "configured": bool(conf["api_key"]),
            "base_url": conf["base_url"],
            "model": conf["model"] or PROVIDER_DEFAULTS[name]["model"],
        }
    return {"providers": providers}


@app.put("/api/roundtable/settings")
def rt_put_settings(body: RoundtableSettingsIn, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """保存某提供方 LLM 配置；api_key 传空串 = 清除已配置的 Key。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    provider = (body.provider or "").strip()
    if provider not in PROVIDER_DEFAULTS:
        raise HTTPException(400, f"不支持的提供方：{provider}")
    fields = {"api_key": body.api_key, "base_url": body.base_url, "model": body.model}
    for field, val in fields.items():
        key = f"llm.{provider}.{field}"
        row = db.get(Setting, key)
        if val is None:
            continue
        val = str(val).strip()
        if field == "api_key" and val == "":
            if row:
                db.delete(row)          # 清除 Key
            continue
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    return {"ok": True}


@app.post("/api/roundtable/generate")
def rt_generate(body: Optional[RoundtableGenerateReq] = None,
                u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """自动通道：对 mode=auto 且已配置 Key 的席位并发调用 LLM 生成今日观点并入库。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    _ensure_default_seats(db)
    date_s = (body.date if body and body.date else _cn_today())
    q = db.query(RoundtableSeat).filter(RoundtableSeat.enabled == 1, RoundtableSeat.mode == "auto")
    if body and body.seat_ids:
        q = q.filter(RoundtableSeat.id.in_(body.seat_ids))
    seats = q.order_by(RoundtableSeat.sort, RoundtableSeat.id).all()
    if not seats:
        return {"date": date_s, "results": [],
                "message": "没有可自动生成的席位（需在席位管理中将席位设为「自动」并配置 API Key）"}

    snap = _build_snapshot(u, db)
    # 线程内只做网络调用（不碰 db session，SQLAlchemy Session 非线程安全），结果回主线程统一写库
    seat_meta = {s.id: {"name": s.name or "", "provider": s.provider or "deepseek",
                        "model": s.model or "", "persona": s.persona or ""} for s in seats}
    confs = {sid: _get_llm_conf(db, m["provider"]) for sid, m in seat_meta.items()}

    def _one(sid: int):
        m = seat_meta[sid]
        conf = confs[sid]
        if not conf.get("api_key"):
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": f"未配置 {m['provider']} 的 API Key"}
        model = m["model"] or conf.get("model") or PROVIDER_DEFAULTS[m["provider"]]["model"]
        seat_obj = RoundtableSeat(id=sid, name=m["name"], persona=m["persona"])  # 仅用于拼提示词
        prompt = _snapshot_prompt_text(snap, seat_obj)
        try:
            content = chat_complete(
                conf["base_url"], conf["api_key"], model,
                [{"role": "system", "content": _LLM_SYSTEM},
                 {"role": "user", "content": prompt}],
            )
        except Exception as e:
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": str(e), "model": model}
        if not content.strip():
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": "模型返回为空", "model": model}
        return {"seat_id": sid, "seat_name": m["name"], "ok": True,
                "content": content, "model": model}

    results = []
    with ThreadPoolExecutor(max_workers=min(3, len(seats))) as ex:
        for r in ex.map(_one, [s.id for s in seats]):
            results.append(r)
    # 主线程统一写库
    for r in results:
        if r.get("ok"):
            _upsert_post(db, date_s, r["seat_id"], r["content"], "auto", r.get("model") or "")
    return {"date": date_s, "results": results}


# ---------------- 前端静态托管 ----------------
# 显式 API 路由已先于 mount 注册，/api/* 由 API 处理；其余路径由静态目录托管（html=True 使 / 返回 index.html）。
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
