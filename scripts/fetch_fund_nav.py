#!/usr/bin/env python3
"""基金净值自动获取（独立脚本，不依赖主系统 datasource/app.py）。

遍历 projects 中 market='fund' 的标的，从东方财富基金净值接口
(https://api.fund.eastmoney.com/f10/lsjz) 拉取历史单位净值(DWJZ)，
按 (project_id, date) 幂等 upsert 进 market_prices。

设计纪律（避免回归 / 不误伤 A 股）：
- 只动 market='fund' 的项目，绝不触碰 A 股行情；
- 用唯一索引 + ON CONFLICT DO UPDATE 做 upsert，不清空、不覆盖已有数据；
- 纯标准库（urllib/sqlite3/json），VPS 系统 python3 即可运行；
- 单只基金失败不影响其它，退出码汇总（有失败返回 1）。
"""
import os
import sys
import json
import ssl
import socket
import sqlite3
import argparse
import urllib.request
import time
from datetime import datetime, timedelta

# 本机出站数据走 IPv6（IPv4 不可达，curl -4 失败 / curl -6 成功），直连东财
# 等域名解析到的 IPv4 地址不可达；强制 getaddrinfo 只返回 IPv6，避免 urllib
# 选中死链路的 A 地址。
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv6(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv6

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

DEFAULT_DB = "/opt/quant-web/quantweb.db"
API = "https://api.fund.eastmoney.com/f10/lsjz"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://fundf10.eastmoney.com/",
}
# 东财 lsjz 接口对大 pageSize 会返回空，实测每页 20 条稳定；按时间窗口分页。
PAGE_SIZE = 20
MAX_PAGES = 300  # 上限保护（约 6000 条，远超单只基金生命周期）


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _fetch_page(fund_code: str, page: int, start_date: str, end_date: str) -> list:
    """拉单页净值列表，失败重试 3 次（东财偶发慢/限速）。"""
    url = (f"{API}?fundCode={fund_code}&pageIndex={page}&pageSize={PAGE_SIZE}"
           f"&startDate={start_date}&endDate={end_date}")
    req = urllib.request.Request(url, headers=HEADERS)
    last_err = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            j = json.loads(raw)
            return (j.get("Data") or {}).get("LSJZList") or []
        except Exception as e:  # 网络/超时/JSON 异常，重试
            last_err = e
            time.sleep(1.5 * attempt)
    raise last_err


def fetch_nav(fund_code: str, start_date: str, end_date: str) -> list:
    """分页拉取窗口内历史单位净值，返回 [(date_str, close_float), ...] 升序。
    跳过 DWJZ 为空的记录（分红 / 拆分等无净值日期）。"""
    out = []
    for page in range(1, MAX_PAGES + 1):
        lst = _fetch_page(fund_code, page, start_date, end_date)
        if not lst:
            break
        for r in lst:
            d = (r.get("FSRQ") or "").strip()
            dwjz = (r.get("DWJZ") or "").strip()
            if not d:
                continue
            try:
                c = float(dwjz)
            except ValueError:
                # 分红 / 拆分等无净值日期，跳过
                continue
            if c <= 0:
                continue
            out.append((d, c))
        if len(lst) < PAGE_SIZE:
            break
        time.sleep(0.3)  # 页间小延迟，避免被限速
    out.sort(key=lambda x: x[0])
    return out


def ensure_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mp_proj_date "
        "ON market_prices(project_id, date)"
    )


def upsert(conn: sqlite3.Connection, project_id: int, rows: list):
    """幂等写入，返回 (inserted, updated)。"""
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
    updated = len(rows) - inserted
    return inserted, updated


def main() -> int:
    ap = argparse.ArgumentParser(description="基金净值自动获取")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", DEFAULT_DB))
    ap.add_argument("--days", type=int, default=60,
                    help="拉取窗口天数（默认60，覆盖最近约3个月，幂等补全/更新；首次全量用 --full）")
    ap.add_argument("--full", action="store_true",
                    help="拉取全部历史（分页到完，首次补全用；可能因东财限速较慢）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要处理的基金，不写库")
    args = ap.parse_args()

    end_date = datetime.now().strftime("%Y-%m-%d")
    days = 99999 if args.full else args.days
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    log(f"净值窗口: {start_date} ~ {end_date}"
        + ("（全量模式）" if args.full else f"（近 {args.days} 天）"))

    if not os.path.exists(args.db):
        log(f"ERROR 数据库不存在: {args.db}")
        return 2

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
            rows = fetch_nav(code, start_date, end_date)
            if not rows:
                log(f"WARN id={pid} {code}({name}) 未取到净值")
                any_fail = True
                continue
            inserted, updated = upsert(conn, pid, rows)
            conn.commit()
            log(f"OK   id={pid} {code}({name}) 取{len(rows)}条 "
                f"新增{inserted}/更新{updated} 最新={rows[-1]}")
        except Exception as e:  # 单只失败不影响其它
            conn.rollback()
            log(f"FAIL id={pid} {code}({name}) 错误: {e}")
            any_fail = True

    conn.close()
    log("完成" + ("（存在失败项）" if any_fail else ""))
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
