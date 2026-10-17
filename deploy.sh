#!/usr/bin/env bash
# quant-web 部署 / 运维工具（本地运行，ssh -6 直连 IPv6 VPS）
#
# 用法：
#   bash deploy.sh verify                # 只做本地验证（语法 + 导入 + 冒烟），不碰线上
#   bash deploy.sh status                # 检测生产环境状态：SSH / systemd / health / 磁盘 / 备份
#   bash deploy.sh deploy                # 默认：本地验证 → 线上备份 → 同步 → 重启 → 健康检查（失败自动回退）
#   bash deploy.sh deploy --dry-run      # 只看将要同步的文件，不做任何远端改动
#   bash deploy.sh deploy --skip-verify  # 跳过本地验证（紧急修复用）
#   bash deploy.sh backup                # 仅在远端打一份代码快照
#   bash deploy.sh backups               # 列出远端备份
#   bash deploy.sh db-backup             # 备份线上 quantweb.db（在线热备 + 完整性校验）
#   bash deploy.sh db-backups            # 列出线上数据库备份
#   bash deploy.sh rollback [TS]         # 回退到指定备份（默认最近一份）
#
# 说明：
#   - 备份只含源码与静态资源（排除 .venv / quantweb.db / __pycache__ / ._*），回退不改动数据库。
#   - 数据库 schema 变更不在回退范围内，涉及建表/迁移的改动请先自行备份 quantweb.db。

set -euo pipefail

# ---------------- 配置 ----------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="$SCRIPT_DIR"                    # 本地源码目录（取脚本所在目录，不再写死绝对路径）
VPS_USER="root"
VPS_HOST="<生产服务器 IPv6（已脱敏）>"          # IPv6 VPS
VPS="$VPS_USER@$VPS_HOST"
# rsync 的 remote 目标必须用方括号包住 IPv6，否则冒号会被当成 host/path 分隔符
RSYNC_TARGET="$VPS_USER@[$VPS_HOST]"
SSH_OPTS="-6 -o StrictHostKeyChecking=no -o ConnectTimeout=10"
REMOTE_DIR="/opt/quant-web"                # 远端运行目录
RELEASE_ROOT="/opt/quant-web-releases"     # 远端备份根目录
SERVICE="quant-web"                        # systemd 服务名
PORT="8090"                                # uvicorn 端口（仅监听 127.0.0.1）
KEEP_BACKUPS=5                             # 远端保留备份份数
PY_FILES=(app.py db.py models.py predictor.py datasource.py llm.py newsfetch.py newsai.py)

# ---------------- 输出 ----------------
if [ -t 1 ]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_OFF=""
fi
info() { echo "${C_GREEN}[ok]${C_OFF}   $*"; }
warn() { echo "${C_YELLOW}[warn]${C_OFF} $*"; }
err()  { echo "${C_RED}[fail]${C_OFF} $*" >&2; }
step() { echo "${C_DIM}-->${C_OFF} $*"; }
die()  { err "$*"; exit 1; }

ssh_run() { ssh $SSH_OPTS "$VPS" "$@"; }

pick_python() {
  if [ -x "$LOCAL_DIR/.venv/bin/python" ]; then
    LOCAL_PY="$LOCAL_DIR/.venv/bin/python"
  else
    LOCAL_PY="python3"
  fi
}

# 同步统一走 tar over ssh：macOS 自带 openrsync(2.6.9) 不支持 IPv6 remote 目标语法，
# 写 user@[2404:...]:/path 会被按冒号错误切分，导致同步静默失败。
tar_sync() {
  local src_dir="$1" remote_sub="$2"; shift 2
  local args=""
  local p
  for p in "$@"; do args="$args --exclude $p"; done
  # COPYFILE_DISABLE=1 + --no-xattrs：避免 macOS 写入 ._ AppleDouble 与 xattr 扩展头
  COPYFILE_DISABLE=1 tar -C "$src_dir" --no-xattrs $args -cf - . \
    | ssh $SSH_OPTS "$VPS" "mkdir -p ${REMOTE_DIR}/${remote_sub} && tar -xf - -C ${REMOTE_DIR}/${remote_sub}"
}

# 列出本地与远端的文件差异（基于 md5 清单，替代 rsync --dry-run）
show_diff() {
  local local_mf remote_mf out
  local_mf="$(cd "$LOCAL_DIR" && "$LOCAL_PY" scripts/file_manifest.py .)"
  remote_mf="$(ssh_run "cd ${REMOTE_DIR} && python3 scripts/file_manifest.py ." 2>/dev/null || echo '')"
  out="$(diff <(printf '%s\n' "$local_mf") <(printf '%s\n' "$remote_mf") || true)"
  if [ -z "$out" ]; then
    info "无文件差异（远端已是最新）"
    DIFF_COUNT=0
  else
    echo "       (- 本地新增/有改动   + 远端独有/已删除)"
    printf '%s\n' "$out" | grep -E '^[<>]' | sed 's/^/       /' || true
    DIFF_COUNT="$(printf '%s\n' "$out" | grep -cE '^[<>]' || true)"
  fi
}

