# Upbit 자동매매 봇

Flask + pyupbit 기반 코인 자동매매 봇 + KIS API 기반 국내주식 자동매매 봇. 대시보드(웹) + Telegram 원격 제어 + Oracle Cloud VPS 24시간 운영.

---

## 빠른 시작

```bash
pip install -r requirements.txt
python app.py          # 로컬 실행 → http://127.0.0.1:5000
```

백테스트:
```bash
# 코인 백테스트
python scripts/backtest.py --tickers KRW-BTC,KRW-ETH --days 90
python scripts/backtest.py --days 365   # 전체 종목

# 주식 백테스트 (.env에 KIS_APP_KEY 설정 필요)
python scripts/stock_backtest.py --tickers 005930,000660 --days 90
python scripts/stock_backtest.py --days 90   # 전체 유니버스
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
  telegram_bot.py        # Telegram 명령 봇 (코인봇 13개 명령)
  notifier.py            # Telegram 알림 발송
  upbit_client.py        # pyupbit 래퍼
  ai_analysis.py         # Fear & Greed 조회·캐시 워커
  sheets_client.py       # Google Sheets 실시간 거래 적재 (코인·국내주식, 오프라인 버퍼링)
  stock/                 # 국내주식 자동매매 모듈 (KIS API)
    trader.py            # 국내 주식 시뮬 매매 로직 + _stock_market_notifier(장 시작 자동 시작·알림 데몬)
    trading_control.py   # 국내 주식 시작/중지/상태 (Flask ↔ Telegram 공용)
    kis_auth.py          # KIS OAuth2 토큰 관리
    kis_client.py        # KIS API 래퍼 (국내 OHLCV·현재가·거래량순위)
    universe.py          # 국내 매매 대상 종목 풀 (KOSPI 추세섹터 18종목)
scripts/
  backtest.py            # 백테스터 (라이브 룰 동일 재현)
  stock_backtest.py      # 주식 백테스터 (KIS OHLCV → score_signal)
deploy/
  restart-claude.sh      # VPS claude-remote tmux 세션 재시작 스크립트 (100755)
  claude-remote-watchdog.sh  # 세션 미기동 시 자동 복구 (cron @reboot + */5)
data/
  trade_history.jsonl         # 코인 거래 이력 (365일 보존)
  stock_trade_history.jsonl   # 주식 시뮬 거래 이력
  stock_state.json            # 국내 주식 시뮬 상태 (포지션·잔고, 재시작 복원)
  sheets_buffer.jsonl         # Google Sheets 오프라인 버퍼 (네트워크 오류 시 임시 보관)
logs/
  bot.log                # 운영 로그 (35일 보존, 자정 일 단위 회전)
```

---

