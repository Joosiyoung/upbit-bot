#!/usr/bin/env bash
# claude-remote 세션이 없으면 기동한다 (cron: @reboot + 5분 주기).
# 이미 떠 있으면 아무 것도 하지 않는다(멱등).
set -u
PROJECT_DIR="/home/ubuntu/upbit-bot"
SESSION="claude-remote"
TMUX_BIN="/usr/bin/tmux"

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  exit 0
fi
"$TMUX_BIN" new-session -d -s "$SESSION" -c "$PROJECT_DIR" "cd $PROJECT_DIR && exec claude --remote-control"
echo "$(date '+%Y-%m-%d %H:%M:%S') watchdog: claude-remote 세션 재기동" >> "$PROJECT_DIR/logs/claude-watchdog.log"