# ---------------- 本地验证 ----------------
cmd_verify() {
  step "本地验证：语法 / 导入 / 冒烟启动"

  if [ -x "$LOCAL_DIR/.venv/bin/python" ]; then
    LOCAL_PY="$LOCAL_DIR/.venv/bin/python"
  else
    LOCAL_PY="python3"
    warn "未找到 .venv，改用系统 python3（依赖可能不全）"
  fi

  for f in "${PY_FILES[@]}"; do
    [ -f "$LOCAL_DIR/$f" ] || die "缺少源文件：$f"
  done

  # 1) 语法检查
  ( cd "$LOCAL_DIR" && "$LOCAL_PY" -m py_compile "${PY_FILES[@]}" ) || die "语法检查失败"
  info "语法检查通过（${#PY_FILES[@]} 个文件）"

  # 2) 导入 + 冒烟：在临时目录副本里跑，避免污染本地 quantweb.db
  TMP_DIR="$(mktemp -d)"
  cp -a "${PY_FILES[@]/#/$LOCAL_DIR/}" "$TMP_DIR/" 2>/dev/null || die "拷贝源文件到临时目录失败"
  [ -f "$LOCAL_DIR/requirements.txt" ] && cp -a "$LOCAL_DIR/requirements.txt" "$TMP_DIR/" || true
  [ -d "$LOCAL_DIR/static" ] && cp -a "$LOCAL_DIR/static" "$TMP_DIR/" || die "缺少 static/ 目录"
  [ -f "$LOCAL_DIR/quantweb.db" ] && cp "$LOCAL_DIR/quantweb.db" "$TMP_DIR/"

  ( cd "$TMP_DIR" && "$LOCAL_PY" -c "import app; print('import ok: app')" ) \
    || { rm -rf "$TMP_DIR"; die "导入失败（依赖缺失？先 pip install -r requirements.txt）"; }
  info "模块导入通过"

  # 3) 冒烟启动：随机空闲端口
  SMOKE_PORT="$("$LOCAL_PY" - <<'PYEOF'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PYEOF
)"
  ( cd "$TMP_DIR" && "$LOCAL_PY" -m uvicorn app:app --host 127.0.0.1 --port "$SMOKE_PORT" --log-level warning ) \
    >"$TMP_DIR/uvicorn.log" 2>&1 &
  SMOKE_PID=$!

  SMOKE_OK=0
  for _ in $(seq 1 40); do
    if curl -fsS -m 2 "http://127.0.0.1:${SMOKE_PORT}/api/health" >/dev/null 2>&1; then SMOKE_OK=1; break; fi
    kill -0 "$SMOKE_PID" 2>/dev/null || break
    sleep 0.5
  done

  if [ "$SMOKE_OK" != "1" ]; then
    err "--- uvicorn 日志尾部 ---"
    tail -n 20 "$TMP_DIR/uvicorn.log" >&2 || true
    kill "$SMOKE_PID" 2>/dev/null || true
    rm -rf "$TMP_DIR"
    die "冒烟启动失败：/api/health 未响应"
  fi

  HEALTH_OUT="$(curl -fsS -m 3 "http://127.0.0.1:${SMOKE_PORT}/api/health")"
  info "冒烟 health：$HEALTH_OUT"

  HTTP_CODE="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:${SMOKE_PORT}/")"
  if [ "$HTTP_CODE" != "200" ]; then
    kill "$SMOKE_PID" 2>/dev/null || true
    rm -rf "$TMP_DIR"
    die "首页返回 $HTTP_CODE（期望 200）"
  fi
  info "首页可访问（200）"

  kill "$SMOKE_PID" 2>/dev/null || true
  wait "$SMOKE_PID" 2>/dev/null || true
  rm -rf "$TMP_DIR"
  info "本地验证全部通过"
}

# ---------------- 生产状态检测 ----------------
remote_wait_health() {
  ssh_run "for i in \$(seq 1 15); do if out=\$(curl -fsS -m 3 http://127.0.0.1:${PORT}/api/health 2>/dev/null); then echo \"\$out\"; exit 0; fi; sleep 2; done; exit 1" 2>/dev/null
}

remote_snapshot() {
  local tag="$1"
  ssh_run "set -e
    mkdir -p ${RELEASE_ROOT}/${tag}
    tar -C ${REMOTE_DIR} --exclude=.venv --exclude=quantweb.db --exclude=__pycache__ --exclude='._*' -cf - . \
      | tar -xf - -C ${RELEASE_ROOT}/${tag}
    echo ${tag}" 2>/dev/null
}

