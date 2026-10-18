"""慢量化个人操作 Web —— 建议→反馈→再预测 决策台后端。
FastAPI + SQLite，独立运行。预测引擎是本地纯函数策略（predictor.py），
**不依赖 QuantMind / Qlib 代码**——同机的 QuantMind 容器处于空转状态、未接入，
评估结论与将来接入条件见 QuantMind接入评估.md。
"""
import os
import re
import json
import time
import socket
import logging
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta, date
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

logger = logging.getLogger("quant-web")

from db import (
    engine, SessionLocal, Project, MarketPrice, Prediction, Feedback,
    User, Session as SessionModel, PasswordResetRequest,
    RoundtableSeat, RoundtablePost, RoundtableStyle, ForumThread, ForumPost, Setting,
    NewsFeed, NewsItem, NewsInjectLog, NewsDigest, SettleReport, LLMCallLog,
)
from models import (
    ProjectCreate, ProjectOut, ProjectUpdate, PricePoint, PredictionCreate, PredictionOut,
    FeedbackCreate, FeedbackOut, ChartData, FetchReq, PriceOut,
    RoundtableSeatCreate, RoundtableSeatUpdate, RoundtableSeatOut,
    RoundtablePostIn, RoundtablePostOut, RoundtableSettingsIn, RoundtableGenerateReq,
    RoundtableArchiveReq,
    RoundtableReviewReq,
    RoundtableStyleCreate, RoundtableStyleUpdate, RoundtableStyleOut,
    ForumThreadCreate, ForumPostCreate, ForumPostOut, ForumThreadOut, ForumThreadDetail,
    NewsFeedCreate, NewsFeedUpdate, NewsFeedOut, NewsItemOut, NewsFetchReq, NewsSettingsIn,
    NewsOpmlIn, DailyReportReq, SettleReportReq,
    NewsDigestReq, NewsScoreReq,
)
from predictor import run_strategy, STRATEGY_CATALOG, rolling_predict, forecast_future
from datasource import (
    fetch_hist, fetch_symbol_name, fetch_index_quotes, fetch_index_hist, lookup_symbol,
    normalize_market, MARKET_LABELS, MARKET_HINTS, kline_bars,
)
from llm import chat_complete
import newsfetch
import newsai
import rtreview

app = FastAPI(title="慢量化", version="0.1.0")


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
def _cn_now():
    """北京时间 YYYY-MM-DD HH:MM（给用户看的时刻一律用北京时间，库内仍存 UTC）"""
    return (datetime.utcnow() + _CN_TZ_OFFSET).strftime("%Y-%m-%d %H:%M")


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


def _account_totals(projects, db, asof: Optional[str] = None) -> dict:
    """账户级三指标（总市值 / 浮动盈亏 / 累计已实现）的**唯一定义**。

    取这三处公用：交易仪表台 KPI、圆桌页指标条、结算帖（日/周/月/季/半年/年）。
    口径一旦分家，同一时点在不同页面会显示不同的数字，而且没人会立刻发现 ——
    所以这里刻意做成「谁要这几个数都来找它」，不要在别处再写一遍求和。

    asof: YYYY-MM-DD，市值/浮盈取该日（含）前最后一条行情、已实现取该日（含）前的反馈；
          None = 各标的取各自最新行情（仪表台/圆桌页的「此刻」口径）。
    持仓数按 position_shares 判定（当前持仓，与结算窗口无关）。
    """
    tot_mv = tot_un = tot_real = 0.0
    held = 0
    for p in projects:
        shares = float(p.position_shares or 0.0)
        cost = float(p.position_cost or 0.0)
        if shares > 1e-9:
            held += 1
        q = db.query(MarketPrice).filter(MarketPrice.project_id == p.id)
        fq = db.query(func.sum(Feedback.realized_pnl)).filter(Feedback.project_id == p.id)
        if asof:
            q = q.filter(MarketPrice.date <= asof)
            fq = fq.filter(Feedback.date <= asof)
        mp = q.order_by(MarketPrice.date.desc()).first()
        close = float(mp.close) if mp else None
        tot_mv += round(shares * (close or 0.0), 2)
        tot_un += round((close - cost) * shares, 2) if close is not None else 0.0
        tot_real += float(fq.scalar() or 0.0)
    return {
        "market_value": round(tot_mv, 2),
        "unrealized": round(tot_un, 2),
        "realized": round(tot_real, 2),
        "positions": held,
        "projects": len(projects),
    }


def _build_overview(projects, db):
    """交易仪表台聚合：持仓总览 + 汇总指标 + 最近交易记录（实时计算，不落新表）。

    position_shares/position_cost 已是「含全部反馈回放的当前持仓」，此处直接取用；
    实现盈亏取各反馈 realized_pnl 累加；市值/浮动盈亏按最新行情 mark-to-market。
    汇总三指标统一走 _account_totals（唯一定义）。
    """
    positions, trades = [], []
    for p in projects:
        fbs = (db.query(Feedback).filter(Feedback.project_id == p.id)
               .order_by(Feedback.date).all())
        realized = round(sum(float(f.realized_pnl or 0.0) for f in fbs), 2)
        mp = (db.query(MarketPrice).filter(MarketPrice.project_id == p.id)
              .order_by(MarketPrice.date.desc()).first())
        last = float(mp.close) if mp else None
        shares = float(p.position_shares or 0.0)
        cost = float(p.position_cost or 0.0)
        mv = round(shares * (last or 0.0), 2)
        up = round((last - cost) * shares, 2) if last is not None else 0.0
        pct = (round((last - cost) / cost * 100, 2)
               if (last is not None and cost > 1e-9 and shares > 1e-9) else None)
        positions.append({
            "id": p.id, "name": p.name, "code": p.code, "market": p.market,
            "shares": round(shares, 4), "cost": round(cost, 4),
            "last_close": last, "last_date": mp.date if mp else None,
            "market_value": mv, "unrealized": up, "pct": pct, "realized": realized,
        })
        for f in fbs:
            trades.append({
                "id": f.id, "date": f.date, "project_id": p.id,
                "name": p.name, "code": p.code, "action": f.action,
                "price": f.price, "qty": f.qty,
                "realized_pnl": round(float(f.realized_pnl or 0.0), 2),
                "note": f.note or "",
            })
    trades.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    return {
        "totals": _account_totals(projects, db),
        "positions": positions,
        "trades": trades[:60],
    }


# ---------------- 盈亏回放 ----------------
def _replay_raw(feedbacks, seed_shares=0.0, seed_cost=0.0):
    """回放反馈序列（全精度，不四舍五入），计算持仓与盈亏。
    seed_shares / seed_cost 为“建仓起点”。返回 (out, dict(shares, avg, realized))。"""
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
    return out, dict(shares=shares, avg=avg, realized=realized)


def replay(feedbacks, seed_shares=0.0, seed_cost=0.0):
    """对外展示用：在 _replay_raw 基础上对持仓/成本四舍五入（成本保留2位）。"""
    out, pos = _replay_raw(feedbacks, seed_shares, seed_cost)
    return out, dict(shares=round(pos["shares"], 4), avg=round(pos["avg"], 2), realized=round(pos["realized"], 2))


def _holding_initial(fbs, cur_shares, cur_cost):
    """由『当前持仓(position_shares)』反推『首笔反馈前的建仓起点』，用于盈亏曲线时间轴基准。

    position_shares 现已维护为『含全部反馈回放的当前持仓』，故盈亏曲线不能再把它当种子再回放一次
    （否则反馈被二次累加）。这里沿反馈逆序撤销每笔操作，得到建仓起点，再交由 replay 正向重放时间轴。
    """
    s = float(cur_shares or 0.0)
    avg = float(cur_cost or 0.0)
    for f in sorted(fbs, key=lambda x: (x.date, x.id), reverse=True):
        if f.action == "BUY" and (f.qty or 0) > 0:
            s0 = s - f.qty
            if s0 > 1e-9:
                avg = (avg * s - (f.price or 0.0) * f.qty) / s0
            else:
                avg = 0.0
            s = s0
        elif f.action == "SELL" and (f.qty or 0) > 0:
            s = s + f.qty  # 卖出不改变成本基准
    return s, avg


def _pnl_curve(mps, fbs, init_shares, init_cost):
    """逐日 mark-to-market 收益曲线：`equity = 已实现盈亏 + 持仓浮盈`。

    **必须唯一定义**：项目页盈亏曲线（_build_chart）与每日持仓日报的「收益变化」
    都取这一段结果，两处口径一旦分家，日报的数字就会和项目页曲线对不上。

    性质：买入不改变 equity（新份额按买入价入账、浮盈为 0），卖出把浮盈转为已实现
    （equity 连续），因此相邻两点的差就是**真实收益变化**，不包含新增投入的本金。

    返回 [{"date","pnl","pct"}, ...]，与行情日期一一对应。
    """
    shares, avg, realized = float(init_shares or 0.0), float(init_cost or 0.0), 0.0
    fs = sorted(fbs, key=lambda f: (f.date, f.id))
    fi = 0
    out = []
    for mp in mps:
        while fi < len(fs) and fs[fi].date <= mp.date:
            f = fs[fi]
            if f.action == "BUY" and (f.qty or 0) > 0:
                ns = shares + f.qty
                if ns > 0:
                    avg = (avg * shares + f.price * f.qty) / ns
                shares = ns
            elif f.action == "SELL" and (f.qty or 0) > 0:
                sq = min(f.qty, shares)
                realized += (f.price - avg) * sq
                shares -= sq
                if shares <= 1e-9:
                    shares, avg = 0.0, 0.0
            fi += 1
        equity = realized + shares * (mp.close - avg)
        pct = round(((mp.close - avg) / avg) * 100, 2) if shares > 1e-9 and avg > 1e-9 else None
        out.append({"date": mp.date, "pnl": round(equity, 2), "pct": pct})
    return out


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
            if field == "market":
                v = normalize_market(v)
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
    p = Project(name=body.name, code=body.code, market=normalize_market(body.market),
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


# ---------------- 标的检索（新建/编辑标的时自动带出名称与品种） ----------------
@app.get("/api/symbol/lookup")
def symbol_lookup(q: str = "", u: User = Depends(get_current_user)):
    """按代码或名称检索标的，返回候选（含权威的市场前缀与品种）。

    主源腾讯 smartbox（本 VPS 实测可达），失败回退东财检索。
    解决两类写法歧义：① 沪深前缀猜不准（510300 沪 / 159915 深）；
    ② 同一 6 位数字既是场内又是场外（019547 国债 vs 某 QDII、161725 LOF vs 某 LOF 联接）。
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"query": q, "items": [], "markets": MARKET_LABELS, "hints": MARKET_HINTS}
    return {"query": q, "items": lookup_symbol(q, limit=8),
            "markets": MARKET_LABELS, "hints": MARKET_HINTS}


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


# ---------------- 行情拉取的共用语义（写入方式 + 窗口） ----------------
# 两条入口都复用这套规则：/api/projects/{pid}/fetch（登录）与
# /api/guest/projects/{pid}/refresh（游客）。**必须一致**——同一条实际线，
# 两种入口写出的复权基准不同就会出现假跳空。
def _existing_span(p, db):
    """存量实际线 (条数, 最早日期)。空则为 (0, None)。"""
    n, d0 = (db.query(func.count(MarketPrice.id), func.min(MarketPrice.date))
             .filter(MarketPrice.project_id == p.id).one())
    return int(n or 0), d0


def _resolve_days(p, mk, days, force, db):
    """决定本次拉取的窗口宽度，返回 `(days, have)`。

    - 场外基金（fund）：写入是**合并式**（只补/改，从不删），窗口大小影响不到存量，原样返回。
    - 场内品种：写入是**覆盖式重写**（见 _store_rows），窗口一旦小于存量条数就会把历史
      整段截短且不可逆（2026-10-15 实测事故：270 条被截到 120 条）。因此自动拉取
      （force=False）时把窗口放到**至少覆盖存量条数**；用户手动强制刷新（force=True）
      由用户明确指定窗口，不设下限。

      ⚠️ 别把「窗口按存量放大」当成缺陷去优化成「刚好等于存量」：datasource 会把 days
      换算成 days+20 根 K 线，正这 20 根缓冲让窗口每次朝**更早**的方向多覆盖一点，
      序列只会缓慢变长；一旦改成刚好等于存量，每次拉取就会把最早的那几根滚掉，
      变成一种更隐蔽的历史截短。

    返回的 `have`（存量条数）供调用方做兜底守卫：即便按存量放大了窗口，数据源仍可能
    只回更少的根数（接口条数上限/停牌），此时**跳过写入**好过截断历史。
    force=True 与 fund 恒返回 have=0（表示不设守卫）。
    """
    if force or mk == "fund":
        return days, 0
    have, _ = _existing_span(p, db)
    if kline_bars(days) < have:
        days = have
    return days, have


def _store_rows(p, mk, rows, db):
    """把拉取结果写入实际线，返回 `(added, updated, rewritten)`。

    - `fund` 场外基金：**合并写入**。净值接口一次只回一段窗口，先删后写会把窗口外的
      历史（可能上千条）整段丢掉，故只按 (project_id, date) 补缺/改值。
    - 场内品种：**覆盖式重写**（先删后写）。前复权价会随除权整体平移，只补新日期会让
      除权点两侧混着两套复权基准（老日期旧基准、新日期新基准），曲线出现假跳空、
      策略信号也会失真，所以必须整段替换。存量与本次结果完全一致时跳过写入。
    """
    existing = db.query(MarketPrice).filter(MarketPrice.project_id == p.id).all()
    if mk == "fund":
        by_date = {m.date: m for m in existing}
        added = updated = 0
        for d, c in rows:
            m = by_date.get(d)
            if m is None:
                db.add(MarketPrice(project_id=p.id, date=d, close=c))
                added += 1
            elif m.close != c:
                m.close = c
                updated += 1
        return added, updated, 0
    if {m.date: m.close for m in existing} == {d: c for d, c in rows}:
        return 0, 0, 0
    db.query(MarketPrice).filter(MarketPrice.project_id == p.id).delete()
    for d, c in rows:
        db.add(MarketPrice(project_id=p.id, date=d, close=c))
    return len(rows), 0, 1


def _shrink_guard_msg(mk, got, have):
    """覆盖式写入前的兜底提示（got < have 时才用）。"""
    return (f"{MARKET_LABELS[mk]}数据源本次只返回 {got} 条，少于已有历史 {have} 条；"
            "已跳过写入以免截断历史（覆盖式写入会整段替换）。请稍后重试，"
            "或在手动强制刷新时指定更大的窗口。")


@app.post("/api/projects/{pid}/fetch")
def fetch_prices(pid: int, body: Optional[FetchReq] = None,
                 u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """按**品种**从对应数据源拉取历史序列并写入实际线。

    品种路由（见 datasource.py）：股票/场内基金/债券 → 腾讯日线；场外基金 → 东财净值。
    写入语义按品种分（见 _store_rows）：场内覆盖式重写、场外合并写入。
    - force=False（进入项目时自动拉取）：若今日已拉取过或行情已是最新，则跳过外部
      调用，避免频繁请求数据源；
    - force=True（手动强制刷新）：始终重新拉取。
    """
    p = _project_or_404(pid, u, db)
    code = (body.code if body and body.code else p.code) or ""
    mk = normalize_market(p.market)
    if not code:
        raise HTTPException(
            400, f"项目未设置代码：新建/编辑项目时填写 code，{MARKET_LABELS[mk]}的写法如 "
                 f"{MARKET_HINTS[mk]}")
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
    # 场外基金默认多取一年：净值序列短（一天一个点），窗口太小会把历史截没
    default_days = 365 if mk == "fund" else 60
    days = (body.days if body and body.days else default_days)
    # 自动拉取时窗口不得小于已有历史条数（覆盖式写入会截短历史，见 _resolve_days）
    days, have = _resolve_days(p, mk, days, force, db)
    try:
        rows, source = fetch_hist(code, mk, days)
    except Exception as e:
        raise HTTPException(502, f"{MARKET_LABELS[mk]}拉取失败：{e}")
    if not rows:
        raise HTTPException(502, "未获取到任何行情数据")
    if have and len(rows) < have:
        raise HTTPException(502, _shrink_guard_msg(mk, len(rows), have))
    _store_rows(p, mk, rows, db)
    # 同步成功后，自动把项目名更新为标的名称（查询失败则保留原名）
    sym_name = None
    try:
        sym_name = fetch_symbol_name(code, mk)
        if sym_name:
            p.name = sym_name
    except Exception as e:
        logger.warning("标的名称查询失败（保留原项目名）：%s", e)
    # 记录今日已拉取，供自动拉取限频（每天最多实际调用一次数据源）
    p.last_fetch_date = today_s
    db.commit()
    return {
        "ok": True,
        "count": len(rows),
        "start": rows[0][0],
        "end": rows[-1][0],
        "latest_close": rows[-1][1],
        "name": sym_name or p.name,
        "market": mk,
        "market_label": MARKET_LABELS[mk],
        "source": source,
        "rewritten": mk != "fund",
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
    new_fb = Feedback(project_id=pid, date=body.date, action=body.action,
                      price=body.price, qty=body.qty,
                      prediction_id=body.prediction_id, note=body.note)
    # position_shares 已是『含全部历史反馈的当前持仓』，本笔操作直接作用于当前持仓即可。
    # 用全精度回放，避免四舍五入误差在反复买卖/反向还原基准时累积漂移。
    replayed, pos = _replay_raw([new_fb], p.position_shares or 0.0, p.position_cost or 0.0)
    new_fb.realized_pnl = pos["realized"]
    # 维护当前持仓：买入/卖出后写回项目，使『持仓』在详情/列表/圆桌各处一致可见
    p.position_shares = round(pos["shares"], 4)
    p.position_cost = pos["avg"]
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

    # position_shares 已是『含全部反馈回放的当前持仓』；时间轴起点用反向回放还原建仓起点，
    # 再正向重放反馈得到历史持仓与逐笔实现盈亏（避免对反馈二次累加）。
    init_s, init_c = _holding_initial(fbs, p.position_shares or 0.0, p.position_cost or 0.0)
    replayed, _ = replay(fbs, init_s, init_c)
    realized_map = {f.id: r for f, r in replayed}

    # 当前持仓面板：直接取维护值（shares/cost），实现盈亏为各反馈累加
    pos = dict(
        shares=round(float(p.position_shares or 0.0), 4),
        avg=round(float(p.position_cost or 0.0), 2),
        realized=round(sum(float(f.realized_pnl or 0.0) for f in fbs), 2),
    )

    # pnl 曲线：按行情日期 mark-to-market（以建仓起点为基准，正向重放反馈）
    # 唯一实现见 _pnl_curve —— 每日持仓日报复用同一段，保证两处口径一致
    pnl_curve = _pnl_curve(mps, fbs, init_s, init_c)

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


@app.get("/api/overview")
def overview(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """交易仪表台：当前用户（管理员为全部）的持仓总览 + 汇总指标 + 最近交易记录。"""
    q = db.query(Project)
    if not u.is_admin:
        q = q.filter(Project.owner_id == u.id)
    return _build_overview(q.order_by(Project.id).all(), db)


@app.get("/api/guest/overview")
def guest_overview(db: Session = Depends(get_db)):
    """游客模式：仅聚合代码为 GUEST_STOCK_CODE 的公开项目（只读）。"""
    rows = db.query(Project).filter(Project.code == GUEST_STOCK_CODE).order_by(Project.id).all()
    return _build_overview(rows, db)


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
    - 否则拉取并按**品种语义**写入（见 _store_rows：场内覆盖式重写、场外合并写入），
      更新 last_fetch_date 为今天。

    ⚠️ 2026-10-16 修复：此前这里写的是「只插不存在的交易日」（只增不改）。场内品种是
    前复权序列，除权后**所有**历史价格会整体平移，只补新日期会让除权点两侧混着两套
    复权基准（老日期旧基准、新日期新基准）→ 曲线假跳空、策略信号失真。现改为与登录
    通道 /api/projects/{pid}/fetch 完全相同的写入语义。
    """
    p = _guest_project_guard(pid, db)
    mk = normalize_market(p.market)
    today_s = _cn_today()
    latest = db.query(MarketPrice.date).filter(MarketPrice.project_id == p.id).order_by(MarketPrice.date.desc()).first()
    latest_date = latest[0] if latest else None
    if latest_date and latest_date >= today_s:
        return {"updated": False, "already_today": True, "latest_date": latest_date,
                "message": f"今日行情已是最新（{latest_date}），无需重复获取"}
    if (p.last_fetch_date or "") == today_s:
        return {"updated": False, "already_today": True, "latest_date": latest_date,
                "message": f"今日已拉取行情（最新 {latest_date}），无需重复获取"}
    days, have = _resolve_days(p, mk, 250, False, db)
    try:
        rows, source = fetch_hist(p.code, mk, days)
    except Exception as e:
        return {"updated": False, "already_today": False, "latest_date": latest_date,
                "error": True, "message": f"行情拉取失败：{e}"}
    if not rows:
        return {"updated": False, "already_today": False, "latest_date": latest_date,
                "error": True, "message": "未获取到任何行情数据"}
    if have and len(rows) < have:
        return {"updated": False, "already_today": False, "latest_date": latest_date,
                "error": True, "message": _shrink_guard_msg(mk, len(rows), have)}
    added, updated_n, rewritten = _store_rows(p, mk, rows, db)
    p.last_fetch_date = today_s
    # 同步成功后，自动把项目名更新为标的名称（查询失败则保留原名）
    stock_name = None
    try:
        stock_name = fetch_symbol_name(p.code, mk)
        if stock_name:
            p.name = stock_name
    except Exception as e:
        logger.warning("游客标的名称查询失败（保留原项目名）：%s", e)
    db.commit()
    new_latest = max(r[0] for r in rows)
    name = stock_name or p.name
    if not (added or updated_n or rewritten):
        return {"updated": False, "already_today": True, "latest_date": new_latest, "name": name,
                "message": f"行情已是最新（{new_latest}），无需更新，来源：{source}"}
    if rewritten:      # 场内：整段重写（前复权基准随除权变化）
        detail, count = f"已重写 {len(rows)} 个交易日", len(rows)
    else:              # 场外：合并写入
        detail = f"新增 {added} 个交易日" + (f"、修正 {updated_n} 个" if updated_n else "")
        count = added
    return {"updated": True, "already_today": new_latest >= today_s, "latest_date": new_latest,
            "count": count, "source": source, "name": name, "rewritten": bool(rewritten),
            "message": f"行情已更新至最新（{new_latest}），{detail}，来源：{source}"}


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
    {"name": "稳健配置", "role": "元宝", "style": "balanced"},
    {"name": "趋势跟踪", "role": "DeepSeek", "style": "trend"},
    {"name": "风险控制", "role": "豆包", "style": "risk"},
]

