#!/usr/bin/env bash
# quantweb.db 备份脚本（在 VPS 上运行，也由 deploy.sh db-backup 远程调用）
#
# - 使用 sqlite3 .backup 在线热备，无需停服务，事务安全
# - 备份后做 integrity_check，校验失败则删除该份备份并报错
# - 按天数清理旧备份，默认保留 7 天
#
# 可用环境变量覆盖：DB_PATH / BAK_ROOT / KEEP_DAYS

set -euo pipefail

DB_PATH="${DB_PATH:-/opt/quant-web/quantweb.db}"
BAK_ROOT="${BAK_ROOT:-/opt/quant-web-dbbackup}"
KEEP_DAYS="${KEEP_DAYS:-7}"

if [ ! -f "$DB_PATH" ]; then
  echo "[fail] $(date +%F' '%T) 找不到数据库：$DB_PATH"
  exit 1
fi
command -v sqlite3 >/dev/null 2>&1 || { echo "[fail] 远端缺少 sqlite3"; exit 1; }

mkdir -p "$BAK_ROOT"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$BAK_ROOT/db-${TS}.db"

sqlite3 "$DB_PATH" ".backup '$OUT'"
CHK="$(sqlite3 "$OUT" 'pragma integrity_check')"
if [ "$CHK" != "ok" ]; then
  echo "[fail] $(date +%F' '%T) 备份校验失败（$CHK），已删除：$OUT"
  rm -f "$OUT"
  exit 1
fi

SIZE="$(du -h "$OUT" | cut -f1)"
echo "[ok] $(date +%F' '%T) 备份完成：$OUT（$SIZE）integrity=ok"

find "$BAK_ROOT" -type f -name 'db-*.db' -mtime +"$KEEP_DAYS" -delete
echo "[ok] 已清理 ${KEEP_DAYS} 天前旧备份，现存 $(find "$BAK_ROOT" -type f -name 'db-*.db' | wc -l | tr -d ' ') 份"