remote_prune() {
  ssh_run "ls -1 ${RELEASE_ROOT} 2>/dev/null | grep -E '^(pre|man)-' | sort | head -n -${KEEP_BACKUPS} | while read d; do rm -rf ${RELEASE_ROOT}/\$d; done" 2>/dev/null || true
}

remote_restore() {
  local tag="$1"
  ssh_run "set -e
    [ -d ${RELEASE_ROOT}/${tag} ] || { echo MISSING; exit 1; }
    tar -C ${RELEASE_ROOT}/${tag} -cf - . | tar -xf - -C ${REMOTE_DIR}
    systemctl restart ${SERVICE}
    echo RESTORED" 2>/dev/null
}

latest_backup() {
  ssh_run "ls -1 ${RELEASE_ROOT} 2>/dev/null | grep -E '^(pre|man)-' | sort | tail -1" 2>/dev/null || true
}

cmd_status() {
  step "生产环境检测：$VPS"

  ssh_run "echo SSH_OK" >/dev/null 2>&1 || die "SSH 连不上 $VPS"
  info "SSH 连通"

  ACTIVE="$(ssh_run "systemctl is-active ${SERVICE}" 2>/dev/null || echo unknown)"
  ENABLED="$(ssh_run "systemctl is-enabled ${SERVICE}" 2>/dev/null || echo unknown)"
  if [ "$ACTIVE" = "active" ]; then
    info "systemd：${SERVICE} = active（开机自启：${ENABLED}）"
  else
    err "systemd：${SERVICE} = ${ACTIVE}"
  fi

  if H="$(remote_wait_health)"; then
    info "health：$H"
  else
    err "health：无响应（服务可能已挂）"
  fi

  VER="$(ssh_run "curl -s -m 3 http://127.0.0.1:${PORT}/ | grep -o '01-core.js?v=[0-9]*' | head -1" 2>/dev/null || true)"
  info "线上静态资源版本：${VER:-未识别}"

  DISK="$(ssh_run "df -h /opt | tail -1 | awk '{print \$4\" 可用 / \"\$5\" 已用\"}'" 2>/dev/null || true)"
  info "磁盘：${DISK:-未知}"

  DB_INFO="$(ssh_run "ls -lh ${REMOTE_DIR}/quantweb.db 2>/dev/null | awk '{print \$5\"  \"\$9}'" 2>/dev/null || true)"
  info "数据库：${DB_INFO:-未找到 quantweb.db}"

  BAKS="$(ssh_run "ls -1 ${RELEASE_ROOT} 2>/dev/null | grep -E '^(pre|man)-' | tail -${KEEP_BACKUPS}" 2>/dev/null || true)"
  if [ -n "$BAKS" ]; then
    info "最近备份："
    echo "$BAKS" | sed 's/^/       /'
  else
    warn "尚无备份（首次 deploy 时自动创建）"
  fi
}

# ---------------- 备份 / 回退 ----------------
cmd_backup() {
  TAG="man-$(date +%Y%m%d-%H%M%S)"
  step "在远端创建快照：${RELEASE_ROOT}/${TAG}"
  OUT="$(remote_snapshot "$TAG")" || die "备份失败"
  [ -n "$OUT" ] || die "备份未产出目录"
  remote_prune
  info "备份完成：$OUT"
}

cmd_backups() {
  OUT="$(ssh_run "ls -1 ${RELEASE_ROOT} 2>/dev/null | grep -E '^(pre|man)-'" 2>/dev/null || true)"
  [ -n "$OUT" ] && echo "$OUT" || warn "远端暂无备份"
}

cmd_rollback() {
  local tag="${1:-}"
  if [ -z "$tag" ]; then
    tag="$(latest_backup)"
    [ -n "$tag" ] || die "远端没有可用备份，无法回退"
    warn "未指定备份，使用最近一份：$tag"
  fi
  step "回退到 $tag"
  OUT="$(remote_restore "$tag")" || die "回退失败：备份 $tag 不存在或解压出错"
  step "等待服务恢复..."
  if H="$(remote_wait_health)"; then
    info "回退完成，health：$H"
  else
    err "回退后服务仍未恢复，日志尾部："
    ssh_run "journalctl -u ${SERVICE} -n 40 --no-pager" 2>/dev/null || true
    die "回退失败，需人工介入"
  fi
}

# ---------------- 数据库备份 ----------------
cmd_db_backup() {
  step "同步 scripts/ 到远端"
  tar_sync "$LOCAL_DIR/scripts" "scripts" || die "脚本同步失败"
  step "执行 quantweb.db 在线备份（sqlite3 .backup + integrity_check）"
  ssh_run "bash ${REMOTE_DIR}/scripts/db_backup.sh" || die "数据库备份失败"
}