## 환경변수 (.env)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `UPBIT_ACCESS_KEY` / `SECRET_KEY` | — | 업비트 API 키 |
| `TELEGRAM_BOT_TOKEN` / `CHAT_ID` | — | Telegram 코인 봇 토큰 / 채팅 ID |
| `TELEGRAM_STOCK_BOT_TOKEN` | `` | Telegram 주식 봇 전용 토큰. 미설정 시 코인 봇으로 fallback |
| `DASHBOARD_HOST` | `127.0.0.1` | VPS는 Tailscale IP 지정. **0.0.0.0 설정 시 기동 거부** |
| `DASHBOARD_PORT` | `5000` | 대시보드 포트 |
| `DASHBOARD_TOKEN` | `` (빈 문자열) | 상태 변경 API(`/api/trading/start`, `/stop`, `/api/refresh`) 인증 토큰. 빈 값이면 경고만 출력하고 통과(로컬 개발 편의). **VPS 운영 시 반드시 설정** |
| `TRADE_AMOUNT_KRW` | `100000` | 대시보드 표시용 참고값. 실제 매수금액은 실시간 KRW 잔고 ÷ 빈 슬롯 수로 자동 계산 |
| `GOOGLE_SHEETS_ID` | `` | Google Sheets 스프레드시트 ID. 미설정 시 Sheets 적재 비활성화 |
| `GOOGLE_SHEETS_KEY_FILE` | `warm-alliance-*.json` | Service Account JSON 키 파일 경로. **git 미추적 — VPS에 별도 SCP 전송 필요** |
| `MAX_POSITIONS` | `5` | 최대 동시 보유 종목 수 |
| `EQUAL_WEIGHT_SIZING` | `True` | True: 종목당 금액을 (총자산 ÷ MAX_POSITIONS)로 상한 — 마지막 슬롯에 잔고 전액 몰림 방지 |
| `MAX_LOSS_PERCENT` | `4.0` | 손절 (%) — 2026-06-22 알트 재튜닝(3→4) |
| `TAKE_PROFIT_PERCENT` | `6.0` | 익절 (%) — 2026-06-22 재튜닝(5→6) |
| `TRAILING_START_PCT` | `9999` | 트레일링 비활성 (승자 조기절단 → OFF). 재활성화 시 정상값 복원 |
| `TRAILING_STOP_PCT` | `1.5` | 고점 대비 하락 한도 (%) — 트레일링 OFF로 미사용 |
| `MAX_HOLD_HOURS` | `48` | time-stop (시간) |
| `BUY_SCORE_THRESHOLD` | `12` | 진입 점수 임계치 (backtest 검증값) |
| `MARKET_REGIME_FILTER` | `True` | BTC 하락 추세 시 전 종목 매수 차단 |
| `DAILY_LOSS_LIMIT_PCT` | `5.0` | 일일 손실 한도 초과 시 당일 매수 차단 |
| `MAX_CONSECUTIVE_STOPLOSS` | `3` | 연속 손절 → 전역 쿨다운 |
| `GLOBAL_BUY_COOLDOWN_MIN` | `120` | 연속 손절 후 전역 매수 금지 (분) |
| `FEAR_GREED_GREED_MAX` | `80` | 극단 탐욕 임계값 → 매수 차단 |
| `FEAR_GREED_FEAR_MIN` | `20` | 극단 공포 임계값 → 매수 차단 |

### KIS API (국내 주식)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `KIS_IS_SANDBOX` | `True` | True=모의투자, False=실거래. 이 값에 따라 아래 키 세트가 자동 선택됨 |
| `KIS_APP_KEY_SANDBOX` | — | 모의투자 전용 앱 키 |
| `KIS_APP_SECRET_SANDBOX` | — | 모의투자 앱 시크릿 |
| `KIS_ACCOUNT_NO_SANDBOX` | — | 모의투자 계좌번호 (예: 50012345-01) |
| `KIS_APP_KEY_REAL` | — | 실거래 앱 키 |
| `KIS_APP_SECRET_REAL` | — | 실거래 앱 시크릿 |
| `KIS_ACCOUNT_NO_REAL` | — | 실거래 계좌번호 (예: 50012345-01) |
| `STOCK_MAX_POSITIONS` | `5` | 주식 최대 동시 보유 종목 수 |
| `STOCK_BUY_SCORE_THRESHOLD` | `12` | 주식 진입 점수 임계치 |
| `STOCK_MAX_LOSS_PERCENT` | `5.0` | 주식 손절 (%) — 3년 KOSPI 백테스트 재튜닝(3.0→5.0), ⚠️ 2026-07-02 페이징 버그 수정 전 ~5개월 데이터 기반 — 재검증 필요 |
| `STOCK_TAKE_PROFIT_PERCENT` | `5.0` | 주식 익절 (%) |
| `STOCK_TRAILING_START_PCT` | `9999` | **트레일링 비활성** (도달 불가능 값으로 OFF — 백테스트상 이익 조기 절단 손해) |
| `STOCK_TRAILING_STOP_PCT` | `1.5` | 주식 고점 대비 하락 한도 (%) — 트레일링 OFF로 미사용 |
| `STOCK_MAX_HOLD_DAYS` | `20` | 주식 최대 보유 일수 (영업일 기준) — 재튜닝(5→20), ⚠️ 2026-07-02 페이징 버그 수정 전 ~5개월 데이터 기반 — 재검증 필요 |
| `STOCK_DAILY_LOSS_LIMIT_PCT` | `5.0` | 주식 일일 손실 한도 초과 시 당일 매수 차단 |
| `STOCK_FEE_RATE` | `0.003` | 주식 시뮬 수수료율 (매수+매도+세금 합산) |
| `STOCK_MAX_PRICE` | `200000` | 레거시 고정값. 실제 진입 필터는 `sim_krw / empty_slots`(슬롯당 예산) 기준으로 동적 계산 |
| `STOCK_BUY_CLOSE_TIME` | `15:20` | 신규 매수 마감 시각 (잔여 시간 진입 방지). 시뮬 모드에서는 비활성화 |
| `STOCK_REGIME_FILTER` | `True` | KOSPI EMA 하락 시 전 종목 매수 차단 |
| `STOCK_ENTRY_CHANGE_MAX` | `2.0` | 당일 등락률 상한 (%). 이 값 초과 시 진입 차단 (갭업 추격 방지) |
| `STOCK_ENTRY_CHANGE_MIN` | `-3.0` | 당일 등락률 하한 (%). 이 값 미만 시 진입 차단 (폭락 종목 차단) |
| `STOCK_ADD_BUY_ENABLED` | `True` | 추가매수 기능 활성화 여부 |
| `STOCK_ADD_BUY_MIN_PROFIT` | `0.0` | 추가매수 허용 최소 수익률 (%). 손실 중 물타기 방지 |
| `STOCK_ADD_BUY_MAX_PROFIT` | `2.0` | 추가매수 허용 최대 수익률 (%). 고점 추격매수 방지 |
| `STOCK_MAX_SLOTS_PER_TICKER` | `2` | 종목당 최대 슬롯 수 (추가매수 포함 상한) |
| `STOCK_SIM_BUDGET` | `5000000` | 주식 시뮬 초기 예산(원). `/start_sim` 금액 미입력 시 이 값 사용 — 2026-06-29 100만→500만원 상향 |

