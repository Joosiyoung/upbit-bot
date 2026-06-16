# Upbit 자동매매 봇

Flask + pyupbit 기반 코인 자동매매 + KIS API 기반 국내 주식 시뮬 봇. 대시보드(웹), Telegram 원격 제어, Oracle Cloud VPS 24시간 운영.

## 실행 명령어

```bash
python app.py                                      # 로컬 실행
pip install -r requirements.txt                    # 의존성 설치

# 백테스트
python scripts/backtest.py --days 365             # 코인 전체
python scripts/stock_backtest.py --days 90        # 주식 전체 (KIS 키 필요)
```

## VPS 배포 절차

```bash
git push origin main
ssh -i "C:\Users\jso84\.ssh\upbit-vps-key" ubuntu@217.142.228.247 \
  "cd /home/ubuntu/upbit-bot && git pull && sudo systemctl restart upbit-bot"

# 로그 확인
ssh -i "C:\Users\jso84\.ssh\upbit-vps-key" ubuntu@217.142.228.247 \
  "sudo journalctl -u upbit-bot -n 50 --no-pager"
```

**주의**: 배포 전 반드시 사용자 승인("배포해") 필요. 자동 배포 금지.

## 에이전트 작업 파이프라인 (필수)

**파일 수정이 1건이라도 있으면** 아래 순서 필수. 단순 질문·조회는 생략.
Claude 메인은 소스 파일을 직접 Edit/Write 하지 않는다.

```
사용자 요청
    ↓
① coder    — 구현·수정 전담. 완료 시 변경 파일·줄 보고.
    ↓
② tester   — 정적 검사 + 구문/임포트/스모크 테스트.
    ↓
③ pm       — CLAUDE.md 체크리스트 대조 후 "배포 가능 / 재작업 필요" 판정.
    ↓
[사용자 "배포해" 승인 대기]
    ↓
④ deployer — git push → VPS pull → restart → 헬스체크 → 실패 시 롤백 안내.
```

**예외**: CLAUDE.md·README.md·`.claude/agents/` 수정은 Claude 메인 직접 처리 가능. pm 호출은 필수.
**재작업**: tester 실패 또는 pm "재작업 필요" → coder 재수정 → tester → pm.

### 에이전트 역할

| 에이전트 | 모델 | 역할 (Tools) | 호출 조건 |
|---------|------|-------------|---------|
| **coder** | sonnet | 기능 구현·버그 수정·리팩터링 (Read·Grep·Glob·Bash·Edit·Write) | 코드 수정 시 자동 |
| **tester** | sonnet | 정적 검사 + 스모크 테스트 (Read·Grep·Glob·Bash) | coder 완료 후 자동 |
| **pm** | sonnet | CLAUDE.md 체크리스트 최종 판정 (Read·Grep·Glob·Bash) | tester 통과 후 자동 |
| **deployer** | sonnet | git push → VPS restart → 헬스체크 (Bash 전용) | "배포해" 승인 후 자동 |
| **qa** | opus | 전체 코드베이스 런타임 오류 사전탐지 (스레드 안전성·None 역참조·타임존·HTML 인젝션·엣지케이스) + 코드 최적화 점검 (중복 API 호출·불필요한 재연산·비효율 자료구조·파일 I/O hot path). 수정 안 함 — Critical/High 이슈 발견 시 coder로 전달 | **수동** — "qa 실행", "전체 코드 검사", "품질 점검", "최적화 점검" |
| **bot-enhancer** | opus | 코드베이스 고도화 방안 제안. 수정 안 함 | "고도화 제안", "개선점 분석" |
| **planner** | opus | 새 기능 기획안 작성(draft/). 수정 안 함 | "기획해줘", "계획 잡아줘" |
| **security-auditor** | opus | 보안 취약점 점검 보고. 수정 안 함 | "보안 점검", "취약점 분석" |
| **daily-summarizer** | sonnet | 당일 커밋 파악 → CLAUDE.md 이력 추가 + 두 파일 최적화 (Read·Bash·Edit·Write) | "오늘 작업한 내용 정리해놔" |
| **incident-responder** | opus | 장애 원인 분석 + 핫픽스 지시. 수정 안 함 | **수동** — "봇이 죽었어", "장애 분석" |
| **log-analyzer** | opus | 코인 거래 로그(`data/trade_history.jsonl`) 분석. 버그 시 수정 | **수동** — "log-analyzer 실행" |
| **param-optimizer** | opus | 코인 백테스트(`backtest.py`) 파라미터 스윕 → 기대값 양수 조합 적용 | **수동** — "param-optimizer 실행" |
| **strategy-researcher** | opus | 코인 새 전략 연구 → draft/ 초안. 수정 안 함 | **수동** — "strategy-researcher 실행" |
| **stock-log-analyzer** | opus | 주식 거래 로그(`data/stock_trade_history.jsonl`) 분석. 버그 시 수정 | **수동** — "stock-log-analyzer 실행" |
| **stock-param-optimizer** | opus | 주식 백테스트(`stock_backtest.py`) 파라미터 스윕 → 적용 | **수동** — "stock-param-optimizer 실행" |
| **stock-strategy-researcher** | opus | 주식 전략 연구(KOSPI 레짐·섹터·시간대) → draft/ 초안. 수정 안 함 | **수동** — "stock-strategy-researcher 실행" |

