#!/usr/bin/env python3
"""周期结算帖 → 论坛（crontab 用）。日 / 周 / 月 / 季 / 半年 / 年，各自独立调度。

线上调度（服务器时区是 UTC，北京时间 = UTC+8，写 crontab 前务必换算）：
    日  0 14 * * 1-5   北京 22:00 周一~五      --periods day
    周  0 14 * * 0     北京 22:00 周日         --periods week
    月  0 14 1 * *     北京 22:00 每月 1 日    --periods month,quarter,half,year
（月/季/半年/年四条合并成一次运行：1 日 22:00 那一跑会把该结束的周期一次发完。）

为什么 22:00 而不是收盘就发：本站持仓 14/16 是场外基金，净值由基金公司在交易日
**19:00–22:00 陆续披露**（易方达官网口径「工作日晚上 9 点左右」），QDII 更要 T+1/T+2。
15:30 发出的日结，基金部分拿到的还是前一交易日的净值，等于每天看旧数据。
长周期同理：月结取「上一个自然月」，1 日 22:00 时上月净值已经出齐。

流程：
    1) 先补最新行情（复用网页端 /api/projects/{id}/fetch 同一个函数，见下）
    2) 逐个周期生成正文（app._build_settlement）并发布（app._publish_settlement）

设计纪律：
- **补数直接调用 `app.fetch_prices`**，不另写一套拉取实现 —— 窗口口径、覆盖式/合并式
  写入语义、缩水守卫、last_fetch_date 限频全部与网页端一致，避免出现「第四套逻辑」。
- 补数**只做一次**，多个周期共用；1 日那条会同时发月/季/半年/年，别拉四遍。
- 单只标的补数失败只记 warn，不阻断结算（帖子里逐条标净值日期）。
- 空窗口不发布（期末无持仓且窗口内无操作），退出码 0。
- 幂等：同一周期重复执行只会写同一个主题帖；内容没变不追加更新层。
- **文件锁**：1 日 22:00 若恰逢周一~五，日报任务与月结任务会同时启动，两个进程一起
  补数 + 写库会互相抢 SQLite 锁。谁先拿到锁谁先跑，另一个最多等 --lock-wait 秒。

用法示例：
    settle_report.py                                  # 日结（默认）
    settle_report.py --periods month,quarter          # 只发月结与季结
    settle_report.py --periods week --dry-run         # 只看正文，不写库
    settle_report.py --periods month --key 2026-09    # 指定周期键补发

退出码：0 正常（含空窗口跳过）/ 1 有失败项 / 2 参数或环境错误。
"""
import os
import sys
import time
import fcntl
import argparse
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)          # quant-web 根目录（app.py 所在处）
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

