# Upbit 자동 매매 대시보드 — 인수인계 문서

## 목차
1. [시스템 개요](#1-시스템-개요)
2. [파일 구조](#2-파일-구조)
3. [환경 설정](#3-환경-설정-env)
4. [서버 실행](#4-서버-실행)
5. [기술적 지표 계산](#5-기술적-지표-계산)
6. [점수 산정 로직](#6-점수-산정-로직)
7. [자동매매 전체 흐름](#7-자동매매-전체-흐름)
8. [매수 조건 상세](#8-매수-조건-상세)
9. [추가매수 조건 상세](#9-추가매수-조건-상세)
10. [매도 조건 상세](#10-매도-조건-상세)
11. [슬롯 시스템](#11-슬롯-시스템)
12. [AI 분석 로직](#12-ai-분석-로직)
13. [Telegram 알림 · 원격 운영](#13-telegram-알림--원격-운영)
14. [하드코딩 값 목록](#14-하드코딩-값-목록)
15. [코드 최적화 이력](#15-코드-최적화-이력)
16. [주의사항](#16-주의사항)

---

## 1. 시스템 개요

Flask 기반 웹 대시보드 + 백그라운드 자동매매 봇.
업비트 KRW 마켓에서 기술적 지표 점수를 기반으로 자동 매수/매도를 실행한다.

```
브라우저 (http://127.0.0.1:5000)
    ↕ REST API
Flask 서버 (app.py)
    ├── cache_worker      — 보유 코인 분석 (30초 주기)
    ├── ai_worker         — Fear & Greed 갱신 (1시간) + 업비트 마켓 체크 (5분)
    └── auto_trade_worker — 자동매매 사이클 (30초 주기)
```

**운영 모드 2가지**
- **시뮬레이션**: 실제 주문 없이 현재 KRW 잔고를 가상 예산으로 사용
- **실거래**: 업비트 API로 실제 시장가 주문 실행

---

## 2. 파일 구조

```
├── app.py                — Flask 진입점 (라우트, 워커 시작, 매매 시작/중지 API)
├── core/                 — 매매 엔진·분석 모듈 (파이썬 패키지)
│   ├── trader.py         — 자동매매 상태·로직·워커 (핵심 엔진)
│   ├── data_builder.py   — 보유 코인 분석 캐시, 시장 코인 분석 캐시
│   ├── analysis.py       — 지표 → 점수 변환, 라벨 함수
│   ├── indicators.py     — RSI/MACD/볼린저밴드/EMA/스토캐스틱 계산
│   ├── ai_analysis.py    — Fear & Greed Index 조회 + 업비트 활성 마켓 체크
│   ├── upbit_client.py   — 업비트 API 래퍼 (OHLCV, 현재가, 잔고, 주문, 체결 조회)
│   ├── notifier.py       — Telegram 알림 (매수/매도/리스크 이벤트, 백그라운드 큐 전송)
│   ├── telegram_bot.py   — Telegram 명령 봇 (/status /start_sim /stop 등 원격 제어)
│   ├── trading_control.py — 매매 시작/중지/상태 공통 로직 (대시보드·Telegram 공유)
│   └── config.py         — .env 값 로드 및 기술적 지표 파라미터 상수
├── web/                  — 대시보드 UI
│   ├── templates/index.html
│   └── static/           — app.js (폴링·UI 업데이트), style.css
├── scripts/              — 보조 스크립트
│   ├── start_server.py   — 서버 시작 (포트 5000 중복 프로세스 정리 후 기동)
│   ├── restart_helper.py — restart.bat에서 호출하는 프로세스 관리 헬퍼
│   ├── backtest.py       — 백테스터 (라이브 점수·청산 룰 재현, 승률·기대값·MDD 산출)
│   └── test_telegram.py  — Telegram 알림 연결 테스트
├── deploy/               — VPS 24시간 운영 배포 파일
│   ├── DEPLOY_GUIDE.md       — Oracle Cloud + Tailscale + systemd 배포 가이드
│   ├── setup_vps.sh          — VPS 초기 셋업 자동화 스크립트 (Ubuntu 22.04)
│   └── upbit-bot.service     — systemd 서비스 유닛 (자동 시작/재시작)
├── data/                 — (자동 생성) 봇 런타임 데이터
│   ├── bot_state.json        — 포지션·쿨다운·리스크 상태 영속화
│   └── trade_history.jsonl   — 전체 거래 이력 (append-only, hold 제외)
├── logs/                 — (자동 생성) 로그
│   ├── bot.log               — 회전 로그 (1MB × 3)
│   └── server.log            — restart.bat 기동 시 stdout/stderr 캡처
├── restart.bat           — 서버 재시작 (더블클릭 진입점, Windows)
├── requirements.txt      — Python 패키지 목록
└── .env                  — API 키 및 운영 파라미터 (Git 제외 필수)
```

---

## 3. 환경 설정 (.env)

`.env` 파일에 아래 값을 설정한다. API 키는 절대 외부에 노출하지 말 것.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `UPBIT_ACCESS_KEY` | — | 업비트 Access Key |
| `UPBIT_SECRET_KEY` | — | 업비트 Secret Key |
| `ANTHROPIC_API_KEY` | — | (미사용) Claude AI API Key — Haiku 분석 제거로 불필요 |
| `LIVE_TRADING` | `False` | `True`로 설정 시 서버 시작 시 실거래 기본값 |
| `MAX_POSITIONS` | `5` | 최대 동시 보유 종목 수 (슬롯 수) |
| `MIN_ORDER_KRW` | `5000` | 업비트 최소 주문금액 (원) |
| `SELL_COOLDOWN_MIN` | `30` | 매도 후 동일 코인 재매수 금지 시간 (분) |
| `MAX_LOSS_PERCENT` | `3.0` | 손절 기준 (%) |
| `TAKE_PROFIT_PERCENT` | `5.0` | 익절 기준 (%) |
| `TRADE_AMOUNT_KRW` | `100000` | 표시용 1회 매수 금액 (실제 매수는 KRW잔고/슬롯 수로 계산) |
| `DAILY_LOSS_LIMIT_PCT` | `5.0` | 일일 실현손실 한도 (당일 시작 자산 대비 %) — 초과 시 당일 신규 매수 차단 |
| `MAX_CONSECUTIVE_STOPLOSS` | `3` | 연속 손절 허용 횟수 — 초과 시 전역 매수 쿨다운 |
| `GLOBAL_BUY_COOLDOWN_MIN` | `120` | 연속 손절 후 전역 매수 금지 시간 (분) |
| `MAX_HOLD_HOURS` | `48` | 최대 보유 시간 (시간) — 초과 시 청산 (time-stop) |
| `MARKET_STALE_SEC` | `180` | 시장 캐시 신선도 한계 (초) — 초과 시 신규 매수 보류 |
| `TRAILING_START_PCT` | `3.0` | 트레일링 스탑 활성화 수익률 (%) |
| `TRAILING_STOP_PCT` | `1.5` | 고점 대비 하락 허용폭 (%) — 초과 시 청산 |
| `ADD_BUY_MIN_PROFIT` | `0.0` | 추가매수 허용 최소 수익률 (%) — 손실 중 물타기 방지 |
| `ADD_BUY_MAX_PROFIT` | `3.0` | 추가매수 허용 최대 수익률 (%) |
| `MAX_SLOTS_PER_TICKER` | `2` | 한 종목이 점유 가능한 최대 슬롯 수 |
| `BUY_FAIL_LIMIT` | `3` | 연속 매수 실패 허용 횟수 |
| `BUY_FAIL_COOLDOWN_MIN` | `30` | 매수 실패 한도 초과 시 해당 종목 매수 금지 시간 (분) |
| `BUY_CONFIRM_TICKS` | `1` | 신규 매수 진입에 필요한 연속 사이클 수(디바운스). 1=즉시. 2+면 score≥8이 연속 유지돼야 진입 → 미완성 캔들 스파이크 진입 방지 |
| `EQUAL_WEIGHT_SIZING` | `True` | `True`면 코인당 매수금액을 (총자산/MAX_POSITIONS)로 제한해 단일 코인 집중 방지. `False`면 종전 방식(잔고/빈슬롯) |
| `ADD_BUY_ENABLED` | `True` | 추가매수(Phase 2.5) 활성화 스위치. `False`면 추가매수 전면 비활성 |
| `FEAR_GREED_GREED_MAX` | `80` | F&G 극단 탐욕 임계값 (이상이면 신규 매수 차단) |
| `FEAR_GREED_FEAR_MIN` | `20` | F&G 극단 공포 임계값 (이하면 신규 매수 차단) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram 봇 토큰 (@BotFather 발급) — 미설정 시 알림 비활성 |
| `TELEGRAM_CHAT_ID` | — | Telegram 채팅 ID (@userinfobot 확인) |
| `DASHBOARD_HOST` | `127.0.0.1` | 대시보드 바인딩 주소. VPS에서는 Tailscale IP(100.x.x.x) 지정. **0.0.0.0 금지** (인증 없음) |
| `DASHBOARD_PORT` | `5000` | 대시보드 포트 |

> **업비트 API 권한**: 자산조회 + 주문하기 권한 필요

---

## 4. 서버 실행

### 최초 설치
```bash
pip install -r requirements.txt
```

### 실행
```bash
# 방법 1: 직접 실행
python app.py

# 방법 2: 시작 스크립트 (포트 5000 중복 프로세스 정리 후 기동)
python scripts/start_server.py

# 방법 3: restart.bat 더블클릭 (Windows)
# → 기존 프로세스 종료 후 재시작, server.log 초기화
```

접속 주소: `http://127.0.0.1:5000`

### restart.bat 동작
1. 5000 포트 점유 프로세스 강제 종료 (Python으로 PID 탐색 후 taskkill)
2. server.log 초기화
3. 서버 재시작 후 HTTP 상태 확인

---

## 5. 기술적 지표 계산

`indicators.py`에서 계산, `config.py`의 파라미터 사용.

| 지표 | 파라미터 | 설명 |
|------|----------|------|
| RSI | period=14 | 과매도(< 30) / 과매수(> 70) |
| MACD | fast=12, slow=26, signal=9 | 골든/데드크로스, 모멘텀 |
| 볼린저 밴드 | period=20, std=2.0 | 밴드 내 위치(bb_pct: 0~1) |
| EMA | short=9, mid=21, long=50 | 정배열/역배열 추세 판단 |
| 스토캐스틱 | k=14, d=3 | 보조 과매수/과매도 |
| 거래량 비율 | period=20 | 현재 거래량 / 20봉 평균 거래량 |

---

## 6. 점수 산정 로직

### 타임프레임별 점수 (`analysis.py` → `score_signal()`)

각 지표가 점수에 기여하는 방식:

| 지표 | 조건 | 점수 |
|------|------|------|
| RSI | < 25 | +3 |
| RSI | < 35 | +2 |
| RSI | < 45 | +1 |
| RSI | > 75 | -3 |
| RSI | > 65 | -2 |
| RSI | > 55 | -1 |
| MACD | 히스토그램 음→양 전환 (골든크로스) | +3 |
| MACD | 히스토그램 양→음 전환 (데드크로스) | -3 |
| MACD | 히스토그램 양수이며 증가 | +1 |
| MACD | 히스토그램 음수이며 감소(더 음수) | -1 |
| MACD | 히스토그램 음수이며 증가(회복) | +1 |
| 볼린저밴드 | bb_pct ≤ 0 (하단 이탈) | +3 |
| 볼린저밴드 | bb_pct ≤ 0.2 (하단 근접) | +1 |
| 볼린저밴드 | bb_pct ≥ 1.0 (상단 이탈) | -3 |
| 볼린저밴드 | bb_pct ≥ 0.8 (상단 근접) | -1 |
| EMA | 정배열 (short > mid > long) | +1 |
| EMA | 역배열 (short < mid < long) | -1 |
| 스토캐스틱 | K < 20 | +1 |
| 스토캐스틱 | K > 80 | -1 |
| 거래량 | ≥ 2.0배 & 점수 양수 | +1 |
| 거래량 | ≥ 2.0배 & 점수 음수 | -1 |

타임프레임 1개당 최대 점수: **+12 / 최소 -10**

### 종합 점수 공식 (`data_builder.py`)

```
total_score = 일봉 점수 × 2.5 + 1시간봉 점수 × 2 + 1분봉 점수 × 0.5
```

> 일봉 가중치가 가장 높아 단기 노이즈 필터링 효과

### 점수 → 매매 신호 변환 (`analysis.py` → `action_from_score()`)

| total_score | 신호 텍스트 | action_class | 봇 자동매수 |
|-------------|-------------|--------------|-------------|
| ≥ 8 | 강한 매수 | buy-strong | ✅ 실행 |
| 5 ~ 7 | 매수 준비 | buy-watch | ❌ 대기 |
| 3 ~ 4 | 매수 관심 | buy-watch | ❌ 대기 |
| 1 ~ 2 | 매수 우호 | buy-mild | ❌ 대기 |
| -4 ~ 0 | 관망 | hold | — |
| -5 이하 | 강한 매도 | sell-strong | 보유 시 매도 |
| -4 ~ -3 | 매도 관심 | sell-watch | — |
| -2 ~ -1 | 매도 우호 | sell-mild | — |

---

## 7. 자동매매 전체 흐름

`auto_trade_worker` (30초 주기):

```
① build_market_data() — 거래량 상위 20개 코인 지표 갱신
        ↓
② run_auto_trade() — 활성화된 경우만 실행
        ↓
   [실거래] _sync_live_positions() — 업비트 실제 잔고 ↔ 포지션 동기화
        ↓                              (가격조회 실패 코인 보존, dust 제외)
   일일 리스크 카운터 날짜 롤오버 (당일 시작 자산 기준점 기록)
        ↓
   Phase 1: 보유 포지션 청산 체크 — 손절 / 익절 / 트레일링 스탑 /
            매도신호 / time-stop / 상장폐지 (실시간 가격 우선 사용)
        ↓
   Phase 2: 신규 매수 — 차단 게이트 통과 시에만
            (일일 손실 한도 · 연속 손절 쿨다운 · F&G 양극단 · 시장 캐시 신선도)
        ↓
   Phase 2.5: 추가매수 (마지막 매수 1시간 경과 + 수익 구간 + 슬롯 상한 내)
```

**상태 영속화 / 재시작 자동 재개**
- 포지션·쿨다운·리스크 카운터·거래 로그는 변경 시마다 `bot_state.json`에 저장된다.
- 서버 재시작 시 자동 복원되며, **매매가 켜진 채 재시작됐으면 자동으로 매매를 재개**한다 (이전에는 재시작 시 봇이 조용히 꺼져 포지션이 무방비 상태였음).
- 모든 체결 이력(관망 제외)은 `trade_history.jsonl`에 append 방식으로 영구 기록된다.

**stop/start 레이스 방지 (epoch)**
- 시작/중지 때마다 `epoch` 카운터가 증가하고, 진행 중인 매매 사이클은 주문 직전·상태 기록 직전마다 epoch을 재확인한다. 중지 후 낡은 사이클이 매수를 계속하거나 이중 매도하는 경합을 차단한다.

---

## 8. 매수 조건 상세

**Phase 2** — 신규 종목 매수 (`trader.py`)

**전역 차단 게이트** (하나라도 걸리면 신규 매수 전체 보류):

| 게이트 | 조건 |
|------|------|
| 일일 손실 한도 | 당일 실현손실 ≥ 당일 시작 자산 × DAILY_LOSS_LIMIT_PCT |
| 연속 손절 보호 | 손절 MAX_CONSECUTIVE_STOPLOSS회 연속 → GLOBAL_BUY_COOLDOWN_MIN분 차단 |
| F&G 극단 탐욕 | F&G ≥ 80 (고점 추격 방지) |
| F&G 극단 공포 | F&G ≤ 20 (폭락장 낙폭 매수 방지) |
| 시장 캐시 노후 | 마켓 캐시 나이 > MARKET_STALE_SEC (오래된 신호로 매매 방지) |

종목별 조건 — 아래를 **모두** 충족해야 매수:

1. `action_class == "buy-strong"` (total_score ≥ 8)
2. `total_score ≥ 8`
3. 현재 미보유 종목
4. TRADING_BLACKLIST 미포함
5. 매도 쿨다운 미적용 (마지막 매도 후 30분 경과)
6. 매수 실패 백오프 미적용 (연속 3회 실패 시 30분 금지)
7. 빈 슬롯 존재 (`available_slots > 0`)
8. 1코인당 매수금액 ≥ MIN_ORDER_KRW (5,000원)

**체결 검증 (실거래):**
주문 접수 후 uuid로 `GET /v1/order`를 폴링해 **실제 체결 수량·평균 체결가**를 포지션에 기록한다 (업비트 시장가 주문의 즉시 응답은 `executed_volume=0`이므로 종전 방식은 추정치였음). 조회 실패 시에만 현재가 기반 추정치로 폴백.

**매수금액 계산:**
```
available_slots = MAX_POSITIONS - 사용중인_슬롯_수
per_coin = KRW잔고 / available_slots   (슬롯이 1개면 잔고 전액)
실제주문금액 = per_coin × 0.999        (0.1% 버퍼: 수수료 0.05% + 여유)
```

조건을 넘는 후보가 여러 개면 **점수 높은 순서**로 빈 슬롯 수만큼 한 번에 매수.

**분석 대상 코인:**
- 업비트 KRW 마켓 **24시간 거래대금 상위 20개**
- TRADING_BLACKLIST 코인 제외

---

## 9. 추가매수 조건 상세

**Phase 2.5** — 기존 보유 종목 추가매수 (`trader.py`)

이미 보유 중인 종목에 대해 아래 조건을 **모두** 충족하면 추가매수 (Phase 2의 전역 차단 게이트도 동일 적용):

1. **마지막 매수**(최초 진입 또는 직전 추가매수, `last_add_ts`) 후 **1시간 경과** — 종전에는 `entry_ts` 기준이라 1시간 후부터 30초마다 무한 추가매수되는 버그가 있었음
2. `total_score ≥ 8` (강한 매수 신호 유지)
3. 현재 수익률 **0% ~ +3% 사이** (`ADD_BUY_MIN_PROFIT` ~ `ADD_BUY_MAX_PROFIT`) — **손실 중 물타기 금지**
4. TRADING_BLACKLIST 미포함
5. 빈 슬롯 존재
6. 해당 종목 `slot_count < MAX_SLOTS_PER_TICKER` (기본 2) — 한 종목 자본 쏠림 방지

**추가매수 후 처리:**
- 해당 포지션의 `slot_count` +1, `last_add_ts` 갱신 (다음 추가매수는 다시 1시간 후)
- `entry_price` = (기존투자금 + 추가투자금) / (기존수량 + 추가수량) — **가중평균 단가 갱신**

---

## 10. 매도 조건 상세

**Phase 1** — 모든 보유 종목에 대해 매 사이클마다 체크 (우선순위 순):

| 조건 | 기준 |
|------|------|
| 상장폐지 | 업비트 활성 마켓에서 ticker 소멸 → 즉시 청산 |
| 익절 | 수익률 ≥ TAKE_PROFIT_PERCENT (기본 +5%) |
| 손절 | 수익률 ≤ -MAX_LOSS_PERCENT (기본 -3%) |
| **트레일링 스탑** | 고점 수익률 ≥ +3% 도달 후, 현재가가 고점 대비 -1.5% 하락 시 청산 |
| 매도신호 | action_class == "sell-strong" (total_score ≤ -5) |
| **time-stop** | 보유 시간 ≥ MAX_HOLD_HOURS (기본 48h) → 청산 (좀비 포지션 방지) |

- 판정용 가격은 **캐시 대신 실시간 `get_current_price()`를 우선** 사용 (보유 ≤ 5종이라 부담 적음).
- 수익률은 시뮬·실거래 동일하게 수수료 차감 기준: `profit_pct = (현재가 × 0.9995 - entry_price) / entry_price × 100`
- **매도 체결 검증**: 매도 직전 실잔고를 조회해 전량 매도하고, 주문 응답의 uuid를 확인한다. **실패 시 포지션을 유지**하고 다음 사이클 재시도 (`sell_fail` 로그, 종전에는 실패해도 포지션을 제거해 미추적 코인이 발생했음).
- **dust 포지션**: 평가액이 5,000원 미만이면 매도 주문이 불가하므로 스킵 (1회만 로그).
- 매도 확정 후 → **30분 쿨다운** + 일일 손익·연속 손절 카운터 갱신 + 상태 저장.

**매매 중지 시 처리 (app.py):**
- 중지 요청 즉시 `enabled=False` + epoch 증가 → 진행 중 사이클의 추가 주문 차단
- `청산 후 멈춤`: 보유 전 종목 시장가 매도 (BLACKLIST 제외). **매도 성공 건만 포지션에서 제거**하고, 실패 종목은 포지션에 남겨 UI에 표시 + 응답에 실패 목록 포함. 청산 성공 종목은 쿨다운 기록.
- `보유 유지 멈춤`: 실제 매도 없이 봇 추적만 종료 (로그 타입 `hold` — 종전에는 `sell`로 기록돼 이력이 왜곡됐음)

---

## 11. 슬롯 시스템

포지션은 `_trading_state["positions"]` dict에 `{ticker: 포지션정보}` 형태로 저장.

**슬롯 사용량 계산:**
```python
used_slots = sum(pos["slot_count"] for pos in positions.values())
available_slots = MAX_POSITIONS - used_slots
```

| 상황 | slot_count | 슬롯 사용 |
|------|-----------|-----------|
| 신규 매수 | 1 | 1슬롯 |
| 추가매수 1회 | 2 | 2슬롯 |
| 추가매수 2회 | 3 | 3슬롯 |

**재시작 시 동기화 (`_sync_live_positions`):**
- 봇 재시작 시 업비트 실제 잔고 → positions 자동 동기화
- 동기화로 추가된 포지션은 `slot_count=1`, `bot_bought=False`로 설정
- `bot_bought=False`: 봇 시작 전부터 보유하던 코인 (청산/추가매수 모두 정상 적용)
- **가격 조회 실패 코인은 "팔린 것"으로 오인하지 않고 보존** (종전에는 일시적 네트워크 오류만으로 포지션 추적이 소실됐음)
- 평가액 5,000원 미만 dust는 새 포지션으로 등록하지 않음 (슬롯 점유 방지)

---

## 12. AI 분석 로직

`ai_analysis.py` — Claude Haiku / CryptoPanic 없음. **외부 무료 API 2종만 사용.**

### Fear & Greed Index (1시간 갱신)

- 출처: `https://api.alternative.me/fng/` (무료, API 키 불필요)
- 값 범위: 0(극단 공포) ~ 100(극단 탐욕)
- **F&G ≥ 80(극단 탐욕)** 또는 **F&G ≤ 20(극단 공포)** → Phase 2/2.5 신규 매수 **전면 차단** (임계값은 .env로 조정 가능)
- 워커는 기동 직후 즉시 1회 조회 (종전에는 첫 5분간 필터 공백), 조회 실패 시 5분 뒤 재시도
- 관망 로그에 현재 F&G 값 표시

### 업비트 활성 마켓 체크 (5분 갱신)

- 출처: `https://api.upbit.com/v1/market/all` (공식 API, 인증 불필요)
- KRW 마켓 전체 활성 ticker 목록을 set으로 캐싱
- **보유 중인 ticker가 활성 목록에서 소멸** → Phase 1에서 즉시 시장가 매도 (거래지원 종료 감지)

### trader.py 연동 방식

```python
# Phase 1 (매도 체크)
from core.ai_analysis import get_active_markets
active_mkts = get_active_markets()
if active_mkts and ticker not in active_mkts:
    sell → "업비트 거래지원 종료 감지"

# Phase 2 (신규 매수)
from core.ai_analysis import get_fear_greed
fg = get_fear_greed()
if fg and fg["value"] >= 80:
    pass  # 신규 매수 전면 차단
```

> 순환 import 방지를 위해 trader.py → ai_analysis.py 참조는 함수 내부 lazy import 패턴 사용

---

## 13. Telegram 알림 · 원격 운영

### Telegram 알림 (`core/notifier.py`)

`.env`에 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` 설정 시 자동 활성화 (미설정이면 전체 no-op).

**알림 발생 이벤트:**

| 이벤트 | 발신 위치 |
|--------|-----------|
| 매수 / 매도 / 매수실패 / 매도실패 | `trader.py` → `_log_trade()` (관망 hold 제외) |
| 자동 매매 시작 / 중지 | `app.py` start/stop 라우트 |
| 연속 손절 보호 발동 | `trader.py` → `_record_sell_locked()` |
| 일일 손실 한도 도달 | `trader.py` → `_record_sell_locked()` |

**설계:** 전송은 백그라운드 큐 워커 스레드(데몬)에서 처리 — Telegram API 장애·지연이 매매 사이클을 블로킹하지 않음. 큐 포화(100건) 시 메시지를 버리고 경고 로그만 남김.

**설정 & 테스트:**
```bash
# .env에 토큰/챗ID 추가 후
python scripts/test_telegram.py
```

### Telegram 명령 봇 (`core/telegram_bot.py`)

폰에서 봇에게 메시지를 보내 매매를 원격 제어. 서버 기동 시 자동 시작 (미설정 시 비활성).

| 명령 | 동작 |
|------|------|
| `/status` | 상태·잔고·포지션·수익률 요약 |
| `/start_sim` | 시뮬레이션 시작 |
| `/start_live` | 실거래 시작 (`/confirm` 2단계 확인 필수) |
| `/stop` | 매매 중지 (포지션 보유 유지) |
| `/liquidate` | 매매 중지 + 전액 청산 (`/confirm` 2단계 확인 필수) |
| `/confirm` | 위험 명령 확인 (60초 이내) |
| `/help` | 명령어 목록 |

**보안 설계:**
- `.env`의 `TELEGRAM_CHAT_ID`에서 온 메시지만 처리 — 타인이 봇을 찾아 명령해도 무시됨
- 실거래 시작·전액 청산은 `/confirm` 2단계 확인 (오터치 방지)
- 서버 재시작 시 쌓여 있던 이전 명령 전부 폐기 (오래된 `/liquidate` 재실행 사고 방지)
- getUpdates long polling 데몬 스레드 — 매매 사이클과 완전 분리
- 같은 토큰으로 PC·VPS 동시 구동 시 Telegram이 409 반환 → 한쪽만 켤 것 (60초 백오프 후 재시도)

매매 시작/중지 로직은 `core/trading_control.py`로 분리되어 대시보드(Flask)와 Telegram 봇이 동일 코드를 공유한다 (중복 시작 가드 포함).

### VPS 24시간 운영 (PC 꺼도 봇 유지)

상세 절차는 **[deploy/DEPLOY_GUIDE.md](deploy/DEPLOY_GUIDE.md)** 참조. 요약:

```
Oracle Cloud 무료 VPS (Ubuntu 22.04)
 ├─ deploy/setup_vps.sh     — venv + 의존성 + Tailscale 자동 설치
 ├─ deploy/upbit-bot.service — systemd 등록 → 부팅/크래시 시 자동 재시작
 ├─ DASHBOARD_HOST=Tailscale IP — 폰/PC에서 http://100.x.x.x:5000 접속
 └─ Upbit Open API 허용 IP에 VPS 공인 IP 등록 필수
```

> 봇 상태는 `data/bot_state.json`으로 영속화되므로 systemd 자동 재시작 후에도 포지션·매매 상태가 그대로 복원·재개된다.

---

## 14. 하드코딩 값 목록

코드에 직접 박혀 있어 `.env`로 변경 불가한 값들:

| 파일 | 위치 | 값 | 설명 |
|------|------|----|------|
| `trader.py` | `TRADING_BLACKLIST` | `{"KRW-XRP", "KRW-CRO", "KRW-RVN"}` | 매수·매도 절대 제외 코인 |
| `trader.py` | `FEE_RATE` | `0.0005` | 업비트 수수료 0.05% (수익률·시뮬 회계 공통) |
| `trader.py` | Phase 2 | `total_score ≥ 8` | 신규 매수 점수 기준 |
| `trader.py` | Phase 2 | `per_coin × 0.999` | 매수 주문금액 버퍼 (수수료 0.05% + 여유) |
| `trader.py` | Phase 2.5 | `timedelta(hours=1)` | 추가매수 쿨다운 (마지막 매수 기준) |
| `trader.py` | `_MAX_LOG` | `50` | 메모리 거래 이력 보관 건수 (전체는 trade_history.jsonl) |
| `trader.py` | `auto_trade_worker` | `30초` | 자동매매 사이클 주기 |
| `upbit_client.py` | `get_order_result` | `max_wait=4초, 0.5초 폴링` | 체결 조회 폴링 파라미터 |
| `data_builder.py` | `get_top_volume_tickers` | `limit=20` | 분석 대상 코인 수 |
| `data_builder.py` | `build_market_data` | 1분봉 100개, 1시간봉 100개, 일봉 60개 | OHLCV 조회 캔들 수 |
| `data_builder.py` | `cache_worker` | `30초` | 보유 코인 분석 갱신 주기 |
| `data_builder.py` | `MARKET_COINS` | 20개 코인 목록 | 거래대금 API 실패 시 폴백 목록 |
| `ai_analysis.py` | `AI_CHECK_INTERVAL` | `300초 (5분)` | 업비트 마켓 체크 주기 |
| `ai_analysis.py` | `AI_FG_INTERVAL_HOURS` | `1시간` | Fear & Greed 갱신 주기 |
| `analysis.py` | `action_from_score` | 각 점수 구간 | 점수 → 신호 변환 기준 |
| `indicators.py` | `calculate_stochastic` | `k=14, d=3` | 스토캐스틱 파라미터 |
| `indicators.py` | `calculate_volume_ratio` | `period=20` | 거래량 비율 기준 봉 수 |
| `analysis.py` | `support_resistance` | `n=100` | 지지/저항 계산 기준 캔들 수 |

> 손절/익절/트레일링/추가매수/리스크 한도/F&G 임계값 등은 모두 `.env`로 이동 — [3. 환경 설정](#3-환경-설정-env) 참조.

---

## 15. 코드 최적화 이력

| 파일 | 수정 내용 |
|------|-----------|
| `trader.py` | `get_active_markets` lazy import를 for 루프 밖으로 이동 (루프 안 반복 import 제거) |
| `data_builder.py` | `import logging`을 파일 상단으로 이동 (except 블록 안 매번 import 제거) |
| `data_builder.py` | `get_top_volume_tickers` 기본값 `limit=10 → 20` (실제 사용 값과 일치) |
| `data_builder.py` | `build_market_data` 루프 내 중복 `if len >= 20: break` 제거 |
| `upbit_client.py` | `jwt.encode()`에 `algorithm="HS256"` 명시 (PyJWT 버전 호환성 보장) |

### 2026-06-10 고도화 (멀티 에이전트 리뷰 기반)

**정합성 버그 수정**
| 항목 | 내용 |
|------|------|
| 매도 체결 검증 | 매도 실패 시에도 포지션을 제거하던 버그 수정 — 실잔고 조회 후 매도, uuid 확인, 실패 시 포지션 유지·재시도 |
| 매수 체결 확정 | 시장가 매수 후 uuid로 체결 조회(`get_order_result`) → 실제 체결가·수량 기록 (종전엔 항상 추정치) |
| 추가매수 무한 반복 | `entry_ts` 기준이라 1시간 후 30초마다 추가매수되던 버그 → `last_add_ts` 기준으로 수정 |
| stop/start 레이스 | epoch 카운터 도입 — 중지 후 낡은 사이클의 매수/이중 매도 차단 |
| 동기화 오인 제거 | 가격 조회 실패 코인을 "팔린 것"으로 오인해 포지션을 삭제하던 문제 수정 |
| 청산 검증 | 청산 실패 종목은 포지션에 남기고 실패 목록 반환 (종전엔 무조건 "청산 완료") |
| 시뮬 회계 통일 | 수익률 계산에 수수료(0.05%) 일괄 반영, `sim_initial_total` 성과 기준점 기록 |

**리스크 관리 신설**
| 항목 | 내용 |
|------|------|
| 일일 손실 한도 | 당일 실현손실 ≥ 시작 자산의 5% → 당일 신규 매수 차단 |
| 연속 손절 보호 | 3연속 손절 → 2시간 전역 매수 쿨다운 |
| 트레일링 스탑 | 고점 +3% 도달 후 고점 대비 -1.5% 하락 시 청산 |
| time-stop | 48시간 초과 보유 포지션 청산 |
| 시장 신선도 게이트 | 마켓 캐시 180초 초과 노후 시 신규 매수 보류 |
| F&G 극단 공포 차단 | 폭락장(F&G ≤ 20) 신규 매수 차단 (종전엔 탐욕만 차단) |
| 물타기 금지 | 추가매수 허용 구간 -2~+3% → 0~+3% (수익 구간만), 종목당 슬롯 상한 2개 |
| 매수 실패 백오프 | 3연속 실패 종목 30분 매수 금지 (30초 무한 재시도 스팸 제거) |

**인프라**
| 항목 | 내용 |
|------|------|
| 상태 영속화 | `bot_state.json` — 포지션·쿨다운·리스크 상태 저장, 재시작 시 자동 복원·자동 재개 |
| 거래 이력 영속화 | `trade_history.jsonl` — 전체 체결 이력 append 기록 |
| 파일 로깅 | `bot.log` 회전 로그 (1MB × 3), 사이클 예외 traceback 기록 |
| 기타 | ai_worker 기동 즉시 1회 실행, F&G 실패 시 재시도, dust 포지션 처리, `KRW-MATIC → KRW-POL`, requirements 정리(anthropic 제거·PyJWT 명시), 워커 기동을 `__main__`으로 이동 |

### 2026-06-12 원격 운영 기능 추가

| 항목 | 내용 |
|------|------|
| Telegram 알림 | `core/notifier.py` 신설 — 매수/매도/실패/리스크 차단/시작·중지 이벤트 폰 푸시 (백그라운드 큐, 매매 사이클 비블로킹) |
| 연결 테스트 | `scripts/test_telegram.py` — 토큰/챗ID 설정 진단 |
| 대시보드 바인딩 설정화 | `DASHBOARD_HOST` / `DASHBOARD_PORT` env 추가 (VPS에서 Tailscale IP 바인딩용, 기본 127.0.0.1 유지) |
| VPS 배포 패키지 | `deploy/` 신설 — DEPLOY_GUIDE.md (Oracle Cloud + Tailscale 가이드), setup_vps.sh (자동 셋업), upbit-bot.service (systemd) |
| Telegram 명령 봇 | `core/telegram_bot.py` 신설 — `/status` `/start_sim` `/start_live` `/stop` `/liquidate` 원격 제어 (chat_id 검증, 위험 명령 2단계 확인, 재시작 시 이전 명령 폐기) |
| 시작/중지 로직 공유화 | `core/trading_control.py` 신설 — app.py 라우트에서 로직 분리, 대시보드·Telegram 공용 + **중복 시작 가드** (실행 중 재시작으로 포지션 리셋되던 위험 제거) |
| 프로덕션 WSGI 서버 | `waitress` 도입 — Flask 개발 서버 경고 제거, 24시간 운영 안정성 (미설치 시 기존 방식 폴백) |

### 2026-06-12 매도 분석 로직 검토 (2-에이전트 교차 검증)

| 항목 | 내용 |
|------|------|
| 보유 코인 매도 신호 공백 수정 | `trader.py` market_map 폴백 — 보유 코인이 거래량 톱20에서 밀리거나 수집 실패 시 `action_class: None`이 들어가 sell-strong 지표 매도가 조용히 비활성화되던 결함 → holdings 캐시의 action_class로 보완 (보수적 가중치라 매도 보호용 안전) |
| 일봉 데이터 수렴 보강 | `data_builder.py` 일봉 조회 count 60 → 200 — EMA50(adjust=False)·MACD(26) 시드 편향 제거, API 비용 동일 |
| 검토 결론 (수정 불요 판정) | 차트 지표만으로 충분 (호가창·펀딩비·뉴스 추가는 비용 대비 효과 없음), 매도는 가격 규칙(익절/손절/트레일링/타임스탑)이 실시간 가격으로 주도하는 현 구조 유지, score_signal 매수 편향·미완성 캔들 리페인팅·매수 수수료 미반영 등은 인지하되 현상 유지 |

### 2026-06-13 로그 분석 기반 정리 (실거래 로그 진단)

| 분류 | 항목 | 내용 |
|------|------|------|
| 로그 위생 | 시장 스캔 현재가 실패 노이즈 제거 | `get_current_price(ticker, warn=False)` 추가 — 거래대금 톱20 스캔 중 상장폐지/신규 티커가 `Code not found`를 반환해 매 30초 사이클마다 WARNING이 도배되던 문제(전체 WARNING의 약 93%)를 DEBUG로 강등. **보유 코인 가격 조회 등 실패가 유의미한 경로는 기본 `warn=True` 유지** — 매도 판정 경로의 경고는 그대로 보임 (`upbit_client.py`, `data_builder.py` 스캔 호출부만 적용). 매매 로직·임계값 불변. |

### 2026-06-13 고도화 (전용 에이전트 진단 + 백테스트 기반)

**`.claude/agents/bot-enhancer.md`** 진단 에이전트로 코드베이스를 정밀 분석하고, **`scripts/backtest.py`** 백테스터로 검증한 뒤 적용.

| 분류 | 항목 | 내용 |
|------|------|------|
| 안전 수정 | 리스크 게이트 공백 제거 | 매매 시작/중지 시 `_ai_cache.clear()` 호출을 제거 — 종전엔 F&G 매수 게이트·상장폐지 감지 캐시가 비워져 워커 재적재(최대 5분)까지 공백이 생겼음 |
| 안전 수정 | 죽은 AI 머지 코드 제거 | Haiku 제거 후 항상 None이던 `app.py`의 티커별 AI 병합 블록 + `app.js` AI 배지/`ai_reason` 렌더 제거 |
| 안전 수정 | 시뮬 회계 표시 통일 | 미실현 손익 표시를 시뮬·실거래 모두 매도 수수료 차감 기준으로 일치(대시보드·Telegram `/status`). 종전엔 시뮬만 무수수료라 약간 낙관적 |
| 검증 인프라 | 백테스터 | `scripts/backtest.py` — 라이브와 동일한 `score_signal`·지표로 진입(score≥8), trader.py Phase 1 우선순위로 청산(익절/손절/트레일링/매도신호/time-stop), 왕복 수수료 반영. 승률·거래당 기대값·MDD·청산사유 분포 출력. (일봉은 완성봉만 사용해 룩어헤드 제거, 1분봉 항목 생략) |
| 검증 인프라 | 누적 성과 API | `/api/performance` — `trade_history.jsonl` 집계(시뮬/실거래 분리: 건수·승률·실현손익·평균·최고/최저). 대시보드 매매 패널에 한 줄 표시 |
| 로직(게이트) | 진입 디바운스 | `BUY_CONFIRM_TICKS` — score≥8 연속 N사이클 유지 후 진입(리페인팅 스파이크 차단). **백테스트상 2+는 개선 없어 기본 1(현행 유지)** |
| 로직(게이트) | 포지션 사이징 캡 | `EQUAL_WEIGHT_SIZING`(기본 True) — 코인당 매수금액을 총자산/MAX_POSITIONS로 캡해 단일 코인 집중(빈 슬롯 1개에 전액 몰빵) 방지 |
| 로직(게이트) | 추가매수 스위치 | `ADD_BUY_ENABLED`(기본 True) — Phase 2.5 추가매수 on/off |
| 운영 | API 호출 절감 | Phase 2.5 잔고 재조회를 (Phase 2 잔고 − 매수액) 추정으로 대체해 실거래 사이클당 1콜 절감 |
| 운영 | rate-limit 백오프 | `upbit_client`가 주문·체결조회 응답의 `Remaining-Req` 헤더를 읽어 초당 잔여 ≤1이면 0.3초 백오프 |

> **백테스트 발견 (요주의):** 최근 90일·10종목 구간에서 현 진입 룰(score≥8)은 **거래당 기대값이 음수**(약 -0.6%)였다. 익절(+5%)은 드물게 발동하고 손절·매도신호가 지배해 보상:위험이 비대칭이었다. 디바운스는 개선 효과가 없었다. **단일 구간(알트 약세장 포함 가능)·1분봉 생략·종가 청산이라는 한계가 있으나**, 실거래 전 `scripts/backtest.py`로 파라미터(임계점수·익손절폭)를 재검증할 것을 강력 권고. 이번 변경 중 사이징 캡은 이 비대칭 손실을 줄이는 방향의 안전장치다.

#### 백테스터 사용법
```bash
# 기본 10종목 90일, 즉시 진입
python scripts/backtest.py --days 90 --confirm 1
# 디바운스 효과 비교 (연속 2사이클 확인 후 진입)
python scripts/backtest.py --days 90 --confirm 2
# 특정 종목 + 장중 고저 터치로 손절/익절 판정
python scripts/backtest.py --tickers KRW-BTC,KRW-ETH --days 60 --intrabar
```

---

## 16. 주의사항

- **항상 시뮬레이션으로 충분히 검증 후 실거래 전환**
- `.env` 파일은 절대 Git에 커밋하지 말 것 (`.gitignore`에 포함되어 있음)
- 업비트 API Rate Limit: 매수 주문 사이에 `time.sleep(1)` 적용 중
- 서버를 여러 번 실행하면 포트 5000에 중복 프로세스가 생길 수 있음 → `restart.bat` 사용 권장
- 포지션·쿨다운·리스크 상태는 `data/bot_state.json`에 영속화 — 재시작 시 자동 복원되며, **매매가 켜진 채 재시작되면 자동 재개**됨. 봇을 완전히 끄려면 반드시 대시보드에서 매매 중지 후 종료할 것
- TRADING_BLACKLIST 코인은 매수/매도 모두 제외되므로 해당 코인을 거래하려면 코드에서 직접 제거 필요
