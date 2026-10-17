#!/usr/bin/env python3
"""圆桌观点复盘（crontab 用）——G3「建议 → 结果」的最后一环，每日自动跑。

线上调度（服务器时区 UTC，北京时间 = UTC+8，写 crontab 前务必换算）：
    10 14 * * *     北京 22:10 每日一次

时序：settle_report（22:00 算账）→ 本任务（22:10 复盘昨日观点）→ 之后无其他
写库任务；21:30 基金净值补数早已结束，T+1 收益此时可算。时段落在 DeepSeek
闲时（峰谷高峰 = 北京 9:00-12:00 / 14:00-18:00，闲时半价），一次调用 ≈ 几分钱。

做什么（rtreview.daily）：
    1. 复盘昨日观点：LLM 抽取「标的+方向」→ 机械比对真实行情判 T+1/T+5；
    2. 补算近 5 个交易日未满窗记录（T+5 到期后判定才最终化，渐进式）。
抽取只对 auto 帖做（manual 帖是用户从外部 AI 复制的，编号体系不同，
依据不可追溯——如实留空，不硬挂）。

用法示例：
    rt_review.py                    # 每日模式：复盘昨日 + 补算近窗
    rt_review.py --date 2026-10-14  # 只复盘指定日期（补跑用）
    rt_review.py --backfill 14      # 补批近 14 天有观点但没复盘的日期
    rt_review.py --dry-run          # 只对已有记录重算判定，不调 LLM 不抽新

退出码：0 正常（含无观点跳过）/ 1 失败或等锁超时 / 2 参数或环境错误。
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
DEFAULT_LOCK = "/tmp/quant-news.lock"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _cn_today() -> str:
    """北京时间今天。VPS 系统时区是 UTC，不能用 datetime.now().date()。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _acquire_lock(path: str, wait: int):
    """拿资讯任务锁（阻塞直到超时）。返回文件对象；wait<=0 表示不加锁。"""
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
    ap = argparse.ArgumentParser(description="圆桌观点复盘：抽取判断 + 机械判定 T+1/T+5")
    ap.add_argument("--db", default=os.environ.get("QUANTWEB_DB") or DEFAULT_DB,
                    help=f"库文件路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--date", default="",
                    help="北京日期 YYYY-MM-DD：只复盘该日（缺省每日模式：昨日+近窗补算）")
    ap.add_argument("--backfill", type=int, default=0,
                    help="补批近 N 天有观点但没复盘的日期（一次性动作）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只对已有记录重算判定，不调 LLM、不抽新判断")
    ap.add_argument("--lock", default=DEFAULT_LOCK, help=f"互斥锁文件（默认 {DEFAULT_LOCK}）")
    ap.add_argument("--lock-wait", type=int, default=180,
                    help="等锁秒数，超时则本次跳过（默认 180；0 = 不加锁）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2
    date_s = args.date or _cn_today()
    try:
        datetime.strptime(date_s, "%Y-%m-%d")
    except ValueError:
        log(f"ERROR 日期格式不对：{date_s}（应为 YYYY-MM-DD）")
        return 2
    os.environ["QUANTWEB_DB"] = os.path.abspath(args.db)

    # 延迟导入：db.py 在建引擎时读 QUANTWEB_DB，必须放在参数解析之后
    from db import SessionLocal
    import app as appmod
    import rtreview

    lock_fd = _acquire_lock(args.lock, args.lock_wait)
    if args.lock_wait > 0 and lock_fd is None:
        log("ERROR 等资讯锁超时（其他资讯任务仍在跑），本次跳过")
        return 1

    db = SessionLocal()
    try:
        conf = None
        if not args.dry_run:
            try:
                conf = appmod.news_llm_conf(db)
            except Exception as e:
                log(f"ERROR LLM 配置不可用（抽取需要）：{e}")
                return 1
        try:
            if args.dry_run:
                n = rtreview.review_date(db, date_s, log=log)
                log(f"[dry-run] 只重算判定（{date_s}）：{n} 条记录")
                return 0
            if args.backfill > 0:
                total = rtreview.backfill(db, conf, days=args.backfill, log=log)
                log(f"OK   补批 {args.backfill} 天：{total['dates']} 个日期、"
                    f"{total['posts']} 条观点、{total['calls']} 条判断")
                return 0
            if args.date:
                r = rtreview.run_review(db, conf, date_s, log=log)
                log(f"OK   复盘 {date_s}：{r['posts']} 条观点 → {r['calls']} 条判断"
                    f"（{r['records']} 条记录）")
                return 0
            r = rtreview.daily(db, conf, log=log)
            log(f"OK   每日复盘：昨日 {r['reviewed']['posts']} 条观点 → "
                f"{r['reviewed']['calls']} 条判断；近窗补算 {r['refreshed']} 条记录")
            return 0
        except Exception as e:
            db.rollback()
            log(f"FAIL 观点复盘失败：{type(e).__name__}: {e}")
            return 1
    finally:
        db.close()
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
