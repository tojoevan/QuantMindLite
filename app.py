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
)
from models import (
    ProjectCreate, ProjectOut, ProjectUpdate, PricePoint, PredictionCreate, PredictionOut,
    FeedbackCreate, FeedbackOut, ChartData, FetchReq, PriceOut,
)
from predictor import run_strategy, STRATEGY_CATALOG, rolling_predict, forecast_future
from datasource import fetch_a_hist, fetch_stock_name

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
        strategy=p.strategy, strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias,
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
        strategy=p.strategy, strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias,
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
            strategy=p.strategy, strategy_type=p.strategy_type,
            strategy_config=p.strategy_config, bias=p.bias,
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
        strategy=p.strategy, strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias,
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
        strategy=p.strategy, strategy_type=p.strategy_type,
        strategy_config=p.strategy_config, bias=p.bias,
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
    _project_or_404(pid, u, db)
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
        pnl_curve.append({"date": mp.date, "pnl": round(equity, 2)})

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


# ---------------- 前端静态托管 ----------------
# 显式 API 路由已先于 mount 注册，/api/* 由 API 处理；其余路径由静态目录托管（html=True 使 / 返回 index.html）。
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