> **수동 전용**: qa·log-analyzer·param-optimizer·strategy-researcher·incident-responder·stock-log-analyzer·stock-param-optimizer·stock-strategy-researcher·daily-summarizer — 사용자가 이름을 명시하거나 트리거 문구를 말할 때만 실행. **자동 위임 금지.**

### 수동 에이전트 운영 루틴

**[코인] 주 1회 점검**
```
log-analyzer → param-optimizer → (개선 시) coder → tester → pm → deployer
```

**[코인] 전략 개선** (log-analyzer 선행 필수)
```
log-analyzer → strategy-researcher → param-optimizer → (기대값 양수 시) coder → tester → pm → deployer
```

**[주식] 주 1회 점검** (표본 20건 미만이면 관찰만)
```
stock-log-analyzer → stock-param-optimizer → (개선 시) coder → tester → pm → deployer
```

**[주식] 전략 개선** (stock-log-analyzer 선행 필수)
```
stock-log-analyzer → stock-strategy-researcher → stock-param-optimizer → (기대값 양수 시) coder → tester → pm → deployer
```

**[장애]**
```
incident-responder → (수정 필요 시) coder → tester → pm → deployer
```

**[코드 품질 정기 점검]** (배포 후 또는 주 1회)
```
qa → (Critical/High 이슈 시) coder → tester → pm → deployer
```

## 아키텍처

```
app.py                  # Flask 진입점 + 백그라운드 워커 기동
core/
  config.py             # 환경변수 로드 + 전역 상수 (KST 타임존)
  trader.py             # 코인 매매 로직 (진입·청산·리스크·상태)
  trading_control.py    # 코인 시작/중지/상태 (Flask ↔ Telegram 공용)
  data_builder.py       # 시장 분석 데이터 빌드·캐시
  analysis.py           # 진입 점수 계산 (score_signal — 코인·주식 공용)
  indicators.py         # RSI, MACD, 볼린저, EMA
  telegram_bot.py       # Telegram 명령 봇
  notifier.py           # Telegram 알림 발송
  upbit_client.py       # pyupbit 래퍼
  ai_analysis.py        # Fear & Greed 조회·캐시 워커
  stock/
    trader.py           # 주식 시뮬 매매 로직 + _stock_market_notifier(장 시작 알림 데몬)
    trading_control.py  # 주식 시작/중지/상태 (Flask ↔ Telegram 공용)
    kis_auth.py         # KIS OAuth2 토큰 관리
    kis_client.py       # KIS API 래퍼 (OHLCV·현재가·잔고조회)
    universe.py         # 매매 대상 종목 풀 (KOSPI 대형주 20종목, 20만원 이하)
scripts/
  backtest.py           # 코인 백테스터
  stock_backtest.py     # 주식 백테스터 (KIS OHLCV → score_signal × 2.5)
data/
  trade_history.jsonl       # 코인 거래 이력 (365일 보존)
  stock_trade_history.jsonl # 주식 시뮬 거래 이력
  stock_state.json          # 주식 시뮬 상태 (포지션·잔고, 재시작 복원)
logs/
  bot.log               # 운영 로그 (35일 보존, 자정 회전)
```

