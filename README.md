# Upbit 자동매매 봇

Flask + pyupbit 기반 코인 자동매매 봇. 대시보드(웹) + Telegram 원격 제어 + Oracle Cloud VPS 24시간 운영.

---

## 빠른 시작

```bash
pip install -r requirements.txt
python app.py          # 로컬 실행 → http://127.0.0.1:5000
```

VPS 배포:
```bash
git push origin main
ssh -i "~/.ssh/upbit-vps-key" ubuntu@<VPS_IP> \
  "cd /home/ubuntu/upbit-bot && git pull && sudo systemctl restart upbit-bot"
```

---

## 구조

```
app.py                   # Flask 진입점 + 백그라운드 워커 기동
core/
  config.py              # .env 로드, 전역 상수, KST 타임존
  trader.py              # 매매 핵심 로직 (진입·청산·리스크·상태 저장)
  trading_control.py     # 시작/중지/상태 (Flask ↔ Telegram 공용)
  data_builder.py        # 시장 분석 데이터 빌드·캐시
  analysis.py            # 진입 점수 계산 (score_signal)
  indicators.py          # RSI, MACD, 볼린저, EMA
  telegram_bot.py        # Telegram 명령 봇
  notifier.py            # Telegram 알림 발송
  upbit_client.py        # pyupbit 래퍼
  ai_analysis.py         # Fear & Greed 조회·캐시 워커
scripts/
  backtest.py            # 백테스터 (라이브 룰 동일 재현)
data/
  trade_history.jsonl    # 거래 이력 (365일 보존)
logs/
  bot.log                # 운영 로그 (35일 보존, 자정 일 단위 회전)
```

---

## 환경변수 (.env)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `UPBIT_ACCESS_KEY` / `SECRET_KEY` | — | 업비트 API 키 |
| `TELEGRAM_BOT_TOKEN` / `CHAT_ID` | — | Telegram 봇 설정 |
| `DASHBOARD_HOST` | `127.0.0.1` | VPS는 Tailscale IP 지정. **0.0.0.0 설정 시 기동 거부** |
| `DASHBOARD_PORT` | `5000` | 대시보드 포트 |
| `DASHBOARD_TOKEN` | `` (빈 문자열) | 상태 변경 API(`/api/trading/start`, `/stop`, `/api/refresh`) 인증 토큰. 빈 값이면 경고만 출력하고 통과(로컬 개발 편의). **VPS 운영 시 반드시 설정** |
| `TRADE_AMOUNT_KRW` | `100000` | 대시보드 표시용 참고값. 실제 매수금액은 실시간 KRW 잔고 ÷ 빈 슬롯 수로 자동 계산 |
| `MAX_POSITIONS` | `5` | 최대 동시 보유 종목 수 |
| `EQUAL_WEIGHT_SIZING` | `True` | True: 종목당 금액을 (총자산 ÷ MAX_POSITIONS)로 상한 — 마지막 슬롯에 잔고 전액 몰림 방지 |
| `MAX_LOSS_PERCENT` | `3.0` | 손절 (%) |
| `TAKE_PROFIT_PERCENT` | `5.0` | 익절 (%) |
| `TRAILING_START_PCT` | `3.0` | 트레일링 스탑 활성화 수익률 (%) |
| `TRAILING_STOP_PCT` | `1.5` | 고점 대비 하락 한도 (%) |
| `MAX_HOLD_HOURS` | `48` | time-stop (시간) |
| `BUY_SCORE_THRESHOLD` | `12` | 진입 점수 임계치 (backtest 검증값) |
| `MARKET_REGIME_FILTER` | `True` | BTC 하락 추세 시 전 종목 매수 차단 |
| `DAILY_LOSS_LIMIT_PCT` | `5.0` | 일일 손실 한도 초과 시 당일 매수 차단 |
| `MAX_CONSECUTIVE_STOPLOSS` | `3` | 연속 손절 → 전역 쿨다운 |
| `GLOBAL_BUY_COOLDOWN_MIN` | `120` | 연속 손절 후 전역 매수 금지 (분) |
| `FEAR_GREED_GREED_MAX` | `80` | 극단 탐욕 임계값 → 매수 차단 |
| `FEAR_GREED_FEAR_MIN` | `20` | 극단 공포 임계값 → 매수 차단 |