# 风格模板（人格 / 风格提示词的**可复用模板**，与席位解耦）。
# 
# - `key` 是内置模板的**稳定标识**，幂等播种只认它：用户把模板改名后，
#   下次启动**不会**再播种出一份重复的。key 不要改，name/summary/persona 随便改。
# - 前三个（balanced / trend / risk）的 persona 与历史上 DEFAULT_SEATS 写死的文本
#   **逐字一致**，这样既有的默认席位可以被无损迁移到模板（生效提示词一字不变）。
# - 席位若不用模板，走「自定义」分支（style_id 为空 + 自身 persona）。
DEFAULT_STYLES = [
    {"key": "balanced", "name": "稳健配置", "summary": "仓位控制与安全边际优先，低频操作",
     "persona": "风格稳健保守：重视仓位控制与安全边际，偏好低频操作，"
                "对高估值与高波动标的保持谨慎，建议以持有观察为主、设定明确止损线。"},
    {"key": "trend", "name": "趋势跟踪", "summary": "顺势而为，弱化主观、强调纪律",
     "persona": "风格趋势跟踪：尊重价格与量能的方向，顺势而为，"
                "对突破关键位/均线的标的给予跟进建议，弱化主观判断、强调纪律执行。"},
    {"key": "risk", "name": "风控优先", "summary": "先看回撤与集中度，宁可错过不可做错",
     "persona": "风格风险优先：首先评估回撤与集中度风险，"
                "对单一标的占比过高或涨幅过大的持仓提示止盈/分散，宁可错过不可做错。"},
    {"key": "value", "name": "价值投资", "summary": "看内在价值与安全边际，赚估值修复的钱",
     "persona": "风格价值投资：以内在价值与安全边际为核心，关注估值分位、盈利质量与现金流，"
                "对价格显著低于价值的标的给出逢低关注建议，不为短期波动与题材买单。"},
    {"key": "growth", "name": "成长赛道", "summary": "看景气度与空间，容忍高估值",
     "persona": "风格成长投资：重点判断行业景气度与成长空间，愿意为确定性成长支付溢价，"
                "关注营收/利润增速与产业趋势，对景气拐点向下或逻辑被证伪的持仓果断降级。"},
    {"key": "macro", "name": "宏观自上而下", "summary": "从利率、流动性与政策推出资产配置",
     "persona": "风格宏观自上而下：先判断利率、流动性与政策取向，再落到大类资产与行业配置，"
                "强调持仓与宏观环境的匹配度，对逆风环境下的高贝塔持仓给出减配建议。"},
    {"key": "contrarian", "name": "逆向博弈", "summary": "在情绪极端处做反向思考",
     "persona": "风格逆向博弈：关注市场情绪与拥挤度，在一致性预期极强时提示反向风险，"
                "对超跌但有基本面支撑的标的给出左侧关注建议，避免追高与情绪化操作。"},
    {"key": "quant", "name": "量化纪律", "summary": "按信号与规则执行，不做主观判断",
     "persona": "风格量化纪律：一切以可复现的信号与规则为准，不做主观判断与故事化推理，"
                "明确给出信号方向、触发条件与失效条件，信号不成立时明确建议观望。"},
]

# 「风格模板首次迁移」标记（落在 settings 表）：席位→模板的无损迁移**只能做一次**，
# 详见 _ensure_default_seats 的说明。
STYLE_INIT_FLAG = "rt.style_init_migrated"