> **미국주식 모듈 제거됨 (2026-06-17)**: 3년 백테스트 결과 미국 일봉 스윙은 환전 스프레드 포함 왕복 2.5% 수수료로 72조합 전부 손실 → 모듈·`US_*` 파라미터·미국 Telegram 명령 전면 삭제하고 국내 전용으로 롤백. 같은 신호가 국내(왕복 0.6%)에선 흑자.

> **VPS 배포 후**: VPS `.env`는 git 미추적이므로 `KIS_APP_KEY_SANDBOX`, `KIS_APP_SECRET_SANDBOX`, `KIS_ACCOUNT_NO_SANDBOX` (또는 REAL 세트) 6개 변수를 VPS에서 직접 추가해야 KIS 기능이 활성화됩니다.

---

## 매매 로직 요약

**매수 차단 게이트 (순서)**
1. 일일 손실 한도 / 연속 손절 쿨다운
2. 시장 레짐 필터 (BTC 단기EMA < 중기EMA) — 시뮬 모드에서는 바이패스
3. F&G 극단값 (≥80 또는 ≤20) — 시뮬 모드에서는 바이패스
4. 시장 캐시 노후 (>180초)
5. 스테이블코인(`config.STABLE_COINS`) 또는 개인 보유분(`PERSONAL_HOLDINGS_BLACKLIST`: XRP·CRO·RVN) — 신규/추가 매수 시 필터링 (`TRADING_BLACKLIST` = 두 집합의 합집합, 총 11종)
6. 진입 점수 < `BUY_SCORE_THRESHOLD` (12)

> **청산은 별도 규칙**: `TRADING_BLACKLIST`는 매수 차단에는 11종 전체가 적용되지만, 청산(Phase 1)에서는 개인 보유분(XRP·CRO·RVN)만 무조건 제외되고 스테이블코인은 `bot_bought=True`(봇이 실수로 매수한 경우)면 청산이 허용된다. 봇이 스테이블코인을 사도 영원히 팔지 못하는 걸 막기 위함 (2026-07-02, USDT 3,388만원 영구 동결 버그 수정).

**주식 매수 차단 게이트 (순서)**
1. 장 외 시간 — `is_market_hours()` False 시 워커 비활성
2. 매수 마감 시각(`STOCK_BUY_CLOSE_TIME`) 초과 — 시뮬 모드에서는 비활성
3. 슬롯당 예산(`sim_krw / empty_slots`) 대비 종목 가격 초과
4. 진입 점수 × 2.5 < `STOCK_BUY_SCORE_THRESHOLD` (12)
5. 당일 매도 이력 — `_stock_sold_today`에 기록된 종목 당일 재진입 차단
6. 당일 등락률 범위 초과 — `STOCK_ENTRY_CHANGE_MIN`(-3%) ~ `STOCK_ENTRY_CHANGE_MAX`(+2%) 벗어난 종목 차단

