#!/usr/bin/env python3
"""把历史圆桌观点一次性回填成论坛存档帖（幂等，可反复执行）。

背景：2026-10-17 起，圆桌的两个写入口（手动粘贴回填 / LLM 自动生成）在保存观点时
会**自动同步**一条论坛存档帖，方便后期回看历史信息。此前写入的观点没有存档，
本脚本按**同一套语义**补齐，避免出现「第四套逻辑」：

    每个「席位 × 日期」= 一个主题帖（标题 `【圆桌】YYYY-MM-DD · 席位名`），
    观点正文作为首帖；已同步过的观点一律跳过，绝不重复建帖。

用法（服务器上，WorkDir 无所谓，脚本自己找 app 目录）：
    /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/sync_roundtable_forum.py --dry-run
    /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/sync_roundtable_forum.py

退出码：0 全部成功 / 1 有失败项（便于人工复核）。
"""
import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)               # quant-web 根目录（app.py 所在处）
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="回填圆桌观点 → 论坛存档帖")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要回填的观点，不写库")
    ap.add_argument("--db", default="", help="库文件路径（默认项目内 quantweb.db，副本试跑用）")
    args = ap.parse_args()
    if args.db:
        os.environ["QUANTWEB_DB"] = os.path.abspath(args.db)

    # 延迟导入：db.py 在建引擎时读 QUANTWEB_DB，必须放在参数解析之后
    from db import SessionLocal, RoundtablePost, RoundtableSeat
    import app as appmod

    db = SessionLocal()
    try:
        rows = (db.query(RoundtablePost)
                .order_by(RoundtablePost.date, RoundtablePost.seat_id).all())
        pending = [p for p in rows if not p.thread_id]
        log(f"圆桌观点共 {len(rows)} 条，其中未存档 {len(pending)} 条"
            f"（已存档的 {len(rows) - len(pending)} 条跳过）")
        if not pending:
            log("没有需要回填的观点。")
            return 0
        if args.dry_run:
            for p in pending:
                seat = db.get(RoundtableSeat, p.seat_id)
                name = (seat.name if seat else f"（席位 {p.seat_id} 已删除）")
                log(f"  [dry-run] {p.date} · {name} · 观点 #{p.id} · {len(p.content or '')} 字")
            log("dry-run 结束，未写库。")
            return 0

        created = failed = 0
        for p in pending:
            seat = db.get(RoundtableSeat, p.seat_id)
            if seat is None:
                log(f"  ⚠ 跳过观点 #{p.id}：席位 {p.seat_id} 已删除")
                failed += 1
                continue
            if not (p.content or "").strip():
                log(f"  ⚠ 跳过观点 #{p.id}：内容为空")
                failed += 1
                continue
            try:
                tid, _added = appmod._sync_roundtable_forum(db, p, seat, p.content)
                db.commit()
            except Exception as e:
                db.rollback()
                log(f"  ✗ 观点 #{p.id}（{p.date} · {seat.name}）存档失败：{e}")
                failed += 1
                continue
            created += 1
            log(f"  ✓ {p.date} · {seat.name} → 主题帖 #{tid}")
        log(f"完成：新建存档 {created} 条，失败 {failed} 条。")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