cmd_db_backups() {
  OUT="$(ssh_run "ls -lh ${REMOTE_DIR}-dbbackup/db-*.db 2>/dev/null | awk '{print \$5\"  \"\$9}'" 2>/dev/null || true)"
  [ -n "$OUT" ] && echo "$OUT" || warn "远端暂无数据库备份"
}

# ---------------- 部署 ----------------
cmd_deploy() {
  local dry_run=0 skip_verify=0
  for a in "$@"; do
    case "$a" in
      --dry-run) dry_run=1 ;;
      --skip-verify) skip_verify=1 ;;
      *) die "未知参数：$a" ;;
    esac
  done

  pick_python   # show_diff 需要 LOCAL_PY（--skip-verify 时也要有）

  if [ "$skip_verify" = "1" ]; then
    warn "已跳过本地验证（--skip-verify）"
  else
    cmd_verify || die "本地验证未通过，终止部署"
  fi

  echo
  step "生产环境预检"
  ssh_run "echo SSH_OK" >/dev/null 2>&1 || die "SSH 连不上 $VPS"
  info "SSH 连通"
  PRE_ACTIVE="$(ssh_run "systemctl is-active ${SERVICE}" 2>/dev/null || echo unknown)"
  if [ "$PRE_ACTIVE" = "active" ]; then
    info "部署前服务状态：active"
  else
    warn "部署前服务状态：$PRE_ACTIVE（可能是修复性发布）"
  fi

  echo
  step "比对本地与远端差异（md5 清单）"
  show_diff

  if [ "$dry_run" = "1" ]; then
    info "--dry-run 模式：未做任何远端改动，退出"
    return 0
  fi

  echo
  TAG="pre-$(date +%Y%m%d-%H%M%S)"
  step "备份远端当前版本 → ${RELEASE_ROOT}/${TAG}"
  OUT="$(remote_snapshot "$TAG")" || die "备份失败，终止部署（线上未改动）"
  [ -n "$OUT" ] || die "备份未产出目录，终止部署"
  info "已备份：$OUT"

  echo
  step "同步源码到 $REMOTE_DIR"
  tar_sync "$LOCAL_DIR" "." '.venv' 'quantweb.db' '__pycache__' '*.pyc' '.DS_Store' '._*' '.git' \
    || die "同步失败"
  info "同步完成（共 ${DIFF_COUNT:-0} 处差异文件）"

  echo
  step "远端安装/更新依赖（阿里云镜像优先，失败回退官方源）"
  ssh_run "cd ${REMOTE_DIR} && \
    if test -d .venv; then V=.venv/bin/pip; else V=pip3; fi; \
    \$V install -q -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt \
      || \$V install -q -r requirements.txt" \
    || warn "依赖安装返回非 0，继续重启校验"

  echo
  step "重启 ${SERVICE} 并等待健康检查"
  ssh_run "systemctl restart ${SERVICE}" 2>/dev/null || die "重启命令失败"
  if H="$(remote_wait_health)"; then
    info "部署成功，health：$H"
  else
    err "部署后健康检查失败，自动回退到 $TAG"
    ssh_run "journalctl -u ${SERVICE} -n 30 --no-pager" 2>/dev/null || true
    if OUT="$(remote_restore "$TAG")"; then
      step "代码已恢复，等待服务..."
      if H2="$(remote_wait_health)"; then
        info "回退成功，线上恢复部署前版本，health：$H2"
      else
        err "回退后服务仍不可用，请人工介入：ssh -6 $VPS 'journalctl -u ${SERVICE} -n 100'"
      fi
    else
      err "自动回退失败，请人工介入：ssh -6 $VPS 'ls ${RELEASE_ROOT}'"
    fi
    die "本次部署已回退，线上保持原版本"
  fi

  remote_prune
  echo
  VER="$(ssh_run "curl -s -m 3 http://127.0.0.1:${PORT}/ | grep -o '01-core.js?v=[0-9]*' | head -1" 2>/dev/null || true)"
  info "线上静态资源版本：${VER:-未识别}（改了 JS/CSS 请确认 ?v= 已递增并硬刷新）"
}

# ---------------- 入口 ----------------
CMD="${1:-deploy}"
shift || true
case "$CMD" in
  verify)   cmd_verify "$@" ;;
  status)   cmd_status "$@" ;;
  deploy)   cmd_deploy "$@" ;;
  backup)   cmd_backup "$@" ;;
  backups)  cmd_backups "$@" ;;
  db-backup)   cmd_db_backup "$@" ;;
  db-backups)  cmd_db_backups "$@" ;;
  rollback) cmd_rollback "$@" ;;
  -h|--help|help)
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *) die "未知命令：$CMD（可用：verify | status | deploy | backup | backups | rollback）" ;;
esac