## 환경변수

핵심 변수만 표기. 전체 목록(KIS 이중 자격증명·STOCK_* 파라미터)은 `README.md` 참조.

```
UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
DASHBOARD_HOST=100.92.237.11   # VPS: Tailscale IP. 로컬: 127.0.0.1
DASHBOARD_PORT=5000
MAX_POSITIONS=5
KIS_IS_SANDBOX=True            # True=모의투자, False=실거래
KIS_APP_KEY_SANDBOX / KIS_APP_SECRET_SANDBOX / KIS_ACCOUNT_NO_SANDBOX
```

VPS `.env`는 git 미추적 — pull해도 보존. 로컬과 별도 관리.

## 핵심 파라미터

### 코인 (config.py)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `BUY_SCORE_THRESHOLD` | 12 | 진입 점수 임계치 (365일 백테스트: +0.15% 기대값) |
| `MARKET_REGIME_FILTER` | True | BTC EMA 하락 시 전 종목 매수 차단 |
| `MAX_LOSS_PERCENT` | 3.0% | 손절 |
| `TAKE_PROFIT_PERCENT` | 5.0% | 익절 |
| `TRAILING_START_PCT` | 3.0% | 트레일링 스탑 활성화 수익률 |
| `TRAILING_STOP_PCT` | 1.5% | 고점 대비 하락 한도 |
| `MAX_HOLD_HOURS` | 48h | time-stop |
| `DAILY_LOSS_LIMIT_PCT` | 5.0% | 일일 손실 한도 초과 시 당일 매수 차단 |
| `EQUAL_WEIGHT_SIZING` | True | 종목당 금액을 (총자산÷MAX_POSITIONS)로 상한 |

### 주식 (config.py STOCK_*)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `STOCK_BUY_SCORE_THRESHOLD` | 12 | 진입 점수 임계치 (일봉 × 2.5 가중치) |
| `STOCK_MAX_LOSS_PERCENT` | 3.0% | 손절 |
| `STOCK_TAKE_PROFIT_PERCENT` | 5.0% | 익절 |
| `STOCK_TRAILING_START_PCT` | 3.0% | 트레일링 스탑 활성화 수익률 |
| `STOCK_TRAILING_STOP_PCT` | 1.5% | 고점 대비 하락 한도 |
| `STOCK_MAX_HOLD_DAYS` | 5 | time-stop (영업일 기준) |
| `STOCK_MAX_PRICE` | 200,000 | 1주 가격 상한 (투자금÷슬롯 기준) |
| `STOCK_BUY_CLOSE_TIME` | 15:20 | 신규 매수 마감 시각 |
| `STOCK_ENTRY_CHANGE_MAX` | +2.0% | 당일 등락률 상한 — 갭업 추격 차단 |
| `STOCK_ENTRY_CHANGE_MIN` | -3.0% | 당일 등락률 하한 — 폭락 종목 진입 차단 |
| `STOCK_ADD_BUY_ENABLED` | True | 추가매수 활성화 여부 |
| `STOCK_ADD_BUY_MIN_PROFIT` | 0.0% | 추가매수 허용 최소 수익률 — 손실 중 물타기 방지 |
| `STOCK_ADD_BUY_MAX_PROFIT` | 2.0% | 추가매수 허용 최대 수익률 — 추격매수 방지 |
| `STOCK_MAX_SLOTS_PER_TICKER` | 2 | 종목당 최대 슬롯 수 (추가매수 상한) |

## 매수 차단 게이트