**청산 우선순위 (Phase 1)**
상장폐지 → 익절 → 손절 → 트레일링 스탑 → 매도 신호 → time-stop

**점수 공식** (진입 스캔·보유 평가 동일)
```
진입 스캔:  total_score = 일봉×2.5 + 1시간봉×2 + 1분봉×0.5
보유 평가:  total_score = 일봉×2.5 + 4시간봉×2 + 1시간봉×0.5
```
추세 정렬형 룰 (2026-06-13 재설계): BTC 상승 추세 + 모멘텀 전환에 가점, 비추세 과매도 매수 차단.
일봉·1시간봉은 완성봉(최신봉 제외) 기준으로 신호 산출 — 진행 중 봉의 리페인팅 방지.

**백테스트 결과 (365일, Buy&Hold -39%)**

| 구성 | 거래당 기대값 | 승률 |
|------|------|------|
| 구 룰 (역추세) | -0.41% | 50.4% |
| **재설계 + 레짐필터 (th=12)** | **+0.15%** | 48.2% |

---

## Telegram 명령

### 코인 봇 (`TELEGRAM_BOT_TOKEN`)

| 명령 | 동작 |
|------|------|
| `/status` | 상태·잔고·포지션 요약 |
| `/perf` | 누적 성과 조회 (승률·손익, 시뮬/실거래 분리) |
| `/positions` | 보유 포지션 상세 (진입가·수익률·보유시간) |
| `/risk` | 리스크 상태 (일일 손익·연속손절·매수 차단 여부) |
| `/history [n]` | 최근 n건 거래 이력 (기본 10건) |
| `/params` | 현재 적용 파라미터 조회 |
| `/start_sim [금액]` | 시뮬레이션 시작 |
| `/start_live` | 실거래 시작 (/confirm 필요) |
| `/stop` | 매매 중지 (보유 유지) |
| `/liquidate` | 중지 + 전액 청산 (/confirm 필요) |
| `/restart_claude` | VPS claude-remote tmux 세션 재시작 (/confirm 필요, 본인 chat_id만) |
| `/help` | 명령어 목록 |
| (자동) 매일 09:00 KST | 전일 09:00 ~ 당일 08:59 거래 통계 자동 전송 (시뮬/실거래 분리) |

### 주식 봇 (`TELEGRAM_STOCK_BOT_TOKEN`) — 국내주식 전용

| 명령 | 동작 |
|------|------|
| `/start_sim [금액]` | 주식 시뮬 시작. 금액(원화) 미입력 시 `STOCK_SIM_BUDGET` 기본값(500만원) 사용. 장 외 시간이면 거부. **09:00 장 시작 시 자동 호출되므로 수동 실행 불필요** |
| `/stop` | 주식 시뮬 중지 (보유 포지션 유지) |
| `/status` | 시뮬 상태·잔고·포지션 요약 |
| `/perf` | 누적 성과 조회 (승률·평균수익률) |
| `/positions` | 보유 포지션 상세 |
| `/history [n]` | 최근 n건 거래 이력 (기본 10건) |
| `/params` | 현재 적용 주식 파라미터 조회 |
| `/market` | 국내 장 현황 (개장/폐장 여부) |
| `/help` | 명령어 목록 |
| (자동) 매일 09:00 KST | 국내 장 시작 Telegram 알림 + 주식 시뮬 자동 시작 (`_stock_market_notifier`, 시뮬 여부 무관하게 항상 동작) |

---

## VPS 인프라

- Oracle Cloud ap-osaka-1, VM.Standard.E2.1.Micro, Ubuntu 22.04
- `upbit-bot.service` (systemd, 자동 재시작)
- 대시보드는 Tailscale 경유 접속 (공인 IP 직접 노출 없음)
- 배포: `git push` → VPS에서 `git pull && systemctl restart`
- Claude Code Remote Control: `claude-remote` tmux 세션, `deploy/claude-remote-watchdog.sh` cron으로 5분 주기 자동복구. 폰 Telegram `/restart_claude` 명령으로 세션 재시작 가능

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