# LLM 提供方默认配置（OpenAI 兼容协议；用户可在「AI 设置」覆盖）# LLM 提供方清单 —— **单一数据源**，前端不再硬编码 provider 表，一律由
# /api/roundtable/settings 下发。除下列内置项外，设置页还支持任意**自定义服务商**
# （名称 + base_url + model + key）：协议都走 OpenAI 兼容的 /chat/completions，
# 所以后端不做白名单限制，只校验名称合法性。
#
# ⚠️ `host` 供设置页做「本机可达性」探测。本 VPS **只有 IPv6 出口**（无 IPv4 默认路由，
# `ip -4 route get 1.1.1.1` → Network is unreachable），因此只有域名带**真 AAAA** 的
# 服务商才连得上：deepseek / moonshot 均为 IPv4-only，在本机上必然失败。
# 二者仍保留在内置表（换到有 IPv4 的机器即可用），页面上会明确标红提示。
#
# 2026-10-17 用**假 Key 实测**各家真实响应（验证链路真的通，而不是只做了 TCP 握手）：
#   zhipu / qwen / doubao / siliconflow / openrouter → 401 鉴权错（链路正常，只差真 Key）
#   openai → **403 unsupported_country_region_territory**：网络通，但按地区拒绝中国 IP，
#            故实际不可用；保留只为完整性，以及将来换机房/加代理时能用
#   deepseek / moonshot → Name or service not known（本机无 AAAA，**直连**必然失败）；
#            但经 rss-relay 中继（白名单已含二者）实测均为 401 鉴权错 —— 链路通，
#            只差真 Key。deepseek 生产已配 Key 走中继正常跑（见新闻 AI 加工层）；
#            moonshot 待用户配 Key，配上即可用（2026-10-17 复测：中继 3.9s 返 401）。
# 内置服务商：**必须**是 OpenAI 兼容的 /chat/completions 协议（见 llm.py）。
# 字段：label 中文名 / base_url 默认接口根 / model 默认模型 / host 可达性探测目标
#      （默认不带端口 = 443；本地自建可写 host:port）/ group 设置页分组。
# model 只是默认值，厂商改版较快，设置页里可随时改；不要在这里放易过期的型号为主推。
PROVIDER_DEFAULTS = {
    # ---- 国内 ----
    "zhipu":       {"label": "智谱 GLM",   "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "model": "glm-5.3-flash", "host": "open.bigmodel.cn", "group": "国内"},
    "qwen":        {"label": "通义千问",   "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen-plus", "host": "dashscope.aliyuncs.com", "group": "国内"},
    "doubao":      {"label": "火山豆包",   "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "model": "doubao-seed-2-1-pro-260915", "host": "ark.cn-beijing.volces.com", "group": "国内"},
    "deepseek":    {"label": "DeepSeek",   "base_url": "https://api.deepseek.com",
                    "model": "deepseek-flash", "host": "api.deepseek.com", "group": "国内",
                    # 推理模型：思考与正文共享 max_tokens，圆桌长提示词下思考发散会烧光预算
                    # （900→1585 字思考、2000→5249 字，reasoning_effort=low 也压不住，
                    # 2026-10-17 实测）。故默认关思考；兜底逻辑见 llm.chat_complete。
                    "thinking": "disabled"},
    "moonshot":    {"label": "Kimi 月之暗面", "base_url": "https://api.moonshot.cn/v1",
                    "model": "kimi-k3", "host": "api.moonshot.cn", "group": "国内"},
    "siliconflow": {"label": "硅基流动",   "base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-7B-Instruct", "host": "api.siliconflow.cn", "group": "国内"},
    "qianfan":     {"label": "百度千帆 · 文心", "base_url": "https://qianfan.baidubce.com/v2",
                    "model": "ernie-5.0", "host": "qianfan.baidubce.com", "group": "国内"},
    "hunyuan":     {"label": "腾讯混元",   "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
                    "model": "hunyuan-turbo", "host": "api.hunyuan.cloud.tencent.com", "group": "国内"},
    "spark":       {"label": "讯飞星火",   "base_url": "https://spark-api-open.xf-yun.com/v1",
                    "model": "4.0Ultra", "host": "spark-api-open.xf-yun.com", "group": "国内"},
    "stepfun":     {"label": "阶跃星辰",   "base_url": "https://api.stepfun.com/v1",
                    "model": "step-3.7-flash", "host": "api.stepfun.com", "group": "国内"},
    "yi":          {"label": "零一万物 Yi", "base_url": "https://api.lingyiwanwu.com/v1",
                    "model": "yi-lightning", "host": "api.lingyiwanwu.com", "group": "国内"},
    "baichuan":    {"label": "百川智能",   "base_url": "https://api.baichuan-ai.com/v1",
                    "model": "Baichuan4-Turbo", "host": "api.baichuan-ai.com", "group": "国内"},
    # TypeSafe 结构化判断（非 chat completions 协议，走 llm.systemone_judge）：
    # 只输出判断概率（~20 tokens/次），不生成自然语言。用于资讯重要度初筛等
    # 纯判断场景；市场概要等生成任务切到它会明确报错（见 newsai.build_digest）。
    # ⚠️ ai.xiaohaima-edu.com 是 Sub2API 聚合中转平台（非官方），共享 Key 有配额限制。
    "jev":         {"label": "Jev TypeSafe", "base_url": "https://ai.xiaohaima-edu.com",
                    "model": "jev-latest", "host": "ai.xiaohaima-edu.com", "group": "国内",
                    "protocol": "systemone"},
    # ---- 海外 ----
    "openai":      {"label": "OpenAI",     "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini", "host": "api.openai.com", "group": "海外"},
    "openrouter":  {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-4o-mini", "host": "openrouter.ai", "group": "海外"},
    "gemini":      {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                    "model": "gemini-3.8-flash", "host": "generativelanguage.googleapis.com", "group": "海外"},
    "grok":        {"label": "xAI Grok",   "base_url": "https://api.x.ai/v1",
                    "model": "grok-3-mini", "host": "api.x.ai", "group": "海外"},
    "mistral":     {"label": "Mistral",    "base_url": "https://api.mistral.ai/v1",
                    "model": "mistral-large-latest", "host": "api.mistral.ai", "group": "海外"},
    "groq":        {"label": "Groq",       "base_url": "https://api.groq.com/openai/v1",
                    "model": "llama-3.3-70b-versatile", "host": "api.groq.com", "group": "海外"},
    # ---- 本地 / 自建（host 带端口，探测按实际端口而不是 443）----
    "ollama":      {"label": "Ollama（本地）", "base_url": "http://127.0.0.1:11434/v1",
                    "model": "qwen2.5:7b", "host": "127.0.0.1:11434", "group": "本地 / 自建"},
}

# 设置页分组显示顺序（自定义服务商固定排最后）
PROVIDER_GROUPS = ["国内", "海外", "本地 / 自建"]
CUSTOM_GROUP = "自定义"

# 自定义服务商名称规则：允许字母数字下划线连字符，长度 1~32。
# 既是安全边界（该名会被拼进 settings 的 key），也留足了自定义空间。
_PROVIDER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_reach_cache: dict = {}      # {"host:port": (探测时间戳, 是否可达)}
_REACH_TTL = 300             # 秒。避免每次进设置页都全量重探
_REACH_TIMEOUT = 1.5         # 秒。实测可达域名握手 0.02~0.7s，1.5 秒足够且不拖慢设置页


def _host_of(url: str) -> str:
    """从 base_url 提取主机名（自定义服务商没有内置 host 时用）。"""
    try:
        return urlparse((url or "").strip()).hostname or ""
    except Exception:
        return ""


def _reach_target(base_url: str, hint: str = "") -> tuple:
    """探测目标 (host, port)：**以实际 base_url 为准**，hint 仅在 base_url 为空时兜底。

    以 base_url 为准很关键：用户在设置页把某个服务商改指到别处（代理 / 内网网关）后，
    要探的必须是新地址；本地自建（如 http://127.0.0.1:11434/v1）也只有按 base_url
    才能拿到真实端口——写死 443 会恒判「不可达」。
    """
    u = urlparse((base_url or "").strip())
    host = u.hostname or ""
    try:
        port = u.port or 0
    except ValueError:          # 端口不合法（如 :abc）时当作没写
        port = 0
    if not host:
        hint = (hint or "").strip()
        if ":" in hint:
            h, _, p = hint.rpartition(":")
            if p.isdigit():
                return h, int(p)
        return hint, 443
    if not port:
        port = 443 if (u.scheme or "https").lower() == "https" else 80
    return host, port


def _host_reachable(target) -> bool:
    """探测本机能否与 (host, port) 建连（带 5 分钟缓存）。

    刻意走 `socket.create_connection`：与 `llm.chat_complete` 是**同一套解析路径**
    （llm.py 在导入时把 getaddrinfo 固定成 AF_INET6），所以探测结果就等于真实可用性，
    IPv4-only 的域名在这里会因无 AAAA 而立刻失败，不会给出假阳性。
    只做 TCP 握手，不发业务请求、不带 Key。
    """
    host, port = target
    if not host:
        return False
    key = f"{host}:{port}"
    now = time.time()
    hit = _reach_cache.get(key)
    if hit and now - hit[0] < _REACH_TTL:
        return hit[1]
    try:
        with socket.create_connection((host, port), timeout=_REACH_TIMEOUT):
            ok = True
    except Exception:
        ok = False
    _reach_cache[key] = (now, ok)
    return ok


# 中继（rss-relay）域名白名单镜像：与 rss-relay/wrangler.toml 的 ALLOW_HOSTS 保持一致。
# ⚠️ 改 wrangler.toml 白名单后必须同步这里，否则设置页会把「白名单已放行、其实能用」的
# 服务商错标成不可达。本表仅用于 UI 状态展示；真实出站路由不依赖它（_llm_route 只看可达性，
# 白名单外的域名 Worker 侧会直接 403，调用方从健康度面板看得到）。
RELAY_HOST = "relay.kapibala.icu"
RELAY_ALLOWED_HOSTS = (
    "aihot.news", "chinanews.com.cn", "wallstreetcn.com", "jiemian.com",
    "eastmoney.com", "api.deepseek.com", "api.moonshot.cn",
    "generativelanguage.googleapis.com", "qbitai.com",
    "api.hunyuan.cloud.tencent.com", "spark-api-open.xf-yun.com",
)


def _relay_host_allowed(host: str) -> bool:
    """host 是否命中中继白名单（与 Worker 的 ALLOW_HOSTS 同为后缀匹配）。"""
    h = (host or "").strip().lower().rstrip(".")
    return bool(h) and any(h == d or h.endswith("." + d) for d in RELAY_ALLOWED_HOSTS)


def _is_local_host(host: str) -> bool:
    """本地/内网地址：中继是公网 Worker，转发不到这些地址（如 ollama 127.0.0.1）。"""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if h == "localhost" or h.endswith(".localhost") or h.endswith(".local"):
        return True
    if h.startswith("127.") or h.startswith("192.168.") or h.startswith("10."):
        return True
    if h.startswith("172."):
        parts = h.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def _relay_usable(db) -> bool:
    """中继通道本身是否可用：relay 域名本机可达 + news.relay_url/key 已配置。

    只读 settings 表与 _reach_cache（带 5 分钟缓存），开销可忽略。
    """
    if not _host_reachable((RELAY_HOST, 443)):
        return False
    conf = newsfetch.relay_conf(db)
    return bool(conf.get("url") and conf.get("key"))


def _reach_mode(direct_ok: bool, host: str, relay_ok: bool) -> str:
    """服务商可达性三态（设置页徽标用）：

    direct —— 本机直连可达；
    relay  —— 直连不可达（典型：无 AAAA 的 IPv4-only 域名），但中继通道可用且该域名
              已在中继白名单内 → 调用会自动走中继，现在就能用（如 DeepSeek / Moonshot）；
    none   —— 真不可用：本地/内网地址（中继转发不到）、域名不在中继白名单、
              或中继本身不可达（relay 挂了 / 没配中继密钥）。
    """
    if direct_ok:
        return "direct"
    if relay_ok and not _is_local_host(host) and _relay_host_allowed(host):
        return "relay"
    return "none"


def _llm_route(db, base_url: str) -> dict:
    """LLM 出站路由：目标主机本机不可达（无 AAAA，如 DeepSeek/Moonshot）且中继
    已配置时，返回走 rss-relay POST 通道所需的参数；否则返回空 dict（直连）。

    只在主线程、有 db 的地方调用（内部读 settings 表）；可达性探测有 5 分钟缓存。
    误判方向安全：把「可达」误判成「不可达」只是多绕一跳中继（结果一样），
    反过来才危险——而 _host_reachable 的探测路径与 chat_complete 完全一致。
    """
    host, port = _reach_target(base_url)
    if _host_reachable((host, port)):
        return {}
    conf = newsfetch.relay_conf(db)
    if not (conf.get("url") and conf.get("key")):
        return {}
    return {"via_relay": conf["url"], "relay_key": conf["key"]}


def _llm_stats_base(seat_id, m: dict, model, conf: dict = None) -> dict:
    """构造 llm_call_log 的 stats 雏形（调用前的静态部分）。

    m 为 seat_meta 项（含 provider/name）。conf 非空时补齐 via_relay。
    """
    conf = conf or {}
    return {
        "seat_id": seat_id,
        "seat_name": m.get("name") or "",
        "provider": m.get("provider") or "",
        "model": model or "",
        "via_relay": 1 if conf.get("via_relay") else 0,
        "ok": 0, "error": "", "finish_reason": "",
        "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
        "duration_ms": 0, "thinking": "", "thinking_fallback": 0,
    }


def _llm_error_kind(ok: bool, error: str, finish_reason: str = "") -> str:
    """把一次调用的成败归类成稳定枚举（供健康度按类聚合，不逐字 match 错误文本）。"""
    if ok:
        return "ok"
    e = (error or "")
    if "返回为空" in e or "返回无 choices" in e:
        # 推理模型思考耗尽预算是独立一类：它跟「没配 Key」的改进动作完全不同
        return "budget" if finish_reason == "length" else "empty"
    if "HTTP 403" in e and "forbidden" in e:
        return "relay_forbidden"     # 中继密钥错（Worker 侧的 403）
    if "HTTP 4" in e:
        return "http4xx"
    if "HTTP 5" in e:
        return "http5xx"
    low = e.lower()
    if ("unreachable" in low or "network" in low or "timed out" in e
            or "resolve" in low or "not known" in low or "name or service" in low):
        return "network"
    return "other"


def _log_llm_call(db, seat_id, seat_name: str, stats: dict, ok: bool, error: str = ""):
    """把一次 LLM 调用写入 llm_call_log（主线程、事务内）。失败只 warning 不回滚观点。"""
    row = LLMCallLog(
        seat_id=seat_id,
        seat_name=seat_name or "",
        provider=stats.get("provider") or "",
        model=stats.get("model") or "",
        ok=1 if ok else 0,
        error_kind=_llm_error_kind(ok, error, stats.get("finish_reason") or ""),
        error=(error or stats.get("error") or "")[:500],
        finish_reason=stats.get("finish_reason") or "",
        prompt_tokens=stats.get("prompt_tokens") or 0,
        completion_tokens=stats.get("completion_tokens") or 0,
        reasoning_tokens=stats.get("reasoning_tokens") or 0,
        duration_ms=stats.get("duration_ms") or 0,
        via_relay=stats.get("via_relay") or 0,
        thinking=stats.get("thinking") or "",
        thinking_fallback=1 if stats.get("thinking_fallback") else 0,
    )
    db.add(row)
    db.commit()
    return row


def _known_providers(db) -> list:
    """内置提供方 + 用户自建的自定义提供方（扫 settings 里出现过的 `llm.<name>.*`）。

    settings 表很小（十几条），全表扫描的代价可忽略。
    """
    names = list(PROVIDER_DEFAULTS.keys())
    for (key,) in db.query(Setting.key).all():
        if not key or not key.startswith("llm."):
            continue
        parts = key.split(".")
        if len(parts) == 3 and parts[1] and parts[2] in ("api_key", "base_url", "model"):
            if parts[1] not in names:
                names.append(parts[1])
    return names

# 提示词输出格式约定（行式结构，前端 prewrap 渲染即整齐，避免表格）
PROMPT_OUTPUT_SPEC = """# 输出格式（严格遵守，不要使用表格）
## 总体判断
（一段话，120字以内，点评今日大盘对整体持仓的影响）
## 逐持仓点评
- 标的名：建议（持有/加仓/减仓）｜一句理由
## 风险提示
（一条当前最需警惕的风险）"""

_LLM_SYSTEM = "你是严谨的A股投研助手。严格基于用户给出的持仓与行情快照作答，不臆测快照之外的数据，并严格遵守用户指定的输出格式。"

# 圆桌席位「接入方式」取值：
#   manual = 手动回填（复制提示词 → 外部咨询 → 粘贴反馈）
#   auto   = LLM Key 自动（后端直调 LLM 生成）
#   mcp    = MCP 接入（本期仅为标注/预留，行为等同 manual，不参与自动生成）
RT_MODES = ("manual", "auto", "mcp")
RT_MODE_HINT = "mode 仅支持 manual / auto / mcp"


def _ensure_default_styles(db):
    """按 `builtin_key` 幂等播种内置风格模板：只补缺失的，**不覆盖**用户改过的内容。

    所以用户重命名 / 改写内置模板都是安全的，不会被下一次启动打回原样
    （幂等认的是 key 而不是 name —— 认 name 的话改名后会再播种一份重复的）。
    """
    have = {s.builtin_key for s in db.query(RoundtableStyle)
            .filter(RoundtableStyle.builtin_key != "").all()}
    changed = False
    for i, s in enumerate(DEFAULT_STYLES):
        if s["key"] in have:
            continue
        db.add(RoundtableStyle(name=s["name"], summary=s.get("summary", ""),
                               persona=s.get("persona", ""), builtin=1,
                               builtin_key=s["key"], sort=i))
        changed = True
    if changed:
        db.commit()


def _resolve_persona(seat, db) -> str:
    """席位**生效**的风格提示词 —— 模板与席位解耦的唯一解析点。

    选了模板 → 用模板正文（改模板，所有引用它的席位即时生效）；
    未选     → 用席位自身的 persona（「自定义」/ 历史数据）。

    任何要拼提示词的地方都必须走这里，不要直接读 seat.persona。
    """
    sid = getattr(seat, "style_id", None)
    if sid:
        st = db.get(RoundtableStyle, sid)
        if st and (st.persona or "").strip():
            return st.persona
    return seat.persona or ""


def _style_id_by_key(db, key: str):
    if not key:
        return None
    st = (db.query(RoundtableStyle)
          .filter(RoundtableStyle.builtin_key == key).first())
    return st.id if st else None


def _ensure_default_seats(db):
    """播种内置风格模板 + 3 个默认席位（幂等）。

    顺带做一次**一次性**的无损迁移：历史席位的 persona 与内置模板文本**逐字相同**时，
    把 style_id 指向该模板（生效提示词一字不变），让它从此享受「改模板全局生效」。

    ⚠️ 迁移只在**首次**执行（`Setting` 打标记 `rt.style_init_migrated`），不能每次都跑：
    席位切回「自定义」时其 persona 是**原样保留**的（这是刻意的，切回来原文还在），
    所以「选了模板 → 切回自定义」的席位，正文恰好还是模板原文 —— 若每次都迁移，
    下一次任意圆桌请求就会把它悄悄迁回模板，把用户刚才的选择顶掉。
    """
    _ensure_default_styles(db)
    existing = {s.name: s for s in db.query(RoundtableSeat).all()}
    changed = False
    for i, s in enumerate(DEFAULT_SEATS):
        name = s["name"]
        cur = existing.get(name)
        if cur is None:
            db.add(RoundtableSeat(name=name, persona="", style_id=_style_id_by_key(db, s.get("style")),
                                  role=s.get("role", ""), mode="manual",
                                  provider="deepseek", sort=i))
            changed = True
        elif not (cur.role or "").strip() and s.get("role"):
            cur.role = s["role"]
            changed = True

    if db.get(Setting, STYLE_INIT_FLAG) is None:
        by_text = {}   # persona 正文 → 模板 id（供无损迁移比对）
        for st in db.query(RoundtableStyle).all():
            if (st.persona or "").strip():
                by_text.setdefault(st.persona.strip(), st.id)
        for cur in existing.values():
            if not cur.style_id and (cur.persona or "").strip():
                tid = by_text.get((cur.persona or "").strip())
                if tid:
                    cur.style_id = tid
                    changed = True
        db.add(Setting(key=STYLE_INIT_FLAG, value="1"))
        changed = True

    if changed:
        db.commit()


def _get_llm_conf(db, provider: str) -> dict:
    """读取某提供方的 LLM 配置（含 api_key，仅服务端使用，绝不回传前端）。"""
    rows = dict(db.query(Setting.key, Setting.value)
                .filter(Setting.key.like(f"llm.{provider}.%")).all())
    # 自定义服务商不在内置表里 → 没有默认值，base_url 必须由用户自己填。
    # （此前这里 fallback 到 deepseek 的默认，会让自定义服务商悄悄指向 api.deepseek.com）
    dflt = PROVIDER_DEFAULTS.get(provider) or {}
    return {
        "api_key": rows.get(f"llm.{provider}.api_key") or "",
        "base_url": rows.get(f"llm.{provider}.base_url") or dflt.get("base_url", ""),
        "model": rows.get(f"llm.{provider}.model") or "",
        # 协议：openai（默认，/chat/completions）或 systemone（/v1/systemone，
        # TypeSafe 判断，仅 jev 内置）。自定义服务商按 openai 处理。
        "protocol": dflt.get("protocol", "openai"),
    }


# 资讯 AI 加工（评分 / 市场概要）用的服务商：默认 deepseek，可在设置里覆盖。
NEWS_LLM_PROVIDER_KEY = "news.llm_provider"


def _check_seat_provider(db, provider: str):
    """圆桌席位的服务商校验：systemone 协议（Jev）不能做席位——它只输出判断
    概率、不生成观点正文。放在保存入口挡掉，而不是等生成时才报错。
    """
    p = (provider or "").strip()
    if not p:
        return
    if (PROVIDER_DEFAULTS.get(p) or {}).get("protocol") == "systemone":
        raise HTTPException(
            400, f"服务商「{p}」是 TypeSafe 判断协议（/v1/systemone），只能做资讯评分等"
                 f"结构化判断、不能生成观点正文。圆桌席位请选其他服务商；"
                 f"资讯 AI 服务商可在「AI 设置」顶部单独切换。")
    if p not in PROVIDER_DEFAULTS and not _get_llm_conf(db, p).get("base_url"):
        raise HTTPException(400, f"未知服务商「{p}」：请先在「AI 设置」添加自定义服务商")


def news_llm_conf(db) -> dict:
    """资讯 AI 加工的 LLM 配置：读 Setting news.llm_provider（默认 deepseek）的
    llm.* 配置，按 _llm_route 补出站路由（不可达自动走中继），并带上思考开关。

    没配 Key 直接抛错——调用方（接口 / 定时脚本）会把错误明明白白显示出来，
    而不是静悄悄跳过（资讯加工失败肉眼不可见，最容易拖成「功能像没上线」）。
    """
    provider = (newsfetch.get_setting(db, NEWS_LLM_PROVIDER_KEY) or "deepseek").strip() or "deepseek"
    conf = _get_llm_conf(db, provider)
    if not conf.get("api_key"):
        raise RuntimeError(f"未配置 {provider} 的 API Key（请到「AI 设置」填写）")
    conf.update(_llm_route(db, conf["base_url"]))
    if not conf.get("model"):
        conf["model"] = (PROVIDER_DEFAULTS.get(provider) or {}).get("model", "")
    if not conf.get("model"):
        raise RuntimeError(f"未指定模型，且服务商「{provider}」没有默认模型")
    conf["disable_thinking"] = (
        (PROVIDER_DEFAULTS.get(provider) or {}).get("thinking") == "disabled")
    return conf


def _seat_out(s: RoundtableSeat, db) -> RoundtableSeatOut:
    conf = _get_llm_conf(db, s.provider or "deepseek")
    # 只回传席位**自身**的 persona（自定义分支用），模板正文不回传——
    # 前端要的是「这个席位选了哪个模板」，正文改了模板自己会生效，不必经席位同步。
    style_name = ""
    if s.style_id:
        st = db.get(RoundtableStyle, s.style_id)
        style_name = (st.name or "") if st else ""
    return RoundtableSeatOut(
        id=s.id, name=s.name or "", persona=s.persona or "",
        style_id=s.style_id, style_name=style_name,
        mode=s.mode or "manual", provider=s.provider or "", model=s.model or "",
        role=s.role or "",
        enabled=1 if s.enabled else 0, has_key=bool(conf.get("api_key")),
        sort=s.sort or 0,
        created_at=s.created_at.isoformat() if s.created_at else None,
    )


def _effective_position(p, db):
    """项目当前持仓 = 维护值 position_shares/position_cost（已含全部反馈回放，提交买卖时写回）。
    返回 (shares, avg, feedbacks按时间正序)。与项目页盈亏曲线、圆桌快照口径一致。"""
    fbs = (db.query(Feedback).filter(Feedback.project_id == p.id)
           .order_by(Feedback.date, Feedback.id).all())
    return float(p.position_shares or 0.0), float(p.position_cost or 0.0), fbs


def _build_snapshot(u, db) -> dict:
    """圆桌「今日快照」：账户三指标 + 大盘指数 + 当前用户可见持仓。

    totals 走 _account_totals（账户三指标的唯一定义，与仪表台 KPI 同源）——
    圆桌页顶部指标条直接用它，不要在页面里另算一遍。
    """
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
        "totals": _account_totals(projects, db),   # 总市值 / 浮动盈亏 / 累计已实现（唯一定义）
        "indices": indices,
        "holdings": holdings,
    }


def _user_holdings(u, db) -> list:
    """当前用户有持仓的标的 [{name, code}]（可见性口径与 _build_snapshot 一致：
    管理员看全部，普通用户只看 owner_id 为自己的）。"""
    q = db.query(Project)
    if not u.is_admin:
        q = q.filter(Project.owner_id == u.id)
    out = []
    for p in q.order_by(Project.pinned.desc(), Project.created_at.desc()).all():
        shares, _cost, _fbs = _effective_position(p, db)
        if shares > 0:
            out.append({"name": p.name or "", "code": p.code or ""})
    return out


def _select_news_injection(u, db, date_s: str):
    """给席位提示词选「近期相关资讯」。**必须在主线程调用**（选条要读库，
    工作线程不碰 SQLAlchemy Session）。

    返回 None = 选条失败（本次提示词不带资讯节，退回老行为，记 warning）；
    返回 [] = 选条成功但近期没有达标资讯（提示词里明示「暂无」，让模型知道
    不是被漏喂，任务规格据此要求标「纯技术面判断」）。
    """
    try:
        codes = [h["code"] for h in _user_holdings(u, db) if h.get("code")]
        return newsai.select_news_for_prompt(db, codes, today=date_s)
    except Exception as e:
        logger.warning("资讯选条失败（本次提示词不带资讯）：%s", e)
        return None


def _snapshot_prompt_text(snap: dict, seat: RoundtableSeat, persona: Optional[str] = None,
                          news_items: Optional[list] = None) -> str:
    """把今日快照 + 席位人格 + 近期资讯 + 输出格式组装成完整提示词（自动/手动通道共用）。

    ⚠️ `persona` 必须由调用方用 `_resolve_persona(seat, db)` 解析后传入——席位的风格
    可能来自风格模板而不是 seat.persona 本身。本函数不碰 db（自动通道在工作线程里拼提示词，
    而 SQLAlchemy Session 非线程安全）。不传则退回 seat.persona（仅供无 db 的测试用）。

    `news_items`：由调用方在主线程选好（newsai.select_news_for_prompt）后传入——
    工作线程不碰 db，选条要读库，所以必须外面选好。None = 本次不带资讯（老行为）。
    """
    style = persona if persona is not None else (seat.persona or "")
    L = ["# 角色", f"你是「{seat.name or '分析师'}」。{style or '语气专业、观点明确。'}"]
    if seat.role:
        L.append(f"你当前扮演的 AI 产品是「{seat.role}」，请以该产品的口吻、表达习惯与视角作答"
                 f"（例如它是谁、擅长什么、语言风格如何），让用户感觉这是 {seat.role} 给出的独立观点。")
    L += ["", f"# 今日快照（{snap['date']}）", "## 大盘"]
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
    # 近期相关资讯（2026-10-17 接入）：同一份数据也喂论坛「市场概要」帖。
    # 刻意放在持仓之后、任务之前——模型先看账户、再看世界，最后才是任务。
    if news_items is not None:
        L += ["", newsai.news_prompt_block(news_items)]
    L += ["", "# 任务",
          "1. 先点评今日大盘对整体持仓的影响；",
          "2. 逐个持仓给出持有/加仓/减仓建议与一句理由；**每条建议必须引用上方资讯的条目编号"
          "作为依据**（如「依据资讯 1、3」），与资讯无关的判断明确标注「纯技术面判断」；",
          "3. 指出当前最需警惕的一个风险；",
          "4. 若资讯中出现与当前持仓无关、但值得关注的新方向（行业景气、政策受益等），"
          "可在末尾另起「新建标的观察」一节，只描述方向与理由（不构成建仓建议）。",
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
    if body.mode not in RT_MODES:
        raise HTTPException(400, RT_MODE_HINT)
    _check_seat_provider(db, body.provider or "deepseek")
    style_id = _check_style_ref(db, body.style_id)
    max_sort = db.query(RoundtableSeat.sort).order_by(RoundtableSeat.sort.desc()).first()
    s = RoundtableSeat(name=body.name.strip() or "新席位", persona=body.persona,
                       style_id=style_id, role=body.role or "",
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
    if body.provider is not None:
        _check_seat_provider(db, body.provider)
    # style_id 单独处理：允许「改回自定义」（传 0 或负数即清除引用），
    # 不能用通用的 `is not None` 判断——那样前端就没法把席位切回自定义了。
    if body.style_id is not None:
        s.style_id = _check_style_ref(db, body.style_id)
    for field in ("name", "persona", "mode", "provider", "model", "role", "enabled", "sort"):
        v = getattr(body, field)
        if v is not None:
            if field == "mode" and v not in RT_MODES:
                raise HTTPException(400, RT_MODE_HINT)
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


# ---- 风格模板（人格/风格提示词的可复用模板，与席位管理、AI 设置并列的独立管理项）----

def _check_style_ref(db, style_id):
    """把请求里的 style_id 归一化成「可写库的值」：<=0 或空 → None（自定义）；不存在 → 400。"""
    if style_id is None or int(style_id) <= 0:
        return None
    if not db.get(RoundtableStyle, int(style_id)):
        raise HTTPException(400, "风格模板不存在")
    return int(style_id)


def _style_out(st: RoundtableStyle, db) -> RoundtableStyleOut:
    used = db.query(RoundtableSeat).filter(RoundtableSeat.style_id == st.id).count()
    return RoundtableStyleOut(
        id=st.id, name=st.name or "", summary=st.summary or "",
        persona=st.persona or "", builtin=1 if st.builtin else 0,
        sort=st.sort or 0, used_by=used,
        created_at=st.created_at.isoformat() if st.created_at else None,
    )


@app.get("/api/roundtable/styles", response_model=list[RoundtableStyleOut])
def rt_list_styles(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """风格模板列表。登录即可读（席位卡片要显示所用模板名），写操作限管理员。

    这里刻意调 `_ensure_default_seats` 而不是 `_ensure_default_styles`：席位向模板的
    无损迁移是在前者里做的，而前端是**并发**拉 seats / styles 的 —— 只播种不迁移的话，
    styles 先返回时 `used_by` 会全算成 0（“未被席位引用”），让人误以为能删。
    """
    _ensure_default_seats(db)
    rows = db.query(RoundtableStyle).order_by(RoundtableStyle.sort, RoundtableStyle.id).all()
    return [_style_out(st, db) for st in rows]


@app.post("/api/roundtable/styles", response_model=RoundtableStyleOut, status_code=201)
def rt_create_style(body: RoundtableStyleCreate, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "模板名不能为空")
    if db.query(RoundtableStyle).filter(RoundtableStyle.name == name).first():
        raise HTTPException(400, f"已存在同名模板「{name}」")
    sort = body.sort
    if sort is None:
        mx = db.query(RoundtableStyle.sort).order_by(RoundtableStyle.sort.desc()).first()
        sort = (mx[0] + 1) if mx else 0
    st = RoundtableStyle(name=name, summary=body.summary or "",
                         persona=body.persona or "", builtin=0,
                         builtin_key="", sort=sort)
    db.add(st)
    db.commit()
    db.refresh(st)
    return _style_out(st, db)


@app.patch("/api/roundtable/styles/{style_id}", response_model=RoundtableStyleOut)
def rt_update_style(style_id: int, body: RoundtableStyleUpdate,
                    u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """内置模板可改（名/说明/正文），改了以后所有引用它的席位即时生效。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    st = db.get(RoundtableStyle, style_id)
    if not st:
        raise HTTPException(404, "风格模板不存在")
    if body.name is not None and body.name.strip() != (st.name or ""):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "模板名不能为空")
        if db.query(RoundtableStyle).filter(RoundtableStyle.name == name,
                                            RoundtableStyle.id != style_id).first():
            raise HTTPException(400, f"已存在同名模板「{name}」")
        st.name = name
    for field in ("summary", "persona", "sort"):
        v = getattr(body, field)
        if v is not None:
            setattr(st, field, v)
    db.commit()
    db.refresh(st)
    return _style_out(st, db)


@app.delete("/api/roundtable/styles/{style_id}")
def rt_delete_style(style_id: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除自定义模板。内置模板不可删；仍被席位引用的不可删（否则席位会静默退化成空风格）。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    st = db.get(RoundtableStyle, style_id)
    if not st:
        raise HTTPException(404, "风格模板不存在")
    if st.builtin:
        raise HTTPException(400, f"「{st.name}」是内置模板，不能删除（可以改名改正文）")
    used = db.query(RoundtableSeat).filter(RoundtableSeat.style_id == style_id).count()
    if used:
        raise HTTPException(400, f"还有 {used} 个席位在使用「{st.name}」，请先改掉这些席位的风格再删除")
    db.delete(st)
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
    """生成某席位今日提示词（手动通道：用户复制后去外部 AI 网页咨询）。

    与自动通道同样注入「近期相关资讯」——手动粘贴去问外部 AI 时，资讯依据
        一样要带上，否则两条通道给出的建议质量会拉开差距。
    预览不写注入日志（没有副作用；自动通道生成成功才登记）。
    """
    _ensure_default_seats(db)
    s = db.get(RoundtableSeat, seat_id)
    if not s:
        raise HTTPException(404, "席位不存在")
    snap = _build_snapshot(u, db)
    news_items = _select_news_injection(u, db, snap["date"])
    return {"seat_id": s.id, "seat_name": s.name, "date": snap["date"],
            "style_name": (db.get(RoundtableStyle, s.style_id).name
                           if s.style_id and db.get(RoundtableStyle, s.style_id) else ""),
            "news_count": len(news_items or []),
            "prompt": _snapshot_prompt_text(snap, s, _resolve_persona(s, db), news_items=news_items)}


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
        thread_id=p.thread_id,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    ) for p, name in rows]


@app.post("/api/roundtable/posts", response_model=RoundtablePostOut, status_code=201)
def rt_submit_post(body: RoundtablePostIn, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """手动通道：把外部 AI 的回复粘贴回填到对应席位（覆盖当日该席位观点）。

    同时把这条观点同步进论坛存档（同一「席位 × 日期」一个主题帖，见
    `_sync_roundtable_forum`）——观点本身已入库，存档失败只记日志、不改接口结果。
    """
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
    thread_id, archived = None, False
    try:
        thread_id, archived = _sync_roundtable_forum(db, post, s, content)
        db.commit()
    except Exception as e:                      # 存档失败不影响已保存的观点
        db.rollback()
        logger.warning("圆桌观点同步论坛失败（seat=%s date=%s）：%s", s.id, date_s, e)
        thread_id = None
    return RoundtablePostOut(
        seat_id=body.seat_id, seat_name=s.name or "", date=date_s, content=content,
        source="manual", model="", thread_id=thread_id,
        updated_at=post.updated_at.isoformat() if post.updated_at else None,
    )


@app.post("/api/roundtable/archive")
def rt_archive_post(body: RoundtableArchiveReq, u: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """补建论坛存档：把某席位某日**已录入**的观点重新同步一次。

    存在意义：保存观点时的自动同步是「失败只记日志、不回滚观点」的 ——
    观点经 `_upsert_post` 独立提交，接口照常返回成功，于是可能出现
    「观点在库里、论坛却没有存档帖、thread_id 为空」的状态。这种状态下
    前端连「查看存档」的入口都不显示，用户既看不到也补不了。本接口是
    唯一的补救入口，与保存时的自动同步**共用 `_sync_roundtable_forum`**，
    语义完全一致（幂等：已有存档且内容未变 → 不重复写，返回现有 thread_id；
    原帖被删 → 重建并改写 thread_id）。

    与保存路径的关键差别：这里**失败要如实报错**（500），因为用户是主动
    来补的，静默返回成功等于把人再次骗过去。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    s = db.get(RoundtableSeat, body.seat_id)
    if not s:
        raise HTTPException(404, "席位不存在")
    date_s = body.date or _cn_today()
    post = (db.query(RoundtablePost)
            .filter(RoundtablePost.date == date_s, RoundtablePost.seat_id == body.seat_id)
            .first())
    if not post:
        raise HTTPException(404, "该席位在该日期没有观点可存档")
    try:
        thread_id, archived = _sync_roundtable_forum(db, post, s, post.content or "")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("圆桌观点补建存档失败（seat=%s date=%s）：%s", body.seat_id, date_s, e)
        raise HTTPException(500, "补建存档失败，请查看服务端日志")
    return {"seat_id": body.seat_id, "date": date_s,
            "thread_id": thread_id, "archived": archived}


@app.get("/api/roundtable/settings")
def rt_get_settings(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """LLM 设置（脱敏）：返回**内置 + 自定义**提供方的配置状态，绝不回传 Key 本身。

    额外下发 `reachable`（本机到该 base_url 对应 host:port 的 TCP 可达性）与
    `reach_mode`（三态：direct 直连可达 / relay 直连不可达但会自动走中继、可用 /
    none 真不可用），供设置页把「有中继兜底的服务商」与「真连不上」区分开——
    本 VPS 只有 IPv6 出口，IPv4-only 的服务商（如 DeepSeek）必然直连失败，
    不提示的话用户只会看到「点了没反应」；但标成红色「不可达」同样误导，
    因为它们在调用时会自动经中继转发、实际可用。
    同时下发 `group`（国内 / 海外 / 本地 / 自定义），设置页据此分组显示。
    探测并发执行且带 5 分钟缓存：首次打开典型 <1 秒，之后瞬时返回。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    names = _known_providers(db)
    confs = {n: _get_llm_conf(db, n) for n in names}
    targets = {}
    for n in names:
        dflt = PROVIDER_DEFAULTS.get(n) or {}
        targets[n] = _reach_target(confs[n]["base_url"], dflt.get("host", ""))
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(names)))) as ex:
        reach = dict(zip(names, ex.map(_host_reachable, [targets[n] for n in names])))
    relay_ok = _relay_usable(db)
    providers = {}
    for n in names:
        dflt = PROVIDER_DEFAULTS.get(n) or {}
        conf = confs[n]
        host = _host_of(conf["base_url"]) or (dflt.get("host") or "")
        providers[n] = {
            "label": dflt.get("label") or n,
            "group": dflt.get("group") or CUSTOM_GROUP,
            "builtin": n in PROVIDER_DEFAULTS,
            "configured": bool(conf["api_key"]),
            "base_url": conf["base_url"],
            "model": conf["model"] or dflt.get("model", ""),
            "host": host,
            "reachable": reach.get(n, False),
            # reach_mode 是「实际能否调通」的三态：direct / relay（自动走中继，可用）/ none。
            # 只有 IPv6-only 的机器才需要这套；有 IPv4 的机器一切直连、relay 态不会出现。
            "reach_mode": _reach_mode(reach.get(n, False), host, relay_ok),
            "protocol": conf.get("protocol", "openai"),
        }
    return {"providers": providers, "groups": PROVIDER_GROUPS + [CUSTOM_GROUP],
            "news_llm_provider": (newsfetch.get_setting(db, NEWS_LLM_PROVIDER_KEY) or "deepseek")}


@app.post("/api/roundtable/news-provider")
def rt_set_news_provider(body: dict, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """设置「资讯 AI 服务商」（评分/概要用）：写入 Setting news.llm_provider。

    与圆桌席位服务商独立——资讯侧可用 Jev（TypeSafe 判断）省钱，圆桌仍用生成式。
    校验：服务商必须已知且已配 Key（没配 Key 切过去，定时任务只会天天报错）。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    provider = (body.get("provider") or "").strip()
    if not provider:
        raise HTTPException(400, "provider 不能为空")
    if provider not in _known_providers(db):
        raise HTTPException(400, f"未知服务商「{provider}」")
    conf = _get_llm_conf(db, provider)
    if not conf.get("api_key"):
        raise HTTPException(400, f"服务商「{provider}」未配置 API Key，请先在同一面板填写")
    newsfetch.put_setting(db, NEWS_LLM_PROVIDER_KEY, provider)
    db.commit()
    return {"ok": True, "news_llm_provider": provider}


@app.get("/api/roundtable/llm-stats")
def rt_llm_stats(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """生成健康度：LLM 调用成功率与失败分析（管理员）。

    数据源 llm_call_log（rt_generate 每次调用一行，含成败/错误分类/tokens/耗时/
    是否中继/是否触发超预算兜底）。口径：
      - 7d / 30d 两个窗口（按服务器本地日期切，UTC naive 存储）；
      - by_kind 为错误分类计数（budget=推理模型超预算、network=连接类、
        relay_forbidden=中继密钥错、http4xx/http5xx、empty、other）；
      - by_provider 给出各服务商的成败，用于「换服务商」决策；
      - fallback_7d 为超预算自动关思考兜底触发次数（>0 说明有模型在裸奔发散）；
      - recent_failures 最近 10 条失败明细（含错误串截断）。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    now = datetime.utcnow()
    rows = (db.query(LLMCallLog)
            .filter(LLMCallLog.created_at >= now - timedelta(days=30))
            .order_by(LLMCallLog.created_at.desc()).all())

    def _win(days):
        since = now - timedelta(days=days)
        return [r for r in rows if r.created_at and r.created_at >= since]

    def _agg(rs):
        ok = sum(1 for r in rs if r.ok)
        return {"total": len(rs), "ok": ok, "fail": len(rs) - ok,
                "rate": round(ok / len(rs) * 100, 1) if rs else None}

    by_kind = {}
    by_provider = {}
    fallback_7d = 0
    for r in rows:
        if not r.ok:
            by_kind[r.error_kind or "other"] = by_kind.get(r.error_kind or "other", 0) + 1
        p = r.provider or "?"
        d = by_provider.setdefault(p, {"total": 0, "ok": 0})
        d["total"] += 1
        d["ok"] += 1 if r.ok else 0
        if r.thinking_fallback and r.created_at and r.created_at >= now - timedelta(days=7):
            fallback_7d += 1
    w7 = _win(7)
    recent = [{
        "time": (r.created_at.strftime("%m-%d %H:%M") if r.created_at else ""),
        "seat": r.seat_name, "provider": r.provider, "model": r.model,
        "kind": r.error_kind, "error": (r.error or "")[:200],
        "finish": r.finish_reason, "completion": r.completion_tokens,
        "reasoning": r.reasoning_tokens, "relay": bool(r.via_relay),
    } for r in rows if not r.ok][:10]
    return {
        "d7": _agg(w7), "d30": _agg(rows),
        "by_kind": by_kind, "by_provider": by_provider,
        "fallback_7d": fallback_7d,
        "recent_failures": recent,
    }


@app.get("/api/roundtable/review/stats")
def rt_review_stats(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """观点复盘面板数据（G3）：席位命中率 / 趋势 / 引用资讯信息量 / 样本明细。

    判定全部机械（方向 × 真实收益），LLM 只负责「文本 → 结构化」抽取，
    不在后端做任何「对错」判断——这是平台「可解释、人在回路」的底线。
    命中率口径为 T+5（与策略外推 5 交易日同尺度）；hold/observe 与
    |收益|<FLAT_BAND 的无效波动一律不计入命中率分母。
    """
    return {
        "seat_stats": rtreview.seat_stats(db),
        "trend": rtreview.trend_stats(db),
        "news_hit": rtreview.news_hit_stats(db),
        "samples": rtreview.samples(db, limit=50),
        "meta": {"flat_band": rtreview.FLAT_BAND, "horizon": rtreview.HORIZON,
                 "min_sample": rtreview.MIN_SAMPLE, "review_days": rtreview.REVIEW_DAYS},
    }


@app.post("/api/roundtable/review")
def rt_review_run(body: Optional[RoundtableReviewReq] = None,
                  u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员手动触发复盘：date 复盘单日；days 补批历史（缺哪抽哪）；都不给则跑每日（昨日+近窗补算）。

    抽取用资讯 AI 的 LLM 配置（news_llm_conf），与资讯评分/概要同源；
    判定机械（方向 × 真实行情），不依赖任何模型事后自评。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    try:
        conf = news_llm_conf(db)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    date = (body.date if body and body.date else None)
    days = (body.days if body and body.days else None)
    if date:
        r = rtreview.run_review(db, conf, date)
        mode = "date"
    elif days:
        r = rtreview.backfill(db, conf, days)
        mode = "backfill"
    else:
        r = rtreview.daily(db, conf)
        mode = "daily"
    return {"ok": True, "mode": mode, "result": r}


@app.put("/api/roundtable/settings")
def rt_put_settings(body: RoundtableSettingsIn, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """保存某提供方 LLM 配置；api_key 传空串 = 清除已配置的 Key。

    支持**自定义服务商**：名称符合 `_PROVIDER_NAME_RE` 即可（不必是内置项）。
    但自定义项没有内置默认值，必须自带 base_url，否则调用时无处可发。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    provider = (body.provider or "").strip()
    if not _PROVIDER_NAME_RE.match(provider):
        raise HTTPException(400, "服务商名称只能用字母、数字、下划线和连字符，长度 1~32")
    if body.delete:
        # 删除自定义服务商：清掉它的全部 llm.<name>.* 配置。
        # 内置项**显式拒绝**——若放任它走到下面，只会把 None 字段一个个 continue 掉、
        # 返回一个「成功」的假象（2026-10-17 实测踩到）。
        if provider in PROVIDER_DEFAULTS:
            raise HTTPException(400, f"「{provider}」是内置服务商，不能删除；清除 API Key 即可停用")
        removed = (db.query(Setting)
                     .filter(Setting.key.like(f"llm.{provider}.%"))
                     .delete(synchronize_session=False))
        db.commit()
        return {"ok": True, "deleted": removed}
    if provider not in PROVIDER_DEFAULTS:
        # 自定义服务商没有内置默认值 → 必须自带 base_url，否则调用时无处可发
        cur = _get_llm_conf(db, provider)
        if not ((body.base_url or "").strip() or cur["base_url"]):
            raise HTTPException(400, f"自定义服务商「{provider}」必须填写 base_url")
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
    # 近期相关资讯选条：主线程读库选好，工作线程只拼提示词（同 persona/路由的理由）。
    # None = 选条失败不带资讯；[] = 无达标资讯（提示词明示，模型据此标「纯技术面」）。
    news_items = _select_news_injection(u, db, date_s)
    # 线程内只做网络调用（不碰 db session，SQLAlchemy Session 非线程安全），结果回主线程统一写库。
    # 风格提示词在这里（主线程、有 db）就解析好——里面可能来自风格模板，工作线程取不到。
    seat_meta = {s.id: {"name": s.name or "", "provider": s.provider or "deepseek",
                        "model": s.model or "", "persona": _resolve_persona(s, db)} for s in seats}
    # 出站路由（无 AAAA 的服务商自动走 rss-relay）必须在主线程算好——
    # _llm_route 读 settings 表，工作线程不碰 db session。
    confs = {}
    for sid, m in seat_meta.items():
        c = _get_llm_conf(db, m["provider"])
        c.update(_llm_route(db, c["base_url"]))
        confs[sid] = c

    def _one(sid: int):
        m = seat_meta[sid]
        conf = confs[sid]
        if not conf.get("api_key"):
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": f"未配置 {m['provider']} 的 API Key",
                    "stats": _llm_stats_base(sid, m, None)}
        # 自定义服务商不在内置表里，取不到默认模型 → 必须显式指定，否则报错而不是崩
        model = (m["model"] or conf.get("model")
                 or (PROVIDER_DEFAULTS.get(m["provider"]) or {}).get("model", ""))
        if not model:
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": f"未指定模型，且服务商「{m['provider']}」没有默认模型",
                    "stats": _llm_stats_base(sid, m, None)}
        seat_obj = RoundtableSeat(id=sid, name=m["name"], persona=m["persona"])  # 仅用于拼提示词
        prompt = _snapshot_prompt_text(snap, seat_obj, news_items=news_items)
        stats = _llm_stats_base(sid, m, model, conf)
        # 输出预算按持仓数动态定：正文 ≈ 400（总体判断+风险提示）+ 持仓数×50。
        # 标定依据（2026-10-17 实测）：7 条真实输出 token 167–946、中位 518，
        # 中文 ≈0.63 token/字，逐持仓点评随持仓数线性增长；13 持仓最长样本 946
        # 已超原固定 900（finish=length 会截出半截正文，比空正文更危险）。
        # 下限 900 保底、上限 4000 防呆；此为关思考场景的预算，若某席位开着
        # 思考导致超预算，llm.py 内的自动关思考兜底会兜住。
        n_holdings = len(snap.get("holdings") or [])
        max_tokens = min(4000, max(900, 400 + n_holdings * 50))
        try:
            content = chat_complete(
                conf["base_url"], conf["api_key"], model,
                [{"role": "system", "content": _LLM_SYSTEM},
                 {"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                via_relay=conf.get("via_relay") or "",
                relay_key=conf.get("relay_key") or "",
                # 推理模型（deepseek-flash）思考与正文共享 max_tokens，圆桌长提示词下
                # 思考发散会烧光预算（finish=length、正文空）。策略按 PROVIDER_DEFAULTS
                # 的 "thinking" 字段走：deepseek=disabled；llm.py 内还有超预算自动兜底。
                disable_thinking=(PROVIDER_DEFAULTS.get(m["provider"]) or {}).get("thinking") == "disabled",
                stats=stats,
            )
        except Exception as e:
            stats["error"] = str(e)[:500]
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": str(e), "model": model, "stats": stats}
        if not content.strip():
            return {"seat_id": sid, "seat_name": m["name"], "ok": False,
                    "error": "模型返回为空", "model": model, "stats": stats}
        return {"seat_id": sid, "seat_name": m["name"], "ok": True,
                "content": content, "model": model, "stats": stats}

    results = []
    with ThreadPoolExecutor(max_workers=min(3, len(seats))) as ex:
        for r in ex.map(_one, [s.id for s in seats]):
            results.append(r)
    # 每次调用（无论成败）落一行 llm_call_log，供「生成健康度」统计与失败追溯
    for r in results:
        try:
            _log_llm_call(db, r.get("seat_id"), r.get("seat_name"), r.get("stats") or {},
                          ok=bool(r.get("ok")), error=r.get("error") or "")
        except Exception as e:
            logger.warning("llm_call_log 写入失败: %s", e)
            db.rollback()
    # 主线程统一写库；每条观点入库后同步进论坛存档（同一「席位 × 日期」一个主题帖）
    seat_by_id = {s.id: s for s in seats}
    for r in results:
        if not r.get("ok"):
            continue
        seat = seat_by_id.get(r["seat_id"])
        if seat is None:                        # 理论上不会发生，防御性跳过
            continue
        post = _upsert_post(db, date_s, r["seat_id"], r["content"], "auto", r.get("model") or "")
        thread_id, archived = None, False
        try:
            thread_id, archived = _sync_roundtable_forum(db, post, seat, r["content"])
            db.commit()
        except Exception as e:                  # 存档失败不回滚观点本身（已提交）
            db.rollback()
            logger.warning("圆桌自动观点同步论坛失败（seat=%s date=%s）：%s", r["seat_id"], date_s, e)
            thread_id = None
        r["thread_id"] = thread_id
        r["archived"] = archived                # True=新增一层存档；False=内容未变、未重复写
    # 资讯注入登记（防同一事件连日重复喂）：只有本次真的生成了观点才登记——
    # 全失败也登记会把当日额度消耗掉，下次重跑反而没有资讯可看。
    if news_items and any(r.get("ok") for r in results):
        try:
            n = newsai.log_injections(db, date_s, [x["id"] for x in news_items])
            logger.info("资讯注入登记：%s 选中 %d 条（新增登记 %d）", date_s, len(news_items), n)
        except Exception as e:
            logger.warning("资讯注入登记失败（不影响观点本身）：%s", e)
            db.rollback()
    return {"date": date_s, "results": results}


# ---------------- 论坛（主题帖 + 跟帖；用户与 Agent 共同讨论） ----------------
FM_MAX_TITLE = 200
FM_MAX_CONTENT = 20000
FM_EXCERPT_LEN = 120

# 系统身份（author_type="system"）：目前只有「每日持仓日报」使用。
# 刻意不复用 agent —— 它没有对应席位，混进席位体系会让「Agent 参与数」等统计失真。
FM_SYSTEM_NAME = "系统日报"
FM_SYSTEM_ROLE = "自动"
# 资讯身份（author_type="news"，2026-10-17「市场概要」帖）：同样不占席位——
# 它是编辑台产物（从当日高分资讯汇总），不是某个分析师席位的观点。
FM_NEWS_NAME = "市场概要"
FM_NEWS_ROLE = "自动"

# ---- 圆桌观点 → 论坛存档（方便后期回看历史信息）----
# 由圆桌的两个写入口调用：rt_submit_post（手动粘贴回填）、rt_generate（LLM 自动生成）。
# 存档语义**刻意与圆桌不同**：圆桌是「每个席位每天一条、重复提交覆盖」，
# 而论坛帖要能回看修订过程，所以同一「席位 × 日期」固定一个主题帖，
# 内容有变化时在该帖内**追加一层跟帖**（并标注第几次更新），内容没变则不写。
FM_ROUNDTABLE_TITLE = "【圆桌】{date} · {seat}"


def _roundtable_thread_title(date_s: str, seat_name: str) -> str:
    return FM_ROUNDTABLE_TITLE.format(date=date_s or "", seat=(seat_name or "").strip() or "未命名席位")[:FM_MAX_TITLE]


def _sync_roundtable_forum(db, post: RoundtablePost, seat: RoundtableSeat, content: str):
    """把一条圆桌观点同步进论坛，返回 (thread_id, 是否新增了一层)。

    调用方负责 commit / 异常回滚——本函数只做「改动 session」，不自己提交，
    这样圆桌侧可以「写库成功即同步」，同步失败也不至于把已保存的观点弄丢。
    边界情形：
      - 首次（thread_id 为空）→ 建主题帖 + 首帖；
      - 已有帖但被用户删掉 → 视为首次，重建并改写 thread_id；
      - 席位改名 → 标题回写为当前席位名（显示名实时解析，标题不做快照）；
      - 内容与帖内最后一层完全一致 → 不写，避免重复点「生成」把论坛刷满。
    """
    title = _roundtable_thread_title(post.date, seat.name)
    t = db.get(ForumThread, post.thread_id) if post.thread_id else None
    if t is None:
        t = ForumThread(title=title, author_type="agent", author_id=seat.id)
        db.add(t)
        db.flush()                      # 先拿 theme id，再写首帖
        db.add(ForumPost(thread_id=t.id, author_type="agent", author_id=seat.id,
                         content=content))
        post.thread_id = t.id
        return t.id, True
    if (t.title or "") != title:
        t.title = title
    last = (db.query(ForumPost).filter(ForumPost.thread_id == t.id)
            .order_by(ForumPost.id.desc()).first())
    if last and (last.content or "").strip() == (content or "").strip():
        return t.id, False
    n = db.query(func.count(ForumPost.id)).filter(ForumPost.thread_id == t.id).scalar() or 0
    db.add(ForumPost(thread_id=t.id, author_type="agent", author_id=seat.id,
                     content=f"（第 {n + 1} 次更新 · {_cn_now()}）\n{content}"))
    t.updated_at = datetime.utcnow()    # 有新一层 → 主题活跃时间刷新（论坛列表置顶依据）
    return t.id, True


def _forum_identity(u: User, author_type: str, seat_id: Optional[int], db: Session):
    """解析发帖身份：普通用户只能以本人身份发言；仅管理员可代 Agent 席位发言。"""
    if (author_type or "user") == "agent":
        if not u.is_admin:
            raise HTTPException(403, "仅管理员可以 Agent 身份发言")
        if not seat_id:
            raise HTTPException(400, "请选择要以哪个 Agent 席位发言")
        s = db.get(RoundtableSeat, seat_id)
        if not s:
            raise HTTPException(404, "席位不存在")
        return "agent", s.id
    return "user", u.id


def _forum_author(db: Session, author_type: str, author_id: Optional[int]):
    """身份 → (显示名, AI 角色)。用户取邮箱前缀；Agent 取席位名与角色；system 为固定署名。

    读取时实时解析（不存快照），因此席位改名会同步体现在历史发言上；
    席位/用户被删除时给出兜底显示名，不报错。
    """
    if (author_type or "user") == "system":
        return FM_SYSTEM_NAME, FM_SYSTEM_ROLE
    if (author_type or "user") == "news":
        return FM_NEWS_NAME, FM_NEWS_ROLE
    if (author_type or "user") == "agent":
        s = db.get(RoundtableSeat, author_id) if author_id else None
        if not s:
            return "已删除席位", ""
        return ((s.name or "").strip() or "未命名席位"), (s.role or "")
    usr = db.get(User, author_id) if author_id else None
    if not usr:
        return "已注销用户", ""
    email = (usr.email or "").strip()
    return (email.split("@")[0] or email or "用户"), ""


def _forum_can_delete(u: User, author_type: str, author_id: Optional[int]) -> bool:
    """本人或管理员可删；Agent 身份的发言只有管理员可删。"""
    if u.is_admin:
        return True
    return (author_type or "user") == "user" and author_id == u.id


def _forum_thread_out(db: Session, t: ForumThread, u: User, posts) -> ForumThreadOut:
    name, role = _forum_author(db, t.author_type, t.author_id)
    first = posts[0] if posts else None
    excerpt = (first.content or "").strip().replace("\n", " ") if first else ""
    if len(excerpt) > FM_EXCERPT_LEN:
        excerpt = excerpt[:FM_EXCERPT_LEN] + "…"
    return ForumThreadOut(
        id=t.id, title=t.title or "", author_type=t.author_type or "user",
        author_name=name, author_role=role, excerpt=excerpt,
        reply_count=max(len(posts) - 1, 0),      # 跟帖数不含首帖
        agent_count=len({p.author_id for p in posts if (p.author_type or "") == "agent"}),
        created_at=t.created_at.isoformat() if t.created_at else None,
        updated_at=t.updated_at.isoformat() if t.updated_at else None,
        can_delete=_forum_can_delete(u, t.author_type, t.author_id),
    )


def _forum_post_out(db: Session, p: ForumPost, u: User) -> ForumPostOut:
    name, role = _forum_author(db, p.author_type, p.author_id)
    return ForumPostOut(
        id=p.id, thread_id=p.thread_id, author_type=p.author_type or "user",
        author_id=p.author_id, author_name=name, author_role=role,
        content=p.content or "",
        created_at=p.created_at.isoformat() if p.created_at else None,
        can_delete=_forum_can_delete(u, p.author_type, p.author_id),
    )


def _forum_detail(db: Session, t: ForumThread, u: User) -> ForumThreadDetail:
    posts = (db.query(ForumPost).filter(ForumPost.thread_id == t.id)
             .order_by(ForumPost.created_at, ForumPost.id).all())
    return ForumThreadDetail(thread=_forum_thread_out(db, t, u, posts),
                             posts=[_forum_post_out(db, p, u) for p in posts])


@app.get("/api/forum/threads", response_model=list[ForumThreadOut])
def forum_list_threads(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """主题帖列表（最近活跃倒序）。站点规模小，全量返回并实时聚合计数，不新增计数表。"""
    threads = (db.query(ForumThread)
               .order_by(ForumThread.updated_at.desc(), ForumThread.id.desc())
               .limit(200).all())
    ids = [t.id for t in threads]
    grouped = {}
    if ids:
        rows = (db.query(ForumPost).filter(ForumPost.thread_id.in_(ids))
                .order_by(ForumPost.created_at, ForumPost.id).all())
        for p in rows:
            grouped.setdefault(p.thread_id, []).append(p)
    return [_forum_thread_out(db, t, u, grouped.get(t.id, [])) for t in threads]


@app.post("/api/forum/threads", response_model=ForumThreadDetail, status_code=201)
def forum_create_thread(body: ForumThreadCreate, u: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """发起新帖：主题 + 首帖一次创建（首帖可为空，仅发标题）。"""
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    if len(title) > FM_MAX_TITLE:
        raise HTTPException(400, f"标题过长（上限 {FM_MAX_TITLE} 字）")
    content = (body.content or "").strip()
    if len(content) > FM_MAX_CONTENT:
        raise HTTPException(400, "内容过长（上限 20000 字符）")
    atype, aid = _forum_identity(u, body.author_type, body.seat_id, db)
    t = ForumThread(title=title, author_type=atype, author_id=aid)
    db.add(t)
    db.commit()
    db.refresh(t)
    if content:
        db.add(ForumPost(thread_id=t.id, author_type=atype, author_id=aid, content=content))
        db.commit()
    return _forum_detail(db, t, u)


@app.get("/api/forum/threads/{tid}", response_model=ForumThreadDetail)
def forum_thread_detail(tid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.get(ForumThread, tid)
    if not t:
        raise HTTPException(404, "帖子不存在")
    return _forum_detail(db, t, u)


@app.post("/api/forum/threads/{tid}/posts", response_model=ForumPostOut, status_code=201)
def forum_reply(tid: int, body: ForumPostCreate, u: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """跟帖：用户以本人身份，管理员可指定 Agent 席位身份发言。"""
    t = db.get(ForumThread, tid)
    if not t:
        raise HTTPException(404, "帖子不存在")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    if len(content) > FM_MAX_CONTENT:
        raise HTTPException(400, "内容过长（上限 20000 字符）")
    atype, aid = _forum_identity(u, body.author_type, body.seat_id, db)
    p = ForumPost(thread_id=t.id, author_type=atype, author_id=aid, content=content)
    db.add(p)
    t.updated_at = datetime.utcnow()     # 有新跟帖 → 主题活跃时间刷新（列表置顶依据）
    db.commit()
    db.refresh(p)
    return _forum_post_out(db, p, u)


@app.delete("/api/forum/threads/{tid}")
def forum_delete_thread(tid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.get(ForumThread, tid)
    if not t:
        raise HTTPException(404, "帖子不存在")
    if not _forum_can_delete(u, t.author_type, t.author_id):
        raise HTTPException(403, "forbidden")
    db.query(ForumPost).filter(ForumPost.thread_id == tid).delete()
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.delete("/api/forum/posts/{pid}")
def forum_delete_post(pid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(ForumPost, pid)
    if not p:
        raise HTTPException(404, "回复不存在")
    if not _forum_can_delete(u, p.author_type, p.author_id):
        raise HTTPException(403, "forbidden")
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---------------- 周期结算帖（仓位 / 收益变化 / 大盘 → 论坛帖） ----------------
# 需求：每天把「仓位、收益变化及相关信息」总结成一篇消息发到论坛，方便日后回看；
# 同理每周 / 每月 / 每季度 / 每半年 / 每年各出一篇周期结算。
# 与圆桌存档帖的分工：圆桌存档是**搬运**（席位观点原样落到论坛），结算帖是**算出来的新内容**；
# 与交易仪表台的分工：仪表台看「此刻的持仓」，结算帖看「较上一期变化了多少」。
# 发布身份固定为 system（见 _forum_author），不占用任何 AI 席位。
#
# 周期键 pkey 与结算窗口一一对应，键本身就是「同周期只认一个帖」的唯一依据：
#   day=2026-10-17   week=2026-W42   month=2026-10   quarter=2026-Q4   half=2026-H2   year=2026
SETTLE_PERIODS = ("day", "week", "month", "quarter", "half", "year")
SETTLE_LABELS = {"day": "日报", "week": "周报", "month": "月报",
                 "quarter": "季报", "half": "半年报", "year": "年报"}
# 每个周期的用词：段标题 / 区间代词 / 自动发布时间（后者写进正文，让回看的人知道这套帖怎么来的）
SETTLE_VOCAB = {
    "day":     {"head": "总览",     "span": "当日",   "cadence": "每交易日 22:00"},
    "week":    {"head": "区间结算", "span": "本周",   "cadence": "每周日 22:00"},
    "month":   {"head": "区间结算", "span": "本月",   "cadence": "每月 1 日 22:00"},
    "quarter": {"head": "区间结算", "span": "本季度", "cadence": "每季度首日 22:00"},
    "half":    {"head": "区间结算", "span": "半年度", "cadence": "每半年首日 22:00"},
    "year":    {"head": "区间结算", "span": "本年度", "cadence": "每年 1 月 1 日 22:00"},
}
REPORT_OPS_DAYS = 5          # 日报「近期操作」回溯的自然日数（当天没录交易时不至于一片空白）


def _month_end(y: int, m: int) -> date:
    """某年某月的最后一天（跨年由 date 自身进位，不必特判 12 月）。"""
    return (date(y, m, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _period_key_of(period: str, d: date) -> str:
    """日期 → 所属周期键。"""
    if period == "day":
        return d.isoformat()
    if period == "week":
        y, w, _ = d.isocalendar()          # ISO 周：周一~周日，周日属于本周
        return f"{y}-W{w:02d}"
    if period == "month":
        return d.strftime("%Y-%m")
    if period == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if period == "half":
        return f"{d.year}-H{1 if d.month <= 6 else 2}"
    if period == "year":
        return str(d.year)
    raise ValueError(f"未知结算周期：{period}")


def _period_window(period: str, key: str):
    """周期键 → (start, end)，闭区间日期串 YYYY-MM-DD。"""
    if period == "day":
        return key, key
    if period == "week":
        y, w = int(key[:4]), int(key.split("-W")[1])
        mon = date.fromisocalendar(y, w, 1)
        return mon.isoformat(), (mon + timedelta(days=6)).isoformat()
    if period == "month":
        y, m = int(key[:4]), int(key[5:7])
        return date(y, m, 1).isoformat(), _month_end(y, m).isoformat()
    if period == "quarter":
        y, q = int(key[:4]), int(key.split("-Q")[1])
        sm = (q - 1) * 3 + 1
        return date(y, sm, 1).isoformat(), _month_end(y, sm + 2).isoformat()
    if period == "half":
        y, h = int(key[:4]), int(key.split("-H")[1])
        sm = 1 if h == 1 else 7
        return date(y, sm, 1).isoformat(), _month_end(y, sm + 5).isoformat()
    if period == "year":
        return f"{key}-01-01", f"{key}-12-31"
    raise ValueError(f"未知结算周期：{period}")


def _latest_closed_key(period: str, today_s: str) -> str:
    """today → 「最近一个已结束」的周期键。

    day 是特例：日报当天发，语义就是「今天」，不要求窗口已走完。
    其余周期都要求窗口**已经结束**（周结要等周一~周日走完，月结要等次月 1 日），
    因此一律取「上一个」周期 —— 这样定时任务哪天误跑都不会算出半截数据。
    """
    d = date.fromisoformat(today_s)
    if period == "day":
        return today_s
    if period == "week":
        # 回到本周（ISO 周一~周日）的周日；周日当天回退 0 天 = 本周刚好走完
        return _period_key_of("week", d - timedelta(days=d.isoweekday() % 7))
    if period == "month":
        return _period_key_of("month", d.replace(day=1) - timedelta(days=1))
    if period == "quarter":
        sm = (d.month - 1) // 3 * 3 + 1
        return _period_key_of("quarter", date(d.year, sm, 1) - timedelta(days=1))
    if period == "half":
        sm = 1 if d.month <= 6 else 7
        return _period_key_of("half", date(d.year, sm, 1) - timedelta(days=1))
    if period == "year":
        return _period_key_of("year", date(d.year, 1, 1) - timedelta(days=1))
    raise ValueError(f"未知结算周期：{period}")


def _valid_period_key(period: str, key: str) -> bool:
    """周期键是否合法（外部传入的 --key / 接口 key 先过这里，避免脏键写进唯一约束）。"""
    try:
        date.fromisoformat(_period_window(period, key)[0])
        return True
    except Exception:
        return False


# 结算帖「更新层」的开头标注，形如「（第 3 次更新 · 2026-10-17 22:00）\n」
_UPDATE_PREFIX_RE = re.compile(r"^（第 \d+ 次更新 · [^）]*）\n")


def _strip_update_prefix(content: str) -> str:
    """剥掉更新层的次数/时间标注，只留正文 —— 判重必须用它，否则永远判不出「没变化」。"""
    return _UPDATE_PREFIX_RE.sub("", content or "", count=1)


def _settle_title(period: str, key: str, start: str, end: str) -> str:
    label = SETTLE_LABELS.get(period, "结算")
    if period == "week":       # 周报带上具体日期段，否则「第 42 周」还得自己换算
        return f"【{label}】{key}（{start[5:]} ~ {end[5:]}）· 持仓与收益"[:FM_MAX_TITLE]
    return f"【{label}】{key} · 持仓与收益"[:FM_MAX_TITLE]


def _report_projects(db):
    """结算帖覆盖的标的集合。

    站点当前是单用户运营，结算口径 = 全部标的（与仪表台管理员视角一致），
    且由定时任务在服务端生成、**不按请求用户过滤** —— 详见 _publish_settlement 注释。
    """
    return (db.query(Project)
            .order_by(Project.pinned.desc(), Project.created_at.desc()).all())


def _report_money(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _report_pct(v) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def _build_settlement(period: str, key: str, db):
    """组装周期结算帖正文（纯文本，论坛按纯文本渲染），返回 (title, content, meta)。

    所有数字**现算不落库**：
    - 期末市值/浮动 → 按**期末持仓**（由操作记录回放到窗口末得出，不是当前持仓）与窗口内最后一条行情；
    - 收益 → 取 _pnl_curve（与项目页盈亏曲线同源），窗口变化 = 期末权益 − 期初权益；
    - 大盘 → 日结算用实时快照，其余周期用 fetch_index_hist 的日线算区间涨跌。
    """
    start, end = _period_window(period, key)
    vocab = SETTLE_VOCAB[period]
    span, is_day = vocab["span"], (period == "day")
    projects = _report_projects(db)

    items, win_ops = [], []
    tot_mv = tot_un = tot_real = tot_pnl_end = tot_pnl_base = tot_real_win = 0.0
    latest_dates, base_dates = [], []
    for p in projects:
        fbs = (db.query(Feedback).filter(Feedback.project_id == p.id)
               .order_by(Feedback.date, Feedback.id).all())
        base_s, base_c = _holding_initial(fbs, p.position_shares or 0.0, p.position_cost or 0.0)
        # 期末持仓：从「首笔反馈前的建仓起点」只回放到窗口末。历史窗口必须用**当时的**仓位，
        # 直接拿 position_shares（当前持仓）去算上个月是错的。
        _, pos_end = _replay_raw([f for f in fbs if f.date <= end], base_s, base_c)
        sh, cst = float(pos_end["shares"]), float(pos_end["avg"])

        mps = (db.query(MarketPrice)
               .filter(MarketPrice.project_id == p.id, MarketPrice.date <= end)
               .order_by(MarketPrice.date).all())
        curve = _pnl_curve(mps, fbs, base_s, base_c)
        # 期初权益 = 窗口开始前最后一个交易日的权益；没有则视为 0（那时还没建仓）
        pnl_base, base_date = 0.0, None
        for c in curve:
            if c["date"] >= start:
                break
            pnl_base, base_date = c["pnl"], c["date"]
        pnl_end = curve[-1]["pnl"] if curve else 0.0

        # 窗口内操作：无论期末是否还持有，都要出现在「区间操作」里
        for f in fbs:
            if start <= f.date <= end:
                win_ops.append((f, p))
        tot_real_win += round(sum(float(f.realized_pnl or 0.0)
                                  for f in fbs if start <= f.date <= end), 2)
        tot_real += round(sum(float(f.realized_pnl or 0.0)
                              for f in fbs if f.date <= end), 2)
        tot_pnl_end += pnl_end
        tot_pnl_base += pnl_base

        if sh <= 1e-9:
            continue                     # 期末无持仓：只计操作，不占「持仓明细」
        close_end = float(mps[-1].close) if mps else None
        close_base, close_base_date = None, None
        for mp in mps:
            if mp.date >= start:
                break
            close_base, close_base_date = float(mp.close), mp.date
        mv = round(sh * close_end, 2) if close_end is not None else None
        un = round((close_end - cst) * sh, 2) if close_end is not None else None
        ret = (round((close_end - cst) / cst * 100, 2)
               if (close_end is not None and cst > 1e-9) else None)
        chg_pct = (round((close_end - close_base) / close_base * 100, 2)
                   if (close_end is not None and close_base) else None)
        tot_mv += mv or 0.0
        tot_un += un or 0.0
        if mps:
            latest_dates.append(mps[-1].date)
        if close_base_date:
            base_dates.append(close_base_date)
        items.append({
            "id": p.id, "name": p.name or "", "code": p.code or "", "market": p.market or "A",
            "shares": round(sh, 4), "cost": round(cst, 4),
            "close": close_end, "date": mps[-1].date if mps else None,
            "chg_pct": chg_pct, "market_value": mv, "unrealized": un,
            "return_pct": ret, "contrib": round(pnl_end - pnl_base, 2),
        })
    watch = len(projects) - len(items)

    # 窗口贡献降序（这一期赚得最多的排前面）
    items.sort(key=lambda x: -(x["contrib"] or 0.0))

    pnl_chg = round(tot_pnl_end - tot_pnl_base, 2)
    un_chg = round(pnl_chg - tot_real_win, 2)
    # 基准日期：各标的窗口前最后一条行情日期不一致时（QDII 掉队、停牌等）不能只取一个日期，
    # 否则会读成「全仓位从那天起的变化」。
    dsorted = sorted(set(base_dates))
    base_note = ("（基准 " + dsorted[0] + "）" if len(dsorted) == 1
                 else "（按各标的最新一期行情）") if dsorted else "（暂无基准）"

    label = SETTLE_LABELS[period]
    body_head = (f"【每日持仓日报】{key}" if is_day
                 else f"【{label}】{key}（{start} ~ {end}）")
    L = [body_head, ""]
    L += [f"■ {vocab['head']}" if is_day else f"■ 区间结算（{start} ~ {end}）",
          f"期末总市值　{_report_money(round(tot_mv, 2))} 元",
          f"浮动盈亏　　{_report_money(round(tot_un, 2))} 元",
          f"累计已实现　{_report_money(round(tot_real, 2))} 元",
          f"累计总收益　{_report_money(round(tot_pnl_end, 2))} 元（= 已实现 + 浮动）",
          f"{span}收益变化　{_report_money(pnl_chg)} 元{base_note}",
          f"　拆解：{span}已实现 {_report_money(tot_real_win)} 元 · "
          f"浮盈变化 {_report_money(un_chg)} 元"]
    if tot_pnl_base > 1e-9:
        L.append(f"{span}收益率　{_report_pct(round(pnl_chg / tot_pnl_base * 100, 2))}"
                 f"（权益 {_report_money(round(tot_pnl_base, 2))} → "
                 f"{_report_money(round(tot_pnl_end, 2))}）")
    L.append(f"持仓 / 标的　{len(items)} / {len(projects)}")

    L += ["", "■ 大盘指数"]
    if is_day:
        try:
            indices = fetch_index_quotes()
        except Exception as e:
            logger.warning("结算帖获取大盘指数失败：%s", e)
            indices = []
        if not indices:
            L.append("数据获取失败")
        for idx in indices:
            if idx.get("close") is None:
                L.append(f"{idx['name']}　数据获取失败")
            else:
                L.append(f"{idx['name']}　{idx['close']}　{_report_pct(idx.get('chg_pct'))}"
                         f"（{idx.get('date') or ''}）")
    else:
        try:
            hist = fetch_index_hist()
        except Exception as e:
            logger.warning("结算帖获取指数日线失败：%s", e)
            hist = {}
        ok = False
        for item in (hist or {}).values():
            c_end = c_base = None
            for d, c in (item.get("rows") or []):     # 升序，逐条推进取「窗口前的最后一个」与「窗口末」
                if d > end:
                    break
                c_end = c
                if d < start:
                    c_base = c
            if c_end is None:
                continue
            ok = True
            if c_base:
                L.append(f"{item['name']}　{c_end}　{span} {_report_pct(round((c_end - c_base) / c_base * 100, 2))}"
                         f"（{c_base} → {c_end}）")
            else:
                L.append(f"{item['name']}　{c_end}　{span}无基准数据")
        if not ok:
            L.append("数据获取失败")

    L += ["", f"■ 持仓明细（按{span}贡献降序）"]
    if not items:
        L.append("当前无持仓标的")
    for i, h in enumerate(items, 1):
        if h["close"] is None:
            L.append(f"{i}. {h['name']}（{h['code']}）　暂无行情数据")
            continue
        L.append(f"{i}. {h['name']}（{h['code']}）")
        L.append(f"　 净值 {h['close']}（{h['date']}）　市值 {_report_money(h['market_value'])} 元"
                 f"　{span} {_report_pct(h['chg_pct'])}")
        L.append(f"　 份额 {h['shares']:,.0f} · 成本 {h['cost']} · "
                 f"浮动 {_report_money(h['unrealized'])} 元（{_report_pct(h['return_pct'])}）"
                 f" · {span}贡献 {_report_money(h['contrib'])} 元")

    # 操作段：日报只看当天容易空白（交易常晚一天录入），故回溯 REPORT_OPS_DAYS 天；
    # 长周期直接用结算窗口内的全部操作。
    if is_day:
        since = (datetime.utcnow() + _CN_TZ_OFFSET
                 - timedelta(days=REPORT_OPS_DAYS)).strftime("%Y-%m-%d")
        op_rows = (db.query(Feedback, Project)
                   .join(Project, Feedback.project_id == Project.id)
                   .filter(Feedback.date >= since)
                   .order_by(Feedback.date.desc(), Feedback.id.desc()).all())
        op_head, op_empty = f"■ 近期操作（最近 {REPORT_OPS_DAYS} 个自然日）", "无操作记录"
    else:
        op_rows = sorted(win_ops, key=lambda t: (t[0].date, t[0].id), reverse=True)
        op_head, op_empty = f"■ {span}操作（{start} ~ {end}）", f"{span}无操作记录"
    L += ["", op_head]
    if not op_rows:
        L.append(op_empty)
    for f, proj in op_rows:
        lab = {"BUY": "买入", "SELL": "卖出", "HOLD": "持有"}.get(f.action, f.action or "")
        seg = (f"{f.date}　{lab} {proj.name}（{proj.code}）"
               f"　{f.qty or 0:,.0f} 份 @ {f.price or 0}")
        if f.realized_pnl:
            seg += f"　实现 {f.realized_pnl:,.2f} 元"
        if (f.note or "").strip():
            seg += f"　备注：{f.note.strip()}"
        L.append(seg)

    dmin = min(latest_dates) if latest_dates else "—"
    dmax = max(latest_dates) if latest_dates else "—"
    L += ["", "■ 数据说明",
          f"· 结算窗口：{start} ~ {end}（{label}）。",
          f"· 收益口径：已实现盈亏 + 持仓浮盈（与项目页盈亏曲线同源）——"
          f"买入不计入收益、卖出把浮盈转为已实现，故{span}收益变化不含期间新增本金。",
          "· 期末市值 / 浮动按**期末持仓**（由操作记录回放到窗口末得出）与窗口内最后一条行情计算，"
          "若窗口结束后又调过仓，回看时以当时持仓为准。",
          "· 净值披露：场外基金由基金公司于交易日 19:00–22:00 陆续披露，"
          "QDII 为 T+1/T+2 披露，因此明细中的净值日期可能早于结算日期。",
          f"· 本{label}数据日期：{dmin}" + (f" ~ {dmax}" if dmax != dmin else "")
          # 刻意不写「生成于 HH:MM」：正文要用来判重（内容没变就不追加更新层），
          # 一旦正文里带上生成时刻，每次执行都会「内容不同」，幂等设计就失效了。
          # 真实发布/更新时间看帖子的创建时间与各更新层的标注。
          + f"；由系统自动生成并发布（{vocab['cadence']}）。"]

    meta = {
        "period": period, "key": key, "start": start, "end": end,
        "holdings": len(items), "watch": watch, "projects": len(projects),
        "ops": len(win_ops),
        "market_value": round(tot_mv, 2), "unrealized": round(tot_un, 2),
        "realized": round(tot_real, 2), "total_pnl": round(tot_pnl_end, 2),
        "change": pnl_chg, "realized_win": round(tot_real_win, 2),
        "data_date": dmax,
    }
    return _settle_title(period, key, start, end), "\n".join(L), meta


def _publish_settlement(db, period: str, key: Optional[str] = None):
    """生成周期结算帖并发布到论坛，返回结果 dict（接口与定时脚本共用）。

    key 留空 → 取「最近一个已结束」的周期（见 _latest_closed_key）。

    幂等语义与圆桌存档一致：**每个周期一个主题帖**；正文与帖内最后一层完全一致 → 不写，
    有变化 → 在该帖追加一层并标注第几次更新。这样定时任务重跑、补数后再跑都不会刷屏，
    同时保留修订痕迹（结算窗口内的数据往往要到次日才补齐，靠的就是这个「追加」而非新建）。

    ⚠️ 判重要**先剥掉更新层的「（第 N 次更新 · 时间）」前缀**再比：不剥的话，
    只要追加过一次，最后一层就永远带着前缀 ≠ 新正文，此后每次执行都会再追加一层
    ——「跑一次加一层」的静默刷屏。这个坑在早期的日报实现里就存在，本次一并修掉。
    """
    period = (period or "day").strip().lower()
    if period not in SETTLE_PERIODS:
        raise ValueError(f"未知结算周期：{period}")
    key = (key or "").strip() or _latest_closed_key(period, _cn_today())
    if not _valid_period_key(period, key):
        raise ValueError(f"周期键格式不对：{key}")
    label = SETTLE_LABELS[period]
    title, content, meta = _build_settlement(period, key, db)
    # 空帖守卫：窗口末没有持仓、窗口内也没有任何操作 → 不发（避免刷一堆空帖）。
    # 「有操作」这一半很重要：清仓那期持仓为 0，但那期恰恰最该留档。
    if meta["holdings"] <= 0 and not meta["ops"]:
        return {"ok": False, "skipped": True, "reason": "no_data", "title": title,
                "message": f"{label}（{key}）窗口内既无持仓也无操作，本次不发布", **meta}
    rec = (db.query(SettleReport)
           .filter(SettleReport.period == period, SettleReport.pkey == key).first())
    t = db.get(ForumThread, rec.thread_id) if (rec and rec.thread_id) else None
    if t is None:                       # 首次，或帖子被用户删掉 → 重建并改写关联
        t = ForumThread(title=title, author_type="system", author_id=None)
        db.add(t)
        db.flush()
        db.add(ForumPost(thread_id=t.id, author_type="system", author_id=None, content=content))
        if rec is None:
            rec = SettleReport(period=period, pkey=key)
            db.add(rec)
        rec.thread_id = t.id
        rec.updates = 1
        db.commit()
        return {"ok": True, "created": True, "posts": 1, "thread_id": t.id,
                "title": title, "content": content, **meta}
    if (t.title or "") != title:
        t.title = title
    last = (db.query(ForumPost).filter(ForumPost.thread_id == t.id)
            .order_by(ForumPost.id.desc()).first())
    if last and _strip_update_prefix(last.content).strip() == content.strip():
        db.commit()                     # 标题可能刚回写，仍要提交
        n = db.query(func.count(ForumPost.id)).filter(ForumPost.thread_id == t.id).scalar() or 1
        return {"ok": True, "created": False, "unchanged": True, "posts": n,
                "thread_id": t.id, "title": title, "content": content, **meta}
    n = db.query(func.count(ForumPost.id)).filter(ForumPost.thread_id == t.id).scalar() or 0
    db.add(ForumPost(thread_id=t.id, author_type="system", author_id=None,
                     content=f"（第 {n + 1} 次更新 · {_cn_now()}）\n{content}"))
    t.updated_at = datetime.utcnow()     # 有新一层 → 主题活跃时间刷新（列表置顶依据）
    rec.updates = n + 1
    db.commit()
    return {"ok": True, "created": False, "unchanged": False, "posts": n + 1,
            "thread_id": t.id, "title": title, "content": content, **meta}


@app.post("/api/report/settle")
def report_settle(body: Optional[SettleReportReq] = None,
                  u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员：生成某周期的结算帖并发布到论坛（每周期一篇，内容有变化才追加更新层）。

    body: {period: day|week|month|quarter|half|year, key: 周期键}；
    key 留空 = 最近一个已结束的周期（如 10 月里发月结 → 9 月）。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    period = ((body.period if body else None) or "day").strip().lower()
    if period not in SETTLE_PERIODS:
        raise HTTPException(400, f"未知结算周期：{period}（可选 {'/'.join(SETTLE_PERIODS)}）")
    key = ((body.key if body else None) or "").strip()
    if key and not _valid_period_key(period, key):
        raise HTTPException(400, f"周期键格式不对：{key}")
    return _publish_settlement(db, period, key)


@app.post("/api/report/daily")
def report_daily(body: Optional[DailyReportReq] = None,
                 u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员：发布当日日报（兼容入口，等价于 period=day）。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    key = ((body.date if body else None) or "").strip()
    return _publish_settlement(db, "day", key)


# ---------------- 市场概要帖（资讯 AI 加工 → 论坛） ----------------
def _publish_news_digest(db, date_s: Optional[str] = None, dry_run: bool = False) -> dict:
    """生成当日「市场概要」并发布到论坛，返回结果 dict（接口与定时脚本共用）。

    与结算帖的分工：结算帖**算账**（持仓数字），概要帖**说事**（世界发生了什么）。
    幂等语义与结算帖一致：每天一个主题帖，正文与帖内最后一层完全一致 → 不写，
    有变化 → 追加一层并标注第几次更新（判重同样先剥更新层前缀）。
    身份用 author_type="news"（论坛显示「市场概要」），不占 AI 席位。
    """
    date_s = (date_s or "").strip() or _cn_today()
    conf = news_llm_conf(db)
    admin = db.query(User).filter(User.is_admin == 1).order_by(User.id).first()
    holdings = _user_holdings(admin, db) if admin else []
    # 快照给「今日数据速览」节用（指数/账户/持仓真实数据）。取数失败不阻塞发布——
    # 降级为没有数据节（纯资讯版），概要继续发。
    try:
        snapshot = _build_snapshot(admin, db) if admin else None
    except Exception as e:
        logger.warning("市场概要快照获取失败（数据速览节降级）：%s", e)
        snapshot = None
    title, content = newsai.build_digest(db, conf, date_s, holdings, snapshot=snapshot)
    if not content:
        return {"ok": False, "skipped": True, "reason": "no_data", "date": date_s,
                "title": title or "", "message": f"{date_s} 没有重要度达标的资讯，本次不发布"}
    if dry_run:
        return {"ok": True, "dry_run": True, "date": date_s, "title": title, "content": content}

    rec = (db.query(NewsDigest).filter(NewsDigest.date == date_s).first())
    t = db.get(ForumThread, rec.thread_id) if (rec and rec.thread_id) else None
    if t is None:                       # 首次，或帖子被用户删掉 → 重建并改写关联
        t = ForumThread(title=title, author_type="news", author_id=None)
        db.add(t)
        db.flush()
        db.add(ForumPost(thread_id=t.id, author_type="news", author_id=None, content=content))
        if rec is None:
            rec = NewsDigest(date=date_s)
            db.add(rec)
        rec.thread_id = t.id
        rec.updates = 1
        db.commit()
        return {"ok": True, "created": True, "posts": 1, "thread_id": t.id,
                "title": title, "content": content}
    if (t.title or "") != title:
        t.title = title
    last = (db.query(ForumPost).filter(ForumPost.thread_id == t.id)
            .order_by(ForumPost.id.desc()).first())
    if last and _strip_update_prefix(last.content).strip() == content.strip():
        db.commit()
        n = db.query(func.count(ForumPost.id)).filter(ForumPost.thread_id == t.id).scalar() or 1
        return {"ok": True, "created": False, "unchanged": True, "posts": n,
                "thread_id": t.id, "title": title, "content": content}
    n = db.query(func.count(ForumPost.id)).filter(ForumPost.thread_id == t.id).scalar() or 0
    db.add(ForumPost(thread_id=t.id, author_type="news", author_id=None,
                     content=f"（第 {n + 1} 次更新 · {_cn_now()}）\n{content}"))
    t.updated_at = datetime.utcnow()
    rec.updates = n + 1
    db.commit()
    return {"ok": True, "created": False, "unchanged": False, "posts": n + 1,
            "thread_id": t.id, "title": title, "content": content}


@app.post("/api/report/news-digest")
def report_news_digest(body: Optional[NewsDigestReq] = None,
                       u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员：生成当日市场概要帖并发布到论坛（每天一篇，内容有变化才追加更新层）。

    body: {date?: 北京日期 YYYY-MM-DD（留空 = 今天）, dry_run?: 只生成正文不发布}
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    date_s = ((body.date if body else None) or "").strip()
    dry_run = bool(body.dry_run) if body else False
    return _publish_news_digest(db, date_s, dry_run=dry_run)


@app.post("/api/news/score")
def news_score(body: Optional[NewsScoreReq] = None,
               u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员：立即对未加工的资讯跑一遍 AI 结构化加工（定时任务同逻辑）。

    评分结果同时服务三处：席位提示词注入、资讯页徽标、市场概要帖。
    同步执行、单次最多 --limit 条（默认 40）；大批量补跑请用 scripts/news_score.py。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    conf = news_llm_conf(db)
    limit = (body.limit if body else None) or 40
    stats = newsai.score_items(db, conf, limit=limit, log=logger.info)
    unit = "条" if conf.get("protocol") == "systemone" else "块"
    stats["message"] = (f"加工 {stats['scored']} 条（选中 {stats['picked']} 条，"
                        f"{stats['failed']} {unit}失败）")
    return stats


# ---------------- 资讯工作台（RSS 订阅 · 中继抓取 · 列表） ----------------
# 首页。抓取经 Cloudflare 中继（本机 VPS 只有 IPv6 出站，多数资讯源仅有 A 记录）。
NEWS_DEFAULT_FEEDS = [
    {
        "url": "https://aihot.news/feed.xml?aihot_actor=639fc075-f02f-4d72-915f-500b963d101a",
        "title": "AIHOT 精选",
        "category": "AI 动态",
    },
    # 财联社电报（RSSHub 公共实例，多实例容灾）—— 2026-10-19 实测可用
    {
        "url": "https://hub.slarker.me/cls/telegraph",
        "backup_urls": "https://rsshub.liumingye.cn/cls/telegraph,https://rsshub.ktachibana.party/cls/telegraph,https://rsshub.cups.moe/cls/telegraph,https://rsshub-balancer.virworks.moe/cls/telegraph",
        "title": "财联社电报",
        "category": "财经",
    },
    # 财联社红色电报（重要快讯，RSSHub 公共实例，多实例容灾）—— 2026-10-19 实测可用
    {
        "url": "https://hub.slarker.me/cls/telegraph/red",
        "backup_urls": "https://rsshub.liumingye.cn/cls/telegraph/red,https://rsshub.ktachibana.party/cls/telegraph/red,https://rsshub.cups.moe/cls/telegraph/red,https://rsshub-balancer.virworks.moe/cls/telegraph/red",
        "title": "财联社红色电报",
        "category": "财经",
    },
    # 澎湃新闻首页头条（RSSHub 公共实例，多实例容灾）—— 2026-10-19 实测可用
    {
        "url": "https://hub.slarker.me/thepaper/featured",
        "backup_urls": "https://rsshub.liumingye.cn/thepaper/featured,https://rsshub.ktachibana.party/thepaper/featured,https://rsshub.cups.moe/thepaper/featured,https://rsshub-balancer.virworks.moe/thepaper/featured",
        "title": "澎湃首页头条",
        "category": "时政",
    },
]

# OPML 导入的体积上限。OPML 只是地址清单，正常几 KB；给 2 MB 足够覆盖超大订阅列表，
# 又挡住误传其他 XML（比如把整个 Feed 原文当 OPML 传上来）。
_OPML_MAX_BYTES = 2 * 1024 * 1024


def _ensure_default_feeds(db) -> None:
    """空库时播种默认订阅源（幂等；已有任何源就跳过，不覆盖用户增删）。"""
    if db.query(NewsFeed).count():
        return
    for i, f in enumerate(NEWS_DEFAULT_FEEDS):
        db.add(NewsFeed(url=f["url"], title=f["title"], category=f["category"], sort=i))
    db.commit()


def _ensure_relay_settings(db) -> None:
    """中继配置优先从环境变量播种（首次部署用），已有值不覆盖。"""
    url = (os.environ.get("NEWS_RELAY_URL") or "").strip()
    key = (os.environ.get("NEWS_RELAY_KEY") or "").strip()
    if url and not newsfetch.get_setting(db, newsfetch.RELAY_URL_KEY):
        newsfetch.put_setting(db, newsfetch.RELAY_URL_KEY, url)
    if key and not newsfetch.get_setting(db, newsfetch.RELAY_KEY_KEY):
        newsfetch.put_setting(db, newsfetch.RELAY_KEY_KEY, key)
    db.commit()


def _news_range_start(range_s: str):
    """把筛选区间换算成 UTC 起点。「今天」按北京时间零点算。"""
    if range_s == "today":
        cn_now = datetime.utcnow() + _CN_TZ_OFFSET
        cn_midnight = cn_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return cn_midnight - _CN_TZ_OFFSET
    if range_s == "7d":
        return datetime.utcnow() - timedelta(days=7)
    if range_s == "30d":
        return datetime.utcnow() - timedelta(days=30)
    return None


def _news_items_payload(db, range_s="today", category="", q="", star_only=False, limit=300,
                        holding_codes=None, related_only=False, page=1, page_size=20) -> dict:
    query = (db.query(NewsItem, NewsFeed)
             .join(NewsFeed, NewsItem.feed_id == NewsFeed.id)
             .filter(NewsFeed.enabled == 1))
    start = _news_range_start(range_s)
    if start is not None:
        query = query.filter(NewsItem.published_at.isnot(None), NewsItem.published_at >= start)
    category = (category or "").strip().strip(",")
    if category:
        # category 是以逗号分隔的多值字段（RSS 多条 <category> 会拼接成一个字符串存储），
        # 选中单个关键字时须按逗号边界匹配，否则「AI」匹配不到「AI,大模型」这类组合值。
        tok = f"%,{category},%"
        query = query.filter(
            ("," + NewsItem.category + ",").like(tok)
            | ("," + NewsFeed.category + ",").like(tok)
        )
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter((NewsItem.title.like(like)) | (NewsItem.summary.like(like))
                             | (NewsItem.source_name.like(like)))
    if star_only:
        query = query.filter(NewsItem.star == 1)

    # 分页：先按当前筛选条件统计总数（total 供前端分页组件显示），再 offset/limit 截断。
    # limit 参数保持旧语义（单次最多返回条数），page_size 是每页条数，取两者较小值。
    # related_only 无法在 SQL 层过滤（依赖每条新闻的持仓相关实时计算），故 total 为
    # 「当前 range/category/q/star_only 条件下的总条数」，与分页组件的页数一致。
    total = int(query.count())
    page_size = max(1, min(page_size, 100))
    page = max(1, page)
    take = max(1, min(limit, page_size))
    rows = (query.order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
            .offset((page - 1) * page_size).limit(take).all())

    # 「与我的持仓相关」在后端算，两条匹配通道：
    #   ① 代码精确：持仓代码（当前用户）∩ 条目的 ai_codes（LLM 提取）；
    #   ② 行业画像：持仓行业关键词命中 条目标题/摘要/标签（覆盖场外基金——
    #      新闻不出现基金代码，但会出现「半导体」「医药」这类行业词）。
    # 两边都过 newsai.norm_code 归一化（sh 前缀 / .SH 后缀 / 大小写），否则永远匹配不上。
    codes = {newsai.norm_code(c) for c in (holding_codes or []) if newsai.norm_code(c)}
    profiles = newsai.load_fund_profiles()
    items, related_n = [], 0
    for it, fd in rows:
        item_codes = {newsai.norm_code(c) for c in (it.ai_codes or "").split(",")
                      if newsai.norm_code(c)}
        text = f"{it.title or ''} {it.summary or ''} {it.ai_tags or ''}"
        related = bool(codes and (codes & (item_codes | newsai.profile_hits(text, profiles))))
        if related:
            related_n += 1
        if related_only and not related:
            continue
        items.append(newsfetch.item_out(it, fd.title or "", related=related))

    # 分类 chips：**条目自身分类**与**源的人工分类**都算。
    # 源级分类必须算进来，否则「导入 OPML 时整组打的分类」（如给财经源统一打「财经」）
    # 在筛选条上根本看不见 —— 但下面的 category 过滤条件其实已经认它（见上方的 OR），
    # 会出现「筛得中、却点不到」的割裂。两个来源合并计数：一条同时带条目分类与源分类的
    # 条目会各计一次，这与「按分类筛选时两种都匹配」的语义一致。
    # 注意：category 字段是逗号分隔的多值（RSS 多条 <category> 拼接存储），
    # 聚合时按逗号拆成独立关键字，避免筛选条上出现「AI,大模型」这种一组粘连的 chip。
    def _split_cats(raw):
        return [x.strip() for x in (raw or "").split(",") if x.strip()]

    cat_counts = Counter()
    for c, n in (db.query(NewsItem.category, func.count(NewsItem.id))
                 .join(NewsFeed, NewsItem.feed_id == NewsFeed.id)
                 .filter(NewsFeed.enabled == 1, NewsItem.category != "")
                 .group_by(NewsItem.category).all()):
        for k in _split_cats(c):
            cat_counts[k] += n
    for c, n in (db.query(NewsFeed.category, func.count(NewsItem.id))
                 .join(NewsItem, NewsItem.feed_id == NewsFeed.id)
                 .filter(NewsFeed.enabled == 1, NewsFeed.category != "")
                 .group_by(NewsFeed.category).all()):
        for k in _split_cats(c):
            cat_counts[k] += n
    cats = [{"name": c, "count": n} for c, n in cat_counts.most_common()]

    today_start = _news_range_start("today")
    today_n = (db.query(func.count(NewsItem.id))
               .filter(NewsItem.published_at.isnot(None), NewsItem.published_at >= today_start)
               .scalar() or 0)
    feed_n = db.query(func.count(NewsFeed.id)).filter(NewsFeed.enabled == 1).scalar() or 0
    err_n = db.query(func.count(NewsFeed.id)).filter(NewsFeed.enabled == 1,
                                                     NewsFeed.last_status == "error").scalar() or 0
    empty_n = db.query(func.count(NewsFeed.id)).filter(NewsFeed.enabled == 1,
                                                       NewsFeed.last_status == "empty").scalar() or 0
    last = (db.query(func.max(NewsFeed.last_fetched)).scalar())
    conf = newsfetch.relay_conf(db)
    return {
        "items": items,
        "categories": cats,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "stats": {
            "today": int(today_n),
            "feeds": int(feed_n),
            "feed_errors": int(err_n),
            "feed_empties": int(empty_n),
            "related": int(related_n),       # 当前筛选范围内与持仓相关的条数
            "last_fetched": last.isoformat() + "Z" if last else None,
            "relay_ready": bool(conf.get("url") and conf.get("key")),
        },
    }


def _news_feed_out(db, f: NewsFeed) -> NewsFeedOut:
    n = db.query(func.count(NewsItem.id)).filter(NewsItem.feed_id == f.id).scalar() or 0
    return NewsFeedOut(
        id=f.id, title=f.title or "", url=f.url or "", site_url=f.site_url or "",
        category=f.category or "", enabled=1 if f.enabled else 0, sort=f.sort or 0,
        last_fetched=f.last_fetched.isoformat() + "Z" if f.last_fetched else None,
        last_status=f.last_status or "", last_error=f.last_error or "",
        item_count=int(n),
        created_at=f.created_at.isoformat() if f.created_at else None,
    )


@app.get("/api/news/items")
def news_items(range: str = "today", category: str = "", q: str = "",
               starred: int = 0, related: int = 0, page: int = 1, page_size: int = 20,
               u: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    _ensure_default_feeds(db)
    codes = [h["code"] for h in _user_holdings(u, db) if h.get("code")]
    return _news_items_payload(db, range, category, q, bool(starred),
                               holding_codes=codes, related_only=bool(related),
                               page=page, page_size=page_size)


@app.get("/api/guest/news/items")
def guest_news_items(range: str = "today", category: str = "", q: str = "",
                     starred: int = 0, related: int = 0, page: int = 1, page_size: int = 20,
                     db: Session = Depends(get_db)):
    """游客只读：与登录态同一份资讯流，不暴露订阅源地址与抓取配置。

    游客没有持仓 → related 恒为 0、也不按相关筛选（前端对游客不渲染该开关）。
    """
    _ensure_default_feeds(db)
    data = _news_items_payload(db, range, category, q, bool(starred),
                               page=page, page_size=page_size)
    data["stats"].pop("relay_ready", None)
    return data


@app.get("/api/news/feeds", response_model=list[NewsFeedOut])
def news_list_feeds(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_default_feeds(db)
    feeds = db.query(NewsFeed).order_by(NewsFeed.sort, NewsFeed.id).all()
    return [_news_feed_out(db, f) for f in feeds]


@app.post("/api/news/feeds", response_model=NewsFeedOut, status_code=201)
def news_add_feed(body: NewsFeedCreate, u: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    url = (body.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Feed 地址必须以 http:// 或 https:// 开头")
    if db.query(NewsFeed).filter(NewsFeed.url == url).first():
        raise HTTPException(400, "该 Feed 地址已存在")
    f = NewsFeed(url=url[:600], title=(body.title or "").strip()[:200],
                 category=(body.category or "").strip()[:32])
    db.add(f)
    db.commit()
    db.refresh(f)
    return _news_feed_out(db, f)


@app.post("/api/news/feeds/import-opml")
def news_import_opml(body: NewsOpmlIn, u: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """从 OPML 批量导入订阅源，并**当场实抓一次**（导入即体检）。

    为什么必须实抓：OPML 只是一个地址清单，它对「这个源还活着吗」一无所知。
    典型教训 —— 新浪财经的 OPML 里 9 个源全部 HTTP 200，但 channel 里
    pubDate 冻结在 2018-09-23、不含任何 <item>。不实抓就会把 9 个僵尸源
    连同白名单、调度一起配好，然后永远收不到内容。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    raw = (body.content or "").strip()
    if not raw:
        raise HTTPException(400, "OPML 内容为空")
    if len(raw.encode("utf-8")) > _OPML_MAX_BYTES:
        raise HTTPException(400, f"OPML 内容过大（上限 {_OPML_MAX_BYTES // 1024 // 1024} MB）")
    try:
        items = newsfetch.parse_opml(raw.encode("utf-8"))
    except Exception as e:
        raise HTTPException(400, f"OPML 解析失败：{type(e).__name__}: {e}")
    if not items:
        raise HTTPException(400, "OPML 里没有找到订阅源（需要一个带 xmlUrl 属性的 <outline>）")

    cat = (body.category or "").strip()[:32]
    have = {u_ for (u_,) in db.query(NewsFeed.url).all()}
    sort_next = db.query(func.max(NewsFeed.sort)).scalar() or 0
    skipped, created = [], []
    for it in items:
        url = it["url"]
        if not re.match(r"^https?://", url):
            skipped.append({**it, "reason": "地址不是 http(s)"})
            continue
        if url in have:
            skipped.append({**it, "reason": "已存在"})
            continue
        have.add(url)
        sort_next += 1
        f = NewsFeed(url=url, title=it["title"], category=cat, sort=sort_next)
        db.add(f)
        created.append(f)
    db.commit()
    for f in created:
        db.refresh(f)

    conf = newsfetch.relay_conf(db)
    can_fetch = bool(conf.get("url") and conf.get("key"))
    fetched = []
    if body.fetch and created:
        if not can_fetch:
            fetched = [{"url": f.url, "title": f.title or "", "status": "untested",
                        "ok": False, "message": "未配置中继，已跳过导入后的实抓"}
                       for f in created]
        else:
            for f in created:
                r = newsfetch.refresh_feed(db, f, limit=30)
                row = {"url": f.url, "title": f.title or f.url, "status": r.get("status", ""),
                       "ok": bool(r.get("ok")), "added": r.get("added", 0),
                       "parsed": r.get("parsed", 0),
                       "message": r.get("error") or ""}
                fetched.append(row)

    return {
        "parsed": len(items),
        "added": len(created),
        "skipped": skipped,
        "feeds": [_news_feed_out(db, f) for f in created],
        "fetched": fetched,
        "fetched_ok": can_fetch,
    }


@app.patch("/api/news/feeds/{fid}", response_model=NewsFeedOut)
def news_update_feed(fid: int, body: NewsFeedUpdate, u: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    f = db.get(NewsFeed, fid)
    if not f:
        raise HTTPException(404, "订阅源不存在")
    if body.title is not None:
        f.title = body.title.strip()[:200]
    if body.category is not None:
        f.category = body.category.strip()[:32]
    if body.enabled is not None:
        f.enabled = 1 if body.enabled else 0
    if body.sort is not None:
        f.sort = body.sort
    db.commit()
    return _news_feed_out(db, f)


def _xml_attr(s: str) -> str:
    """XML 属性值转义。OPML 里源标题/地址都可能带 & < > " '（财经源名尤其爱用 &）。"""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


@app.get("/api/news/feeds/export-opml")
def news_export_opml(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """导出现有订阅源为 OPML（换机 / 备份 / 分享订阅清单用）。

    按 category 分组输出（与导入时的「源级/统一分类」对称）；无分类的源平铺在根下。
    刻意**只导启用的源**——停用说明源有问题，导出去再导入只会得到一批待体检的僵尸源。
    """
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    feeds = db.query(NewsFeed).order_by(NewsFeed.sort, NewsFeed.id).all()
    groups: dict = {}
    for f in feeds:
        if not f.enabled:
            continue
        groups.setdefault((f.category or "").strip(), []).append(f)

    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<opml version="2.0">', "<head>",
         "<title>QuantMind 资讯订阅</title>",
         f"<dateCreated>{datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S')} +0000</dateCreated>",
         "</head>", "<body>"]
    for cat in sorted(groups, key=lambda c: (c == "", c)):
        kids = groups[cat]
        if cat:
            L.append(f'  <outline text="{_xml_attr(cat)}" title="{_xml_attr(cat)}">')
        for f in kids:
            attrs = (f'type="rss" text="{_xml_attr(f.title or f.url)}" '
                     f'title="{_xml_attr(f.title or f.url)}" '
                     f'xmlUrl="{_xml_attr(f.url)}"')
            if (f.site_url or "").strip():
                attrs += f' htmlUrl="{_xml_attr(f.site_url)}"'
            L.append(f'    <outline {attrs} />')
        if cat:
            L.append("  </outline>")
    L += ["</body>", "</opml>", ""]
    return Response("\n".join(L), media_type="application/xml",
                    headers={"Content-Disposition":
                             'attachment; filename="quant-news-feeds.opml"'})


@app.delete("/api/news/feeds/{fid}")
def news_delete_feed(fid: int, u: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    f = db.get(NewsFeed, fid)
    if not f:
        raise HTTPException(404, "订阅源不存在")
    db.query(NewsItem).filter(NewsItem.feed_id == fid).delete()
    db.delete(f)
    db.commit()
    return {"ok": True}


@app.post("/api/news/fetch")
def news_fetch(body: Optional[NewsFetchReq] = None, u: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """抓取并入库。管理员手动触发；后续可挂 systemd timer 定时调本接口。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    _ensure_default_feeds(db)
    conf = newsfetch.relay_conf(db)
    if not (conf.get("url") and conf.get("key")):
        raise HTTPException(400, "尚未配置中继（订阅管理 → 中继设置）。本机 VPS 只有 IPv6 出站，"
                                 "不走中继将无法访问绝大多数资讯源。")
    fid = body.feed_id if body else None
    limit = (body.limit if body and body.limit else 60)
    return newsfetch.refresh_all(db, feed_id=fid, limit=limit)


@app.get("/api/news/settings")
def news_get_settings(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """中继配置状态。密钥永不回显，只回传是否已配置。"""
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    conf = newsfetch.relay_conf(db)
    return {"relay_url": conf.get("url") or "", "has_key": bool(conf.get("key"))}


@app.put("/api/news/settings")
def news_put_settings(body: NewsSettingsIn, u: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    if not u.is_admin:
        raise HTTPException(403, "forbidden")
    if body.relay_url is not None:
        newsfetch.put_setting(db, newsfetch.RELAY_URL_KEY, body.relay_url.strip().rstrip("/"))
    if body.relay_key is not None:
        newsfetch.put_setting(db, newsfetch.RELAY_KEY_KEY, body.relay_key.strip())
    db.commit()
    conf = newsfetch.relay_conf(db)
    return {"relay_url": conf.get("url") or "", "has_key": bool(conf.get("key"))}


@app.post("/api/news/items/{iid}/star")
def news_star(iid: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """收藏 / 取消收藏（站点级；单用户为主，未做按用户隔离）。"""
    it = db.get(NewsItem, iid)
    if not it:
        raise HTTPException(404, "条目不存在")
    it.star = 0 if it.star else 1
    db.commit()
    return {"id": it.id, "star": int(it.star or 0)}


# 启动时补齐默认订阅源与中继配置（幂等）
_news_boot = SessionLocal()
try:
    _ensure_default_feeds(_news_boot)
    _ensure_relay_settings(_news_boot)
except Exception as _e:      # 播种失败不应阻断启动
    logger.warning("资讯默认数据播种跳过：%s", _e)
finally:
    _news_boot.close()


# ---------------- 持仓模型迁移（一次性） ----------------
def _migrate_positions():
    """一次性迁移：将历史项目的 position_shares/position_cost 由『建仓种子』修正为
    『含全部反馈回放的当前持仓』。此后提交买卖会持续维护该值（见 submit_feedback）。
    幂等：用 settings 标记，已迁移则跳过，避免对反馈二次累加。"""
    db = SessionLocal()
    try:
        flag = db.query(Setting).filter(Setting.key == "migrated_position_maintained").first()
        if flag:
            return
        for p in db.query(Project).all():
            fbs = (db.query(Feedback).filter(Feedback.project_id == p.id)
                   .order_by(Feedback.date, Feedback.id).all())
            if not fbs:
                continue
            _, pos = _replay_raw(fbs, p.position_shares or 0.0, p.position_cost or 0.0)
            p.position_shares = round(pos["shares"], 4)
            p.position_cost = pos["avg"]
        db.add(Setting(key="migrated_position_maintained", value="1"))
        db.commit()
    except Exception as e:  # 迁移失败不应阻断启动
        logger.warning("持仓迁移跳过：%s", e)
        db.rollback()
    finally:
        db.close()


_migrate_positions()


# ---------------- 前端静态托管 ----------------
# 显式 API 路由已先于 mount 注册，/api/* 由 API 处理；其余路径由静态目录托管（html=True 使 / 返回 index.html）。
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
