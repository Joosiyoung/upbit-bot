#!/usr/bin/env bash
# VPS 초기 셋업 스크립트 (Ubuntu 22.04 기준)
#
# 사용법 (코드 업로드 후 프로젝트 루트에서):
#   chmod +x deploy/setup_vps.sh
#   ./deploy/setup_vps.sh
#
# 수행 내용:
#   1. 시스템 패키지 업데이트 + Python venv 설치
#   2. 가상환경 생성 + requirements 설치
#   3. Tailscale 설치 (인증은 수동: sudo tailscale up)
#   4. systemd 서비스 등록 안내 출력

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== [1/4] 시스템 패키지 설치 ==="
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip curl

echo "=== [2/4] Python 가상환경 + 의존성 설치 ==="
if [ ! -d venv ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "=== [3/4] Tailscale 설치 ==="
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "Tailscale 이미 설치됨 — 건너뜀"
fi

echo "=== [4/4] 남은 수동 작업 ==="
cat <<'EOF'

  1) Tailscale 로그인 (브라우저 인증):
       sudo tailscale up
     인증 후 이 서버의 Tailscale IP 확인:
       tailscale ip -4

  2) .env 파일 생성/수정 (프로젝트 루트):
       UPBIT_ACCESS_KEY=...
       UPBIT_SECRET_KEY=...
       DASHBOARD_HOST=<위에서 확인한 100.x.x.x>
       TELEGRAM_BOT_TOKEN=...
       TELEGRAM_CHAT_ID=...

  3) Upbit Open API 관리 페이지에서 이 서버의 "공인 IP"를 허용 IP에 등록:
       curl ifconfig.me   ← 공인 IP 확인

  4) Telegram 연결 테스트:
       ./venv/bin/python scripts/test_telegram.py

  5) systemd 서비스 등록 (upbit-bot.service의 경로/사용자 확인 후):
       sudo cp deploy/upbit-bot.service /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now upbit-bot
       systemctl status upbit-bot

  6) 접속 확인 (Tailscale이 설치된 폰/PC에서):
       http://<100.x.x.x>:5000

EOF
echo "셋업 완료."
