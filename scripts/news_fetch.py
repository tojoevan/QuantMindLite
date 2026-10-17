#!/usr/bin/env python3
"""资讯抓取入库（crontab 用）：把各订阅源的最新条目抓回 news_items。

线上调度（服务器时区是 UTC，北京时间 = UTC+8，写 crontab 前务必换算）：
    0 4 * * *       北京 12:00 每日一次（午间档：收上午密集更新的源）
    30 12 * * *     北京 20:30 每日一次（晚间档：给 20:50 的评分备当天素材）

为什么补这个脚本：抓取原先只能靠管理员打开资讯页点「立即抓取」——而
资讯 → Agent 注入链路（20:50 评分 → 21:20 概要帖 → 席位提示词）要无人值守
运转，就不能依赖「有人开页面」。早晚各一次：晚间档必须早于 20:50 评分
（抓取与评分都写同一个 SQLite 库，错开 20 分钟；21:30 还有基金净值补数，
同样只做时间错峰）。与评分/概要共用 /tmp/quant-news.lock 串行。

本任务不调 LLM（只走 rss-relay 的 GET 通道取 RSS/Atom），无峰谷定价问题；
但中继是共享资源，--limit 别调太大。

设计纪律：
- 直接调 newsfetch.refresh_all（与 POST /api/news/fetch 同一函数），
  中继配置检查与 _ensure_default_feeds 照抄接口流程——脚本与页面同一条路。
- 只写 news_items 的源站字段，**绝不碰 ai_***（那是 newsai 的加工落点）。
- 单个源失败不影响其它源；退出码照实反映，方便 crontab 日志排障。

用法示例：
    news_fetch.py                    # 全量抓取（每源最多 60 条）
    news_fetch.py --limit 100        # 每源多抓些
    news_fetch.py --feed-id 3        # 只抓某个源
    news_fetch.py --dry-run          # 只列出会抓哪些源，不发请求

退出码：0 正常（含无更新）/ 1 有源失败或等锁超时 / 2 参数或环境错误。
"""
import os
import sys
import time
import fcntl
import argparse
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)          # quant-web 根目录（app.py 所在处）
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

DEFAULT_DB = "/opt/quant-web/quantweb.db"
DEFAULT_LOCK = "/tmp/quant-news.lock"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


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
    ap = argparse.ArgumentParser(description="资讯抓取入库（订阅源 → news_items）")
    ap.add_argument("--db", default=os.environ.get("QUANTWEB_DB") or DEFAULT_DB,
                    help=f"库文件路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--limit", type=int, default=60,
                    help="每个源最多抓多少条（默认 60）")
    ap.add_argument("--feed-id", type=int, default=0,
                    help="只抓某个源（默认 0 = 全部启用中的源）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出会抓哪些源，不发请求不写库")
    ap.add_argument("--lock", default=DEFAULT_LOCK, help=f"互斥锁文件（默认 {DEFAULT_LOCK}）")
    ap.add_argument("--lock-wait", type=int, default=180,
                    help="等锁秒数，超时则本次跳过（默认 180；0 = 不加锁）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2
    os.environ["QUANTWEB_DB"] = os.path.abspath(args.db)

    # 延迟导入：db.py 在建引擎时读 QUANTWEB_DB，必须放在参数解析之后
    from db import SessionLocal, NewsFeed
    import app as appmod
    import newsfetch

    lock_fd = _acquire_lock(args.lock, args.lock_wait)
    if args.lock_wait > 0 and lock_fd is None:
        log("ERROR 等资讯锁超时（另一个资讯任务仍在跑），本次跳过")
        return 1

    db = SessionLocal()
    try:
        appmod._ensure_default_feeds(db)
        q = db.query(NewsFeed)
        if args.feed_id:
            q = q.filter(NewsFeed.id == args.feed_id)
        else:
            q = q.filter(NewsFeed.enabled == 1)
        feeds = q.order_by(NewsFeed.sort, NewsFeed.id).all()
        log(f"源 {len(feeds)} 个（{'指定源' if args.feed_id else '全部启用中'}）"
            f"| limit={args.limit}/源 | 库 {args.db}")
        if args.dry_run:
            for f in feeds:
                log(f"  [dry-run] #{f.id} {f.title or f.url}")
            return 0
        if not feeds:
            log("没有可抓的源（订阅管理里添加，或检查是否被停用）")
            return 0

        # 中继配置检查与接口保持一致：本机只有 IPv6 出站，不走中继抓不到绝大多数源
        conf = newsfetch.relay_conf(db)
        if not (conf.get("url") and conf.get("key")):
            log("ERROR 尚未配置中继（订阅管理 → 中继设置）。本机 VPS 只有 IPv6 出站，"
                "不走中继将无法访问绝大多数资讯源。")
            return 2
        log(f"relay conf ok（url={conf.get('url')}）")

        stats = newsfetch.refresh_all(db, feed_id=args.feed_id or None, limit=args.limit)
        for r in stats.get("results", []):
            mark = "ok" if r.get("ok") else ("empty" if r.get("empty") else "FAIL")
            log(f"  [{mark}] #{r.get('feed_id')} {r.get('title') or ''} "
                f"+{r.get('added', 0)} 新 / ~{r.get('updated', 0)} 更新"
                + (f" | {r.get('error')}" if r.get("error") else ""))
        log(f"完成：{stats['ok']}/{stats['feeds']} 个源成功，"
            f"新增 {stats['added']} 条，更新 {stats['updated']} 条，"
            f"空源 {stats['empty']} 个")
        failed = [r for r in stats.get("results", []) if not r.get("ok") and not r.get("empty")]
        return 1 if failed else 0
    finally:
        db.close()
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