---

## 매매 로직 요약

**매수 차단 게이트 (순서)**
1. 일일 손실 한도 / 연속 손절 쿨다운
2. 시장 레짐 필터 (BTC 단기EMA < 중기EMA) — 시뮬 모드에서는 바이패스
3. F&G 극단값 (≥80 또는 ≤20) — 시뮬 모드에서는 바이패스
4. 시장 캐시 노후 (>180초)
5. 진입 점수 < `BUY_SCORE_THRESHOLD` (12)

**청산 우선순위 (Phase 1)**
상장폐지 → 익절 → 손절 → 트레일링 스탑 → 매도 신호 → time-stop

**점수 공식**
```
total_score = 일봉×2.5 + 1시간봉×2 + 1분봉×0.5
```
추세 정렬형 룰 (2026-06-13 재설계): BTC 상승 추세 + 모멘텀 전환에 가점, 비추세 과매도 매수 차단.

**백테스트 결과 (365일, Buy&Hold -39%)**

| 구성 | 거래당 기대값 | 승률 |
|------|------|------|
| 구 룰 (역추세) | -0.41% | 50.4% |
| **재설계 + 레짐필터 (th=12)** | **+0.15%** | 48.2% |

---

## Telegram 명령

| 명령 | 동작 |
|------|------|
| `/status` | 상태·잔고·포지션 요약 |
| `/start_sim [금액]` | 시뮬레이션 시작 |
| `/start_live` | 실거래 시작 (/confirm 필요) |
| `/stop` | 매매 중지 (보유 유지) |
| `/liquidate` | 중지 + 전액 청산 (/confirm 필요) |
| `/help` | 명령어 목록 |

---

## VPS 인프라

- Oracle Cloud ap-osaka-1, VM.Standard.E2.1.Micro, Ubuntu 22.04
- `upbit-bot.service` (systemd, 자동 재시작)
- 대시보드는 Tailscale 경유 접속 (공인 IP 직접 노출 없음)
- 배포: `git push` → VPS에서 `git pull && systemctl restart`

상세 배포 가이드: [deploy/DEPLOY_GUIDE.md](deploy/DEPLOY_GUIDE.md)

---

## 보안

| 항목 | 내용 |
|------|------|
| **0.0.0.0 바인딩 금지** | `DASHBOARD_HOST=0.0.0.0` 설정 시 `app.py` 기동을 `sys.exit`으로 즉시 거부 |
| **API 토큰 인증** | `DASHBOARD_TOKEN` 설정 시 상태 변경 엔드포인트에 `X-Dashboard-Token` 헤더 또는 `?token=` 파라미터 필수. 읽기 전용(`/`, `/api/status`, `/api/analysis`, `/api/trades`)은 인증 불필요 |
| **CSRF 완화** | 상태 변경 엔드포인트는 `Content-Type: application/json` 요청만 수락 (그 외 415 반환). 커스텀 헤더 기반 인증은 브라우저 cross-origin에서 자동 차단됨 |
| **외부 API sanity check** | F&G 값 0~100 범위 검증, 업비트 마켓 목록 20개 미만 비정상 시 캐시 유지, 체결 trades price/volume 양수 검증 |
| **상태 파일 스키마 검증** | 재시작 시 `bot_state.json`의 포지션마다 `entry_price > 0`, `quantity > 0`, `1 ≤ slot_count ≤ 10` 검증. 실패 포지션은 폐기 후 WARNING 로그 |
| **shell=True 제거** | `scripts/restart_helper.py`의 `taskkill`, `netstat` 호출을 리스트 인자 방식으로 전환 (command injection 방지) |
