#!/usr/bin/env python3
"""每日持仓日报 → 论坛（crontab 用，0 14 * * 1-5 = 北京时间 22:00 周一~五）。

为什么要 22:00 而不是收盘就发：本站持仓 14/16 是场外基金，净值由基金公司在交易日
**19:00–22:00 陆续披露**（易方达官网口径「工作日晚上 9 点左右」），QDII 更要 T+1/T+2。
15:30 发出来的日报，基金部分拿到的还是前一交易日的净值，等于每天看旧数据。

流程：
    1) 先补最新行情（复用网页端 `/api/projects/{id}/fetch` 同一个函数，见下）
    2) 生成日报正文（app._build_daily_report）
    3) 发布到论坛（app._publish_daily_report，每天一篇、内容有变化才追加更新层）

设计纪律：
- **补数直接调用 `app.fetch_prices`**，不另写一套拉取实现 —— 窗口口径、覆盖式/合并式
  写入语义、缩水守卫、last_fetch_date 限频全部与网页端一致，避免出现「第四套逻辑」。
- 单只标的补数失败只记 warn，不阻断日报（日记用现有数据照发，明细里会标净值日期）。
- 无持仓时不发布（避免每天刷一条空帖），退出码 0。
- 幂等：同一天重复执行只会写同一个帖；内容没变不追加。

退出码：0 正常（含无持仓跳过）/ 1 有失败项 / 2 参数或环境错误。
"""
import os
import sys
import argparse
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)          # quant-web 根目录（app.py 所在处）
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

DEFAULT_DB = "/opt/quant-web/quantweb.db"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _cn_today() -> str:
    """北京时间今天。VPS 系统时区是 UTC，不能用 datetime.now().date()。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成并发布当日持仓日报到论坛")
    ap.add_argument("--db", default=os.environ.get("QUANTWEB_DB") or DEFAULT_DB,
                    help=f"库文件路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--date", default="", help="日报日期 YYYY-MM-DD（默认北京今天）")
    ap.add_argument("--no-fetch", action="store_true",
                    help="跳过补数，直接用库里现有行情生成（离线/调试用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要发布的正文，不写库")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2
    os.environ["QUANTWEB_DB"] = os.path.abspath(args.db)

    # 延迟导入：db.py 在建引擎时读 QUANTWEB_DB，必须放在参数解析之后
    from db import SessionLocal, Project, User
    import app as appmod

    date_s = args.date or _cn_today()
    db = SessionLocal()
    try:
        projects = (db.query(Project)
                    .order_by(Project.pinned.desc(), Project.created_at.desc()).all())
        held = [p for p in projects if (p.position_shares or 0) > 0]
        log(f"标的 {len(projects)} 个（有持仓 {len(held)} 个）| 日报日期 {date_s} | 库 {args.db}")

        if args.dry_run:
            title, content, meta = appmod._build_daily_report(date_s, db)
            log(f"[dry-run] 标题：{title}")
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
                        db.rollback()      # 单只失败不影响其它，也不阻断日报
                        fails += 1
                        log(f"FAIL id={p.id} {p.name} 补数失败：{e}")

        res = appmod._publish_daily_report(db, date_s)
        if res.get("skipped"):
            log(f"跳过发布：{res.get('message')}")
            return 1 if fails else 0
        state = ("新建主题帖" if res.get("created")
                 else ("内容未变化，未追加" if res.get("unchanged") else "追加更新层"))
        log(f"已发布 → 主题帖 #{res['thread_id']}（{state}，共 {res['posts']} 层）")
        log(f"    标题：{res['title']}")
        log(f"    持仓 {res['holdings']} 个 | 总市值 {res['market_value']} | "
            f"浮动 {res['unrealized']} | 本期 {res['change']} | 数据日期 {res['data_date']}")
        return 1 if fails else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
