#!/usr/bin/env python3
"""资讯 AI 结构化加工（crontab 用）：把未加工的资讯批量送 LLM，回填 ai_* 字段。

线上调度（服务器时区是 UTC，北京时间 = UTC+8，写 crontab 前务必换算）：
    50 12 * * *     北京 20:50 每日一次   --limit 80

为什么 20:50 而不是 21:00：21:30 有基金净值补数（fetch_fund_nav.py，周一~五），
两个任务都写同一个 SQLite 库，错开 40 分钟更稳（本任务与 21:20 的概要帖共用
/tmp/quant-news.lock 串行，跟补数之间只留时间错峰）。
订阅源全天陆续更新，晚间跑一次能覆盖当天绝大多数条目；市场概要帖（21:20）
紧接着用这批评分，顺序不能反。

加工结果的三处消费：
    1. 资讯页徽标（ai_score / ai_reason / ai_tags / ai_codes / related）
    2. 圆桌席位提示词注入（newsai.select_news_for_prompt）
    3. 论坛「市场概要」帖（scripts/news_digest.py）

设计纪律：
- **LLM 配置走 app.news_llm_conf**（Setting news.llm_provider，默认 deepseek），
  出站路由由 app._llm_route 自动判断：目标无 AAAA 时改走 rss-relay POST 通道
  （2026-10-17 实测：api.deepseek.com 在 IPv6-only 的 VPS 上直连必败）。
- 分块送模型（newsai.SCORE_CHUNK 条/块），单块失败不影响其它块，退出码照实反映。
- 只加工 ai_score 为空的条目（增量）；补跑历史用 --limit 调大、--days 放宽窗口。
- 文件锁与结算任务共用一把（/tmp/quant-news.lock）：21:30 的发帖任务紧接着跑，
  两个任务都写库，串行更稳；互等超时本次跳过。

用法示例：
    news_score.py                        # 加工最近 5 天未评分条目（默认 limit 60）
    news_score.py --limit 200 --days 7   # 补跑历史
    news_score.py --dry-run              # 只看选条与解析结果，不写库

退出码：0 正常（含无条目可加工）/ 1 有失败块或等锁超时 / 2 参数或环境错误。
"""
import os
import sys
import time
import fcntl
import argparse
from datetime import datetime, timedelta

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
    ap = argparse.ArgumentParser(description="资讯 AI 结构化加工（评分/理由/标签/关联标的）")
    ap.add_argument("--db", default=os.environ.get("QUANTWEB_DB") or DEFAULT_DB,
                    help=f"库文件路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--limit", type=int, default=60,
                    help="本次最多加工多少条（默认 60；补跑历史可调大）")
    ap.add_argument("--days", type=int, default=5,
                    help="加工窗口：只处理最近 N 天发布的条目（默认 5）")
    ap.add_argument("--provider", default="",
                    help="覆盖 LLM 服务商（默认读 Setting news.llm_provider，再默认 deepseek）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印选条与解析结果，不写库")
    ap.add_argument("--lock", default=DEFAULT_LOCK, help=f"互斥锁文件（默认 {DEFAULT_LOCK}）")
    ap.add_argument("--lock-wait", type=int, default=180,
                    help="等锁秒数，超时则本次跳过（默认 180；0 = 不加锁）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2
    os.environ["QUANTWEB_DB"] = os.path.abspath(args.db)

    # 延迟导入：db.py 在建引擎时读 QUANTWEB_DB，必须放在参数解析之后
    from db import SessionLocal
    import app as appmod
    import newsai

    lock_fd = _acquire_lock(args.lock, args.lock_wait)
    if args.lock_wait > 0 and lock_fd is None:
        log("ERROR 等资讯锁超时（另一个资讯任务仍在跑），本次跳过")
        return 1

    db = SessionLocal()
    try:
        try:
            conf = appmod.news_llm_conf(db)
            if args.provider:
                conf = appmod._get_llm_conf(db, args.provider)
                conf.update(appmod._llm_route(db, conf["base_url"]))
                if not conf.get("model"):
                    conf["model"] = (appmod.PROVIDER_DEFAULTS.get(args.provider) or {}).get("model", "")
        except Exception as e:
            log(f"ERROR LLM 配置不可用：{e}")
            return 2
        log(f"provider conf ok（model={conf.get('model')} "
            f"relay={'是' if conf.get('via_relay') else '否'}）| limit={args.limit} "
            f"days={args.days} | 库 {args.db}")

        stats = newsai.score_items(db, conf, limit=args.limit, days=args.days,
                                   dry_run=args.dry_run, log=log)
        log(f"完成：选中 {stats['picked']} 条，加工 {stats['scored']} 条，"
            f"{stats['chunks']} 块成功，{stats['failed']} 块失败"
            + ("（dry-run 未写库）" if args.dry_run else ""))
        return 1 if stats["failed"] else 0
    finally:
        db.close()
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
