#!/usr/bin/env python3
"""场外基金净值每日自动补数（crontab 用，30 13 * * 1-5）。

⚠️ 2026-10-16 起本脚本**不再自带一套拉取实现**，改为直接复用主系统的
   `datasource.fetch_hist(code, "fund", days)` —— 与网页端 /api/projects/{id}/fetch
   走的是同一份代码、同一个窗口口径、同一套写入语义。此前这里有三个不一致：
     1) 窗口 60 天 vs 主系统 365 天 → 新建基金被 cron 先填时只拿到 60 天
        （实例：id=11 code=022438 只有 39 条，同期同类基金均为 ~245 条）；
     2) 不写 projects.last_fetch_date → 网页端当天的自动拉取会重复请求一次；
     3) 两套分页/重试/解析逻辑并行维护。

保留原因：网页端要用户手动点才算拉取，交易日 13:30 由 cron 兜底自动补净值。
写入语义：**合并写（upsert）不删历史** —— 与主系统对 fund 的处理一致
（净值接口一次只回一段窗口，先删后写会把窗口外历史整段丢掉）。

设计纪律（避免回归 / 不误伤 A 股）：
- 只处理 market='fund' 的项目，绝不触碰场内品种行情；
- 唯一索引 + ON CONFLICT DO UPDATE，幂等，不清空、不覆盖窗口外数据；
- 单只基金失败不影响其它，退出码汇总（有失败返回 1）。
"""
import os
import sys
import sqlite3
import argparse
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)          # quant-web 根目录（datasource.py 所在处）
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

DEFAULT_DB = "/opt/quant-web/quantweb.db"
# 与主系统一致：app.fetch_prices 对 fund 的默认窗口是 365 自然日
DEFAULT_DAYS = 365


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_fetch_hist():
    """延迟导入主系统的取数函数：导入失败要给出可读原因，而不是裸 traceback。"""
    try:
        from datasource import fetch_hist
    except Exception as e:
        log(f"ERROR 无法导入主系统 datasource（{_APP_DIR}）：{e}")
        log("       本脚本依赖主系统同一份取数实现，请先确认 /opt/quant-web 完整。")
        raise SystemExit(3)
    return fetch_hist


def ensure_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mp_proj_date "
        "ON market_prices(project_id, date)"
    )


def upsert(conn: sqlite3.Connection, project_id: int, rows: list):
    """幂等合并写入，返回 (inserted, updated)。"""
    before = conn.execute(
        "SELECT count(*) FROM market_prices WHERE project_id=?", (project_id,)
    ).fetchone()[0]
    conn.executemany(
        "INSERT INTO market_prices (project_id, date, close) VALUES (?,?,?) "
        "ON CONFLICT(project_id, date) DO UPDATE SET close=excluded.close",
        [(project_id, d, c) for d, c in rows],
    )
    after = conn.execute(
        "SELECT count(*) FROM market_prices WHERE project_id=?", (project_id,)
    ).fetchone()[0]
    inserted = after - before
    return inserted, len(rows) - inserted


def _cn_today() -> str:
    """北京时间今天（与 app._cn_today 一致，VPS 时区不必是 UTC+8）。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser(description="场外基金净值每日补数（复用主系统取数实现）")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", DEFAULT_DB))
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"拉取窗口自然日数（默认 {DEFAULT_DAYS}，与主系统 fund 口径一致；"
                         "幂等合并，不会截断历史）")
    ap.add_argument("--full", action="store_true",
                    help="拉取全部历史（首次补全/补齐缺口用；可能因东财限速较慢）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要处理的基金，不写库")
    args = ap.parse_args()

    days = 99999 if args.full else args.days
    log(f"净值窗口：近 {days} 天" + ("（全量模式）" if args.full else "")
        + f" | 数据库 {args.db}")

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2

    fetch_hist = load_fetch_hist()
    # 与主系统 _cn_today() 同一口径（北京时间），供网页端自动拉取限频判断
    last_fetch_mark = _cn_today()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_index(conn)

    projects = conn.execute(
        "SELECT id, code, name FROM projects WHERE market='fund' ORDER BY id"
    ).fetchall()
    log(f"找到 {len(projects)} 只 fund 类项目")
    if not projects:
        log("无 fund 类项目，退出（如需处理某标的请先将其 market 设为 'fund'）")
        conn.close()
        return 0

    if args.dry_run:
        for pid, code, name in projects:
            log(f"[dry-run] 将处理: id={pid} code={code} name={name}")
        conn.close()
        return 0

    any_fail = False
    for pid, code, name in projects:
        try:
            rows, source = fetch_hist(code, "fund", days)
            if not rows:
                log(f"WARN id={pid} {code}({name}) 未取到净值")
                any_fail = True
                continue
            inserted, updated = upsert(conn, pid, rows)
            # 与网页端 /api/projects/{id}/fetch 一致：记录「今日已拉取」，
            # 否则当天用户在网页上打开该基金时还会再请求一次数据源。
            conn.execute("UPDATE projects SET last_fetch_date=? WHERE id=?",
                         (last_fetch_mark, pid))
            conn.commit()
            log(f"OK   id={pid} {code}({name}) 取{len(rows)}条 "
                f"新增{inserted}/更新{updated} 最新={rows[-1]} 来源={source}")
        except Exception as e:  # 单只失败不影响其它
            conn.rollback()
            log(f"FAIL id={pid} {code}({name}) 错误: {e}")
            any_fail = True

    conn.close()
    log("完成" + ("（存在失败项）" if any_fail else ""))
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
