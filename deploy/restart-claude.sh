#!/usr/bin/env bash
# claude-remote tmux 세션을 프로젝트 디렉터리에서 깨끗이 재시작한다.
# - 봇(/restart_claude) 및 SSH 수동 실행 양쪽에서 호출 가능
# - 기존 세션이 없어도 정상 동작(새로 생성)
set -u
PROJECT_DIR="/home/ubuntu/upbit-bot"
SESSION="claude-remote"
TMUX_BIN="/usr/bin/tmux"

"$TMUX_BIN" kill-session -t "$SESSION" 2>/dev/null || true
sleep 1
"$TMUX_BIN" new-session -d -s "$SESSION" -c "$PROJECT_DIR" "cd $PROJECT_DIR && unset ANTHROPIC_API_KEY && exec claude --remote-control"
echo "claude-remote 세션 재시작 완료 ($(date '+%Y-%m-%d %H:%M:%S'))"