DEFAULT_DB = "/opt/quant-web/quantweb.db"
DEFAULT_LOCK = "/tmp/quant-settle.lock"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _cn_today() -> str:
    """北京时间今天。VPS 系统时区是 UTC，不能用 datetime.now().date()。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _acquire_lock(path: str, wait: int):
    """拿结算锁（阻塞直到超时）。返回文件对象；wait<=0 表示不加锁。"""
    if wait <= 0:
        return None
    fd = open(path, "w")
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.time() >= deadline:
                fd.close()
                return None
            time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成并发布周期结算帖到论坛")
    ap.add_argument("--db", default=os.environ.get("QUANTWEB_DB") or DEFAULT_DB,
                    help=f"库文件路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--periods", default="day",
                    help="结算周期，逗号分隔（day,week,month,quarter,half,year；默认 day）")
    ap.add_argument("--key", default="",
                    help="周期键（2026-10-17 / 2026-W42 / 2026-09 / 2026-Q3 / 2026-H2 / 2026）；"
                         "只能配合单个周期使用，留空 = 最近一个已结束周期")
    ap.add_argument("--date", default="",
                    help="「今天」的日期 YYYY-MM-DD（默认北京时间今天）；用于回溯/试跑")
    ap.add_argument("--no-fetch", action="store_true",
                    help="跳过补数，直接用库里现有行情生成（离线/调试用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要发布的正文，不写库（也不补数）")
    ap.add_argument("--lock", default=DEFAULT_LOCK, help=f"互斥锁文件（默认 {DEFAULT_LOCK}）")
    ap.add_argument("--lock-wait", type=int, default=300,
                    help="等锁秒数，超时则本次跳过（默认 300；0 = 不加锁）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2
    os.environ["QUANTWEB_DB"] = os.path.abspath(args.db)

    # 延迟导入：db.py 在建引擎时读 QUANTWEB_DB，必须放在参数解析之后
    from db import SessionLocal, Project, User
    import app as appmod

    periods, bad = [], []
    for x in (args.periods or "").split(","):
        x = x.strip().lower()
        if not x:
            continue
        if x not in appmod.SETTLE_PERIODS:
            bad.append(x)
        elif x not in periods:
            periods.append(x)
    if bad:
        log(f"ERROR 未知周期：{','.join(bad)}（可选 {','.join(appmod.SETTLE_PERIODS)}）")
        return 2
    if not periods:
        log("ERROR --periods 为空")
        return 2
    if args.key and len(periods) != 1:
        log("ERROR --key 只能配合单个周期使用")
        return 2
    today = args.date or _cn_today()
    if args.key and not appmod._valid_period_key(periods[0], args.key):
        log(f"ERROR 周期键与周期不匹配：{periods[0]} / {args.key}")
        return 2

    lock_fd = _acquire_lock(args.lock, args.lock_wait)
    if args.lock_wait > 0 and lock_fd is None:
        log("ERROR 等结算锁超时（另一个结算任务仍在跑），本次跳过")
        return 1

    db = SessionLocal()
    try:
        projects = (db.query(Project)
                    .order_by(Project.pinned.desc(), Project.created_at.desc()).all())
        held = [p for p in projects if (p.position_shares or 0) > 0]
        log(f"标的 {len(projects)} 个（有持仓 {len(held)} 个）| "
            f"周期 {'+'.join(periods)} | 基准日 {today} | 库 {args.db}")

        if args.dry_run:
            for period in periods:
                key = args.key or appmod._latest_closed_key(period, today)
                title, content, meta = appmod._build_settlement(period, key, db)
                log(f"[dry-run] {appmod.SETTLE_LABELS[period]}({key}) 标题：{title}")
                print("-" * 60)
                print(content)
                print("-" * 60)
                log(f"[dry-run] 未写库。meta={meta}")
            return 0

        fails = 0
        if not args.no_fetch:
            admin = db.query(User).filter(User.is_admin == 1).order_by(User.id).first()
            if admin is None:
                log("WARN 无管理员用户，跳过行情补数（无法复用 fetch_prices 的鉴权口径）")
            else:
                for p in held:
                    try:
                        r = appmod.fetch_prices(p.id, None, u=admin, db=db)
                        if r.get("skipped"):
                            log(f"SKIP id={p.id} {p.name}：{r.get('message')}")
                        else:
                            log(f"OK   id={p.id} {p.name} 取{r.get('count')}条 "
                                f"{r.get('start')}~{r.get('end')} 最新={r.get('latest_close')}")
                    except Exception as e:
                        db.rollback()      # 单只失败不影响其它，也不阻断结算
                        fails += 1
                        log(f"FAIL id={p.id} {p.name} 补数失败：{e}")

        for period in periods:
            key = args.key or appmod._latest_closed_key(period, today)
            tag = f"{appmod.SETTLE_LABELS[period]}({key})"
            try:
                res = appmod._publish_settlement(db, period, key)
            except Exception as e:
                db.rollback()
                fails += 1
                log(f"FAIL {tag} 生成/发布失败：{e}")
                continue
            if res.get("skipped"):
                log(f"SKIP {tag}：{res.get('message')}")
                continue
            state = ("新建主题帖" if res.get("created")
                     else ("内容未变化，未追加" if res.get("unchanged") else "追加更新层"))
            log(f"OK   {tag} → 主题帖 #{res['thread_id']}（{state}，共 {res['posts']} 层）")
            log(f"     {res['title']}")
            log(f"     持仓 {res['holdings']} 个 | 期末市值 {res['market_value']} | "
                f"浮动 {res['unrealized']} | 区间变化 {res['change']} | "
                f"数据日期 {res['data_date']}")
        return 1 if fails else 0
    finally:
        db.close()
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