**코인** (순서)
1. 일일 손실 한도 / 연속 손절 쿨다운
2. 시장 레짐 필터 (BTC 단기EMA < 중기EMA) — 시뮬 바이패스
3. F&G 극단값 (≥80 또는 ≤20) — 시뮬 바이패스
4. 시장 캐시 노후 (>180초)
5. 진입 점수 < BUY_SCORE_THRESHOLD

**주식**
1. 장 외 시간 — `is_market_hours()` False 시 워커 비활성
2. 매수 마감 시각(`STOCK_BUY_CLOSE_TIME`) 초과
3. 종가 > `STOCK_MAX_PRICE`
4. 진입 점수 × 2.5 < `STOCK_BUY_SCORE_THRESHOLD`
5. 당일 매도 이력 — `_stock_sold_today`에 기록된 종목 당일 재진입 차단
6. 당일 등락률 범위 초과 — `(현재가/전일종가 - 1) × 100` < `STOCK_ENTRY_CHANGE_MIN` 또는 > `STOCK_ENTRY_CHANGE_MAX`

## 주요 Gotchas

- **`_KST = config.KST`** — 반드시 `from core import config` 이후에. 순서 역전 시 `NameError`로 크래시.
- **`DASHBOARD_TOKEN`** — VPS `.env`에 반드시 설정. 빈 값이면 상태 변경 API 무인증 허용 + WARNING 출력.
- **Telegram HTML** — `<`, `>`, `&` 문자 → `html.escape()` 필수. 누락 시 400 에러.
- **0.0.0.0 바인딩 금지** — `DASHBOARD_HOST=0.0.0.0` 설정 시 `app.py` 기동 즉시 `sys.exit`.
- **Telegram 409 충돌** — 같은 토큰으로 로컬+VPS 동시 기동 금지.
- **상태 파일 datetime** — naive datetime → `_as_kst()`로 KST-aware 변환 후 비교.
- **`.gitignore` 수정** — PowerShell `echo >>` 금지 (UTF-16 인코딩). Write 도구 사용.
- **주식 시뮬 시작** — 장 외 시간 호출 시 즉시 거부. 재시작 시 기존 포지션·잔고 유지(`_stock_positions.clear()` 없음).
- **시뮬 모드 목적** — 레짐 필터·F&G 바이패스는 데이터 축적 목적. 진입 점수 임계치(12)는 유지.
- **VPS GitHub push 403** — VPS `credential.helper=store` 토큰 만료로 VPS에서 push 불가. 코드 수정은 반드시 로컬 push → VPS pull 순서. VPS에서 직접 push 시도 금지.
- **`_stock_sold_today` 비지속** — 서비스 재시작 시 초기화됨. 당일 매도 이력은 메모리에만 보관. 의도적 설계(재시작 후 당일 재진입은 허용).
- **`_daily_signal_cache` 단일 워커 전용** — `_stock_worker` 외 다른 스레드에서 접근 금지. 락 없이 설계됨.
- **`settings.json` VPS 미동기화 (의도적)** — Windows 전용 경로·명령(taskkill, powershell, cmd.exe 등) 포함. `.claude/*` gitignore로 제외됨. VPS는 Remote Control 최초 실행 시 자체 Linux 설정 자동 생성.

## 최근 변경 이력

