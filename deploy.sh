#!/usr/bin/env bash
# 一键部署 quant-web 到 VPS（本机终端运行；也可由 agent 用 ssh -6 直连执行）
set -e
LOCAL_DIR="/Users/joevan/WorkBuddy/2026-08-08-12-55-23/quant-web"
VPS="root@2404:8c80:82:1057::47"
SSH_OPTS="-6 -o StrictHostKeyChecking=no"
REMOTE_DIR="/opt/quant-web"

echo "==> [1/2] 同步代码到 VPS（保留 .venv 与 quantweb.db）..."
rsync -az -e "ssh $SSH_OPTS" \
  --exclude '.venv' --exclude 'quantweb.db' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  "$LOCAL_DIR/" "$VPS:$REMOTE_DIR/"

echo "==> [2/2] 安装 akshare（若缺失）并重启服务..."
ssh $SSH_OPTS "$VPS" "cd $REMOTE_DIR && \
  (test -d .venv && .venv/bin/pip install -q akshare || pip3 install -q akshare) && \
  systemctl restart quant-web && sleep 3 && \
  echo -n 'health: ' && curl -s localhost:8090/api/health && echo && \
  systemctl is-active quant-web"

echo "==> 完成。浏览器硬刷新（Cmd+Shift+R）即可看到新面板。"
