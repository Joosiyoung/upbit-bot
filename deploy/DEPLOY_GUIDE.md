# VPS 배포 가이드 — PC 없이 24시간 봇 운영

로컬 PC를 꺼도 봇이 계속 돌고, 폰/다른 PC에서 대시보드를 보는 구성입니다.

```
Oracle Cloud 무료 VPS (Ubuntu 22.04)
 ├─ 봇 24시간 구동  ← systemd가 자동 재시작 관리
 ├─ Tailscale       ← 폰/PC에서 안전하게 대시보드 접속
 └─ Telegram 알림   ← 매수/매도/리스크 이벤트 폰 푸시
```

---

## 0단계. Telegram 알림 설정 (VPS 없이도 지금 바로 가능)

1. Telegram 앱에서 **@BotFather** 검색 → `/newbot` → 봇 이름 입력 → **토큰** 발급
2. 방금 만든 봇을 검색해 **아무 메시지나 1회 전송** (안 하면 chat not found 오류)
3. **@userinfobot** 에게 아무 메시지 전송 → 내 **chat id** (숫자) 확인
4. 프로젝트 루트 `.env`에 추가:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABCdef...
   TELEGRAM_CHAT_ID=123456789
   ```
5. 연결 테스트:
   ```
   python scripts/test_telegram.py
   ```
   "✅ 연결 테스트" 메시지가 폰에 오면 완료. 이후 매수/매도/리스크 차단 시 자동 알림.

---

## 1단계. Oracle Cloud 무료 VPS 만들기

1. https://cloud.oracle.com 회원가입 (해외결제 가능 카드 필요 — 본인 확인용, 무료 한도 내 과금 없음)
2. 콘솔 → **Compute → Instances → Create Instance**
   - Image: **Ubuntu 22.04**
   - Shape: **Ampere A1 Flex (ARM)** — 4 OCPU / 24GB까지 무료 (재고 부족 시 1~2 OCPU로 줄여서 시도)
   - SSH 키: 새로 생성해 **private key 다운로드** (잃어버리면 접속 불가)
3. 생성 후 **공인 IP** 메모

> ARM 재고가 계속 없으면 AMD VM.Standard.E2.1.Micro (1GB)도 이 봇 구동에는 충분합니다.

## 2단계. SSH 접속 + 코드 업로드

Windows PowerShell에서:

```powershell
# 접속
ssh -i C:\path\to\private_key ubuntu@<공인IP>

# 코드 업로드 (로컬 PowerShell에서, 프로젝트 폴더 기준)
scp -i C:\path\to\private_key -r "D:\Claude Test" ubuntu@<공인IP>:/home/ubuntu/upbit-bot
```

> `.env`도 같이 올라갑니다. `data/`, `logs/`, `__pycache__/`는 올라가도 무방하지만 빼고 싶으면 업로드 전 삭제.

## 3단계. 자동 셋업 스크립트 실행

VPS에서:

```bash
cd /home/ubuntu/upbit-bot
chmod +x deploy/setup_vps.sh
./deploy/setup_vps.sh
```

Python venv + 의존성 + Tailscale 설치까지 자동으로 진행되고, 끝나면 남은 수동 작업 목록이 출력됩니다.

## 4단계. Tailscale 연결

```bash
sudo tailscale up        # 출력된 URL을 브라우저로 열어 로그인 (Google 계정 가능)
tailscale ip -4          # 이 서버의 Tailscale IP (100.x.x.x) 확인
```

폰/PC에도 Tailscale 앱 설치 후 **같은 계정**으로 로그인.

## 5단계. .env 수정

```bash
nano /home/ubuntu/upbit-bot/.env
```

```
UPBIT_ACCESS_KEY=...
UPBIT_SECRET_KEY=...
DASHBOARD_HOST=100.x.x.x      ← 4단계에서 확인한 Tailscale IP
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

> **중요**: `DASHBOARD_HOST`를 Tailscale IP로 지정하면 tailnet 기기에서만 접속 가능.
> **절대 `0.0.0.0`으로 설정하지 말 것** — 대시보드에 인증이 없어 전 세계에 노출됩니다.

## 6단계. Upbit API 허용 IP 등록

Upbit 웹 → 마이페이지 → **Open API 관리** → 사용 중인 키의 허용 IP에 VPS **공인 IP** 추가.

```bash
curl ifconfig.me    # VPS 공인 IP 확인
```

> 기존에 집 IP만 등록돼 있으면 VPS에서 주문이 거부됩니다. 둘 다 등록 가능.

## 7단계. systemd 서비스 등록 (자동 시작/재시작)

```bash
sudo cp deploy/upbit-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now upbit-bot
systemctl status upbit-bot          # active (running) 확인
```

이후 서버 재부팅·봇 크래시 시 자동 재시작. 봇 상태는 `data/bot_state.json`에서 자동 복원.

## 8단계. 접속 확인

Tailscale이 켜진 폰/PC 브라우저에서:

```
http://100.x.x.x:5000
```

---

## 운영 명령어 모음

| 작업 | 명령 |
|------|------|
| 봇 상태 확인 | `systemctl status upbit-bot` |
| 실시간 로그 | `journalctl -u upbit-bot -f` |
| 봇 재시작 | `sudo systemctl restart upbit-bot` |
| 봇 중지 | `sudo systemctl stop upbit-bot` |
| 코드 업데이트 후 | 재업로드(scp) → `sudo systemctl restart upbit-bot` |

## 문제 해결

| 증상 | 원인/해결 |
|------|-----------|
| 주문이 전부 실패 | Upbit 허용 IP에 VPS 공인 IP 미등록 (6단계) |
| 대시보드 접속 불가 | 폰의 Tailscale 앱이 꺼져 있음 / DASHBOARD_HOST 오타 |
| Telegram 알림 안 옴 | `./venv/bin/python scripts/test_telegram.py`로 진단 |
| 서비스 시작 실패 | `journalctl -u upbit-bot -n 50` 로그 확인 (.env 누락이 흔한 원인) |