| 날짜 | 구분 | 내용 |
|------|------|------|
| 2026-06-13 | 코인 | 추세정렬 전략 재설계 + BTC 레짐 필터 + 리페인팅 방지 (completed=True, iloc[-2]) |
| 2026-06-13 | 코인 | 점수 가중치 통일(day×2.5 + 4h×2 + 1h×0.5), 시뮬 수수료 이중반영 버그 수정 |
| 2026-06-13 | 코인 | 코인 일일 보고서 자동 발송 (매일 09:00 KST) |
| 2026-06-14 | 인프라 | coder → tester → pm → deployer 에이전트 파이프라인 확립 |
| 2026-06-14 | 주식 | Phase 2: 주식 시뮬 매매 엔진 (`core/stock/`) 신규 구축 |
| 2026-06-14 | 주식 | KIS 이중 자격증명(sandbox/real), 20종목 유니버스(20만원 이하), 주식 백테스터 |
| 2026-06-14 | 주식 | KIS 잔고 자동 조회(`get_cash_balance`), `/stock_start_sim` 예산 자동 설정 |
| 2026-06-14 | 주식 | 장 시작/마감 알림 데몬 분리(`_stock_market_notifier`), 장 외 시작 거부, 재개 모드(포지션 유지) |
| 2026-06-14 | 주식 | 주식 전용 에이전트 3종: stock-log-analyzer, stock-param-optimizer, stock-strategy-researcher |
| 2026-06-15 | 대시보드 | 코인/주식 탭 분리 (대시보드 UI 탭 독립) |
| 2026-06-15 | 인프라 | Telegram 주식 봇 분리 (`TELEGRAM_STOCK_BOT_TOKEN`), 주식 봇 명령 10개 완성 |
| 2026-06-15 | 인프라 | QA 에이전트 신설 — 전체 코드베이스 런타임 오류 사전탐지 (opus 모델) |
| 2026-06-15 | 품질 | QA 점검 4건 수정: naive datetime(`ai_analysis.py`), `kis_client` None 반환, `market_was_open` 초기화, `backtest.py` em-dash 인코딩 |
| 2026-06-15 | 인프라 | VPS Swap 2GB 추가, Node.js v24.16.0 + Claude Code v2.1.177 설치 (Remote Control 준비) |
| 2026-06-15 | 인프라 | `.claude/agents/` 15개 git 추적 추가 — VPS Remote Control 에이전트 동기화 |
| 2026-06-15 | 코인 | fix(trader): 쿨다운 만료 시 `consec_stoploss` 미리셋 버그 수정 (`_buy_block_reason_locked`) |
| 2026-06-15 | 인프라 | qa 에이전트 코드 최적화 점검 롤 추가 (중복 API 호출·재연산·자료구조·파일 I/O hot path) |
| 2026-06-16 | 주식 | fix(stock): 종목 가격 필터를 `sim_krw / empty_slots` 슬롯당 예산 기준으로 동적 계산 (STOCK_MAX_PRICE 고정값 대체) |
| 2026-06-16 | 주식 | feat(stock): 추가매수 로직 — `STOCK_ADD_BUY_ENABLED`, `STOCK_MAX_SLOTS_PER_TICKER=2`, 0~2% 수익 구간 조건부 추가매수 |
| 2026-06-16 | 주식 | feat(stock): 시뮬 모드에서 `buy_cutoff` 비활성화 (데이터 수집 목적), `_save_state()` 락 내 호출 (레이스컨디션 방지) |
| 2026-06-16 | 주식 | 당일 재진입 쿨다운: `_stock_sold_today` — 익절/손절 후 당일 동일 종목 재진입 차단 |
| 2026-06-16 | 주식 | 일봉 신호 캐시: `_get_daily_signal()` — KIS TR 99% 절감, 자정 자동 무효화 |
| 2026-06-16 | 주식 | 당일 등락률 필터: `STOCK_ENTRY_CHANGE_MIN/MAX` — 갭업 추격·폭락 종목 진입 차단 |
| 2026-06-16 | 주식 | 5분봉 타이밍 필터 보류 — 표본 50건 이상 축적 후 백테스트 비교 기반으로 재판단 예정 |
| 2026-06-16 | 인프라 | feat(docs): daily-summarizer 에이전트 신설 — "오늘 작업한 내용 정리해놔" 트리거 시 CLAUDE.md·README.md 자동 업데이트 |

## VPS 인프라

- Oracle Cloud ap-osaka-1 / VM.Standard.E2.1.Micro / Ubuntu 22.04
- `upbit-bot.service` (systemd, 자동 재시작)
- Tailscale 대시보드: `http://100.92.237.11:5000`
- SSH 키: `C:\Users\jso84\.ssh\upbit-vps-key`
- Swap 2GB (`/swapfile`) — OOM 방지
- Claude Code v2.1.177 설치됨 — Remote Control 지원

## 타임존

모든 `datetime.now()` 호출은 `datetime.now(config.KST)` 사용 (KST = UTC+9).
