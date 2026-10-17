#!/usr/bin/env python3
"""市场概要帖 → 论坛（crontab 用）：从当日高分资讯生成一篇「世界发生了什么」主题帖。

线上调度（服务器时区是 UTC，北京时间 = UTC+8，写 crontab 前务必换算）：
    20 13 * * *     北京 21:20 每日一次

必须晚于评分任务（20:50 的 scripts/news_score.py）——概要帖的素材是
「重要度 >= 4 且已加工」的当日资讯，评分没跑就没有素材可写。
两个任务共用一把锁（/tmp/quant-news.lock），串行执行；21:30 还有基金净值
补数（fetch_fund_nav.py）也写这个库，所以定格 21:20 错开。

与结算帖的分工：
    结算帖（scripts/settle_report.py，22:00）算账 —— 持仓数字、盈亏、收益率；
    本脚本说事 —— 今日大事、对持仓的影响、明日关注。
一个主题帖一天一篇，内容有变化才追加更新层（幂等，重跑不刷屏）。

用法示例：
    news_digest.py                      # 生成并发布今日概要帖
    news_digest.py --dry-run            # 只打印正文，不写库
    news_digest.py --date 2026-10-15    # 补发某一天

退出码：0 正常（含无素材跳过）/ 1 失败或等锁超时 / 2 参数或环境错误。
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
    ap = argparse.ArgumentParser(description="生成当日市场概要帖并发布到论坛")
    ap.add_argument("--db", default=os.environ.get("QUANTWEB_DB") or DEFAULT_DB,
                    help=f"库文件路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--date", default="",
                    help="北京日期 YYYY-MM-DD（默认今天）；用于补发")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要发布的正文，不写库")
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

    lock_fd = _acquire_lock(args.lock, args.lock_wait)
    if args.lock_wait > 0 and lock_fd is None:
        log("ERROR 等资讯锁超时（评分任务仍在跑），本次跳过")
        return 1

    db = SessionLocal()
    try:
        try:
            res = appmod._publish_news_digest(db, date_s, dry_run=args.dry_run)
        except Exception as e:
            db.rollback()
            log(f"FAIL 市场概要（{date_s}）生成/发布失败：{e}")
            return 1
        if res.get("skipped"):
            log(f"SKIP 市场概要（{date_s}）：{res.get('message')}")
            return 0
        if args.dry_run:
            log(f"[dry-run] 标题：{res['title']}")
            print("-" * 60)
            print(res["content"])
            print("-" * 60)
            log("[dry-run] 未写库。")
            return 0
        state = ("新建主题帖" if res.get("created")
                 else ("内容未变化，未追加" if res.get("unchanged") else "追加更新层"))
        log(f"OK   市场概要（{date_s}）→ 主题帖 #{res['thread_id']}（{state}，共 {res['posts']} 层）")
        log(f"     {res['title']}")
        return 0
    finally:
        db.close()
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
