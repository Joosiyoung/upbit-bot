# Upbit 자동매매 봇

Flask + pyupbit 기반 코인 자동매매 + KIS API 기반 국내주식 시뮬 봇. 대시보드(웹), Telegram 원격 제어, Oracle Cloud VPS 24시간 운영.

> **진행 중 작업**: 신호 재설계(BB 추격 가점 제거 + 임계치 13 + 시뮬 레짐게이트, 2026-07-14 채택)는 마진이 얇아(180일 분할검증 EV in +0.12%/out +0.18%) 라이브 시뮬 EV를 계속 추적 중. 남은 일: sim_krw 잔고 표시 0.05% 이중차감 버그(낮은 우선순위, EV 측정엔 무영향) 미수정, 주식 모듈 동일 처리(특성테스트·단일구현 추출)는 후순위 대기. 현황·근거는 [docs/UPGRADE_NOTES.md](docs/UPGRADE_NOTES.md) 참조. 청산 판정은 `core/exit_rules.py`, 진입 점수 가중치는 `core/scoring.py`(`score_signal`의 `bb_mode` 인자 포함) 단일 구현을 라이브·백테스터가 공유한다. 변경 시 `python -m pytest tests`로 특성 테스트 확인.

## 실행 명령어

```bash
python app.py                                      # 로컬 실행
pip install -r requirements.txt                    # 의존성 설치

# 백테스트
python scripts/backtest.py --days 365             # 코인 전체
python scripts/backtest.py --tp 6.0 --sl 4.0 --max-hold 48  # 파라미터 오버라이드
python scripts/stock_backtest.py --days 90        # 주식 전체 (KIS 키 필요)
```

## VPS 배포 절차

Claude Code가 VPS 위에서 직접 실행 중이므로 SSH 없이 배포 가능.

```bash
git push origin main
sudo systemctl restart upbit-bot

# 로그 확인
sudo journalctl -u upbit-bot -n 50 --no-pager
```

# 외부(로컬 PC)에서 배포 시
```bash
git push origin main
ssh -i "C:\Users\jso84\.ssh\upbit-vps-key" ubuntu@217.142.228.247 \
  "cd /home/ubuntu/upbit-bot && git pull && sudo systemctl restart upbit-bot"
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
  indicators.py         # RSI, MACD, 볼린저, EMA, ATR, 고점대비낙폭
  telegram_bot.py       # Telegram 명령 봇 (코인봇 13개 명령 포함 /restart_claude)
  notifier.py           # Telegram 알림 발송
  upbit_client.py       # pyupbit 래퍼
  ai_analysis.py        # Fear & Greed 조회·캐시 워커
  sheets_client.py      # Google Sheets 실시간 거래 적재 (코인·국내주식, 오프라인 버퍼링)
  stock/
    trader.py           # 국내 주식 시뮬 매매 로직 + _stock_market_notifier(장 시작 자동 시작·알림 데몬)
    trading_control.py  # 국내 주식 시작/중지/상태 (Flask ↔ Telegram 공용)
    kis_auth.py         # KIS OAuth2 토큰 관리
    kis_client.py       # KIS API 래퍼 (국내 OHLCV·현재가·거래량순위)
    universe.py         # 국내 매매 대상 종목 풀 (KOSPI 추세섹터 18종목)
scripts/
  backtest.py           # 코인 백테스터 (--tp/--sl/--max-hold CLI 오버라이드 지원)
  stock_backtest.py     # 주식 백테스터 (KIS OHLCV → score_signal × 2.5)
deploy/
  restart-claude.sh     # VPS claude-remote tmux 세션 kill 후 재기동 스크립트 (실행권한 100755 git 추적)
  claude-remote-watchdog.sh  # 세션 없을 때만 기동(멱등). cron: @reboot + */5 자동복구
data/
  trade_history.jsonl         # 코인 거래 이력 (365일 보존)
  stock_trade_history.jsonl   # 국내 주식 시뮬 거래 이력
  stock_state.json            # 국내 주식 시뮬 상태 (포지션·잔고, 재시작 복원)
  sheets_buffer.jsonl         # Google Sheets 오프라인 버퍼 (네트워크 오류 시 임시 보관)
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
| `BUY_SCORE_THRESHOLD` | 13 | 진입 점수 임계치 (2026-07-14 채택: bb-off+레짐게이트 180d in +0.12% / out +0.18%, 임계값 13·14 연속 통과) |
| `MARKET_REGIME_FILTER` | True | BTC EMA 하락 시 전 종목 매수 차단 (2026-07-14부터 시뮬에도 동일 적용) |
| `MAX_LOSS_PERCENT` | 4.0% | 손절 (2026-06-22 알트 유니버스 재튜닝: 3.0→4.0) |
| `TAKE_PROFIT_PERCENT` | 6.0% | 익절 (2026-06-22 재튜닝: 5.0→6.0, 알트 변동성에 넓은 타깃) |
| `TRAILING_START_PCT` | 9999 | **트레일링 비활성** (트레일링이 +5% 익절 도달을 100% 차단·승자 조기절단 → 9999로 OFF) |
| `TRAILING_STOP_PCT` | 1.5% | 고점 대비 하락 한도 (트레일링 OFF로 미사용) |
| `MAX_HOLD_HOURS` | 48h | time-stop. 파라미터 스윕(24조합) 결과 현행값 유지 결정 (2026-07-05) |
| `DAILY_LOSS_LIMIT_PCT` | 5.0% | 일일 손실 한도 초과 시 당일 매수 차단 |
| `EQUAL_WEIGHT_SIZING` | True | 종목당 금액을 (총자산÷MAX_POSITIONS)로 상한 |

### 주식 (config.py STOCK_*)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `STOCK_BUY_SCORE_THRESHOLD` | 12 | 진입 점수 임계치 (일봉 × 2.5 가중치) |
| `STOCK_MAX_LOSS_PERCENT` | 5.0% | 손절 (⚠️ 2026-07-02 KIS 페이징 버그 수정 전 ~5개월 데이터 기반 — 재검증 필요) |
| `STOCK_TAKE_PROFIT_PERCENT` | 5.0% | 익절 |
| `STOCK_TRAILING_START_PCT` | 9999 | **트레일링 비활성** (백테스트상 이익 조기 절단으로 손해 → 도달 불가능 값으로 OFF) |
| `STOCK_TRAILING_STOP_PCT` | 1.5% | 고점 대비 하락 한도 (트레일링 OFF로 미사용) |
| `STOCK_MAX_HOLD_DAYS` | 20 | time-stop (영업일 기준, ⚠️ 2026-07-02 KIS 페이징 버그 수정 전 ~5개월 데이터 기반 — 재검증 필요) |
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
2. 시장 레짐 필터 (BTC 단기EMA < 중기EMA) — 시뮬에도 동일 적용 (2026-07-14~)
3. F&G 극단값 (≥80 또는 ≤20) — 시뮬 바이패스
4. 시장 캐시 노후 (>180초)
5. 스테이블코인(`config.STABLE_COINS`) 또는 개인 보유분(`PERSONAL_HOLDINGS_BLACKLIST`: XRP·CRO·RVN) — 신규/추가 매수 시 필터링 (`TRADING_BLACKLIST` = 두 집합 합집합, 총 11종). **청산(Phase 1)은 별도 규칙**: 개인 보유분은 무조건 제외, 스테이블코인은 `bot_bought=True`면 청산 허용 (2026-07-02)
6. 진입 점수 < BUY_SCORE_THRESHOLD

**국내주식**
1. 장 외 시간 — `is_market_hours()` False 시 워커 비활성
2. 매수 마감 시각(`STOCK_BUY_CLOSE_TIME`) 초과 — 시뮬 모드 비활성
3. 슬롯당 예산(`sim_krw / empty_slots`) 대비 종목 가격 초과
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
- **주식 시뮬 시작** — 장 외 시간 호출 시 즉시 거부. 재시작 시 기존 포지션·잔고 유지(`_stock_positions.clear()` 없음).
- **시뮬 모드 목적** — F&G 바이패스만 유지(데이터 축적 목적). 레짐 필터는 2026-07-14부터 시뮬에도 동일 적용(백테스트 검증 구성과 일치). 진입 점수 임계치(13)는 라이브와 동일.
- **VPS git push** — SSH 키 인증(`~/.ssh/id_ed25519`)으로 해결됨. `git push origin main` VPS에서 직접 가능.
- **`_stock_sold_today` 비지속** — 서비스 재시작 시 초기화됨. 당일 매도 이력은 메모리에만 보관. 의도적 설계(재시작 후 당일 재진입은 허용).
- **`_daily_signal_cache` 단일 워커 전용** — `_stock_worker` 외 다른 스레드에서 접근 금지. 락 없이 설계됨.
- **`.gitignore` 수정** — PowerShell `echo >>` 금지 (UTF-16 인코딩 손상). Write 도구로 직접 수정할 것.
- **`settings.json` VPS 미동기화 (의도적)** — Windows 전용 경로·명령 포함. `.claude/*` gitignore로 제외됨. VPS는 Remote Control 최초 실행 시 자체 Linux 설정 자동 생성.
- **`build_stock_status_msg` 현재가 조회** — `get_current_price()`는 예외를 던지지 않고 None 반환. `try/except` 사용 금지. `if not price: price = entry_price` 패턴 사용. None 반환 시 진입가를 fallback으로 표시하고 `(진입가)` suffix 추가.
- **Sheets "주식" 시트** — 국내주식 16컬럼(`_STOCK_HEADERS`)으로 운영. 코인·국내주식 2개 시트 + 테이블정의서. 국내주식 Sheets 로깅 활성(`trader._log_trade`).
- **주식 봇 Telegram 명령** — 현재 9개 활성 (`/start_sim`, `/stop`, `/status`, `/perf`, `/positions`, `/history`, `/params`, `/market`, `/help`). 모두 국내주식 전용.
- **트레일링 비활성 방식** — `STOCK_TRAILING_START_PCT=9999`로 트레일링 분기가 절대 발동하지 않게 설정만으로 OFF (trader.py 로직 무변경). 재활성화 시 값을 정상 범위(예: 3.0)로 되돌리면 됨.
- **`_stock_market_notifier` 기동** — `app.py`에서 국내주식 장 시작(09:00) 알림 스레드 기동. 시뮬 상태와 무관하게 항상 동작. 장 시작 감지 시 `start_stock_trading()` 자동 호출 포함 (이미 실행 중이면 skip).
- **프로젝트 서브에이전트 미등록 가능성** — 일부 런타임 세션(예: VPS Remote Control)에서 `.claude/agents/`의 에이전트들이 Agent 툴 `subagent_type`으로 로드되지 않을 수 있다. `'coder' not found. Available agents: claude, ...` 에러로 나타남. 복구: 프로젝트 디렉터리에서 세션 재시작. 그래도 안 되면 메인이 파이프라인 전 단계 직접 대행. `/restart_claude` Telegram 명령으로 폰에서 세션 재시작 가능.
- **`ANTHROPIC_API_KEY` 프롬프트 차단** — `.env`에 `ANTHROPIC_API_KEY`가 환경에 있으면 새 `claude --remote-control` 기동 시 대화형 프롬프트에서 멈춰 리모트 컨트롤이 뜨지 않는다. 해결: `restart-claude.sh` 기동 명령에 `unset ANTHROPIC_API_KEY` 포함(Remote Control은 Claude Pro 로그인으로 동작, API 키 불필요).
- **deploy/*.sh 실행권한 git 추적** — `git update-index --chmod=+x`로 100755 기록됨 → pull해도 +x 유지. Windows에서 `.sh` 파일 커밋 시 실행 비트 유실 주의.
- **claude-remote 세션 운영** — `deploy/claude-remote-watchdog.sh`가 cron(`@reboot` + `*/5 * * * *`)으로 세션 자동복구. `deploy/restart-claude.sh`는 폰 `/restart_claude` Telegram 명령으로 호출. 봇은 `upbit-bot.service` 별도 프로세스라 claude-remote 세션 종료와 무관하게 동작.
- **`TRADING_BLACKLIST` 이원적 의미** — 매수 차단에는 `PERSONAL_HOLDINGS_BLACKLIST`(XRP·CRO·RVN) ∪ `config.STABLE_COINS`(8종) 합집합 11종 전부 적용. 청산(Phase 1)에서는 개인 보유분만 무조건 스킵, 스테이블코인은 `bot_bought=True`면 청산 허용. 두 세트를 하나로 취급하면 봇이 실수로 산 스테이블코인이 영구 동결됨 (2026-07-02 USDT 3,388만원 동결 버그로 발견).
- **`_sync_live_positions` 라이브 모드 잠재 리스크** — 실거래(`live:true`) 전환 시 블랙리스트 제외 종목을 "매도됨"으로 오인해 포지션이 조용히 사라질 수 있다. 현재 `live:false`라 비활성 상태이지만 라이브 전환 전 반드시 검토·수정 필요.
- **KIS 일봉/분봉 API 응답은 내림차순(최신순)** — `inquire-daily-itemchartprice` 등 KIS OHLCV 응답은 날짜가 최신→과거 순. 페이징 시 `df.index.min()`으로 명시적으로 최솟값을 구해야 함. `df.index[0]` 사용 시 하루치만 중복 이동하는 버그 발생 (2026-07-02 수정됨).
- **섀도우 로그 (`shadow` 필드)** — 매수 시점 게이트 상태(`regime_ok`·`fg_block`·`atr_pct`·`pullback_pct`·`bb_pct`)가 JSONL에 기록됨. 필드 자체는 매매 판단에 직접 반영되지 않는 진단용 기록. 78건 실측 기반 신호 재설계(BB 추격 가점 제거) 2026-07-14 채택 완료 — 이후로는 채택된 신호의 라이브 성과 추적 목적으로 계속 축적 중. 진단 도구: `scripts/shadow_analysis.py`(매수 shadow ↔ 매도 손익 FIFO 매칭, 피처별 분위 분석).

## 최근 변경 이력

| 날짜 | 구분 | 내용 |
|------|------|------|
| 2026-06-13~17 | 초기구축 | 코인 추세정렬 전략 재설계(BTC 레짐 필터·리페인팅 방지·점수 가중치), 국내주식 시뮬 엔진(`core/stock/`) 구축, 에이전트 파이프라인 확립(coder·tester·pm·deployer·qa), 대시보드 탭 분리, Google Sheets 적재, VPS Remote Control 환경 준비, 미국주식 모듈 시도 후 수수료 구조상 수익 불가 확정으로 전면 제거 → 국내 전용 롤백 |
| 2026-06-22 | 코인 | **파라미터 재튜닝(알트 유니버스 적합)** — 익절 5→6%, 손절 3→4%, 트레일링 OFF(`TRAILING_START_PCT=9999`). 실제 알트 10종 백테스트(90일+12일 교차검증)에서 TP6/SL4/trailOFF가 두 레짐 모두 우위(12일 기대값 +0.075%→+0.271%) |
| 2026-06-22 | 인프라 | **폰 Remote Control 운영 체계 구축** — `/restart_claude` Telegram 명령(2단계 `/confirm` 패턴, chat_id 인증), `deploy/restart-claude.sh`·`deploy/claude-remote-watchdog.sh` 신규, `.sh` 실행권한 100755 git 추적 |
| 2026-06-24 | 주식 | 장 자동 시작 — `_stock_market_notifier`에서 09:00 장 시작 감지 시 `start_stock_trading()` 자동 호출. 수동 `/start_sim` 불필요 |
| 2026-06-24 | 주식 | 진입 스캔 진단 로깅 — 스캔 시작·종목별 스킵 이유·스캔 완료 로깅 추가 |
| 2026-06-25 | 주식 | 장애 분석 — KIS 거래량순위 API 404 상시, FALLBACK_UNIVERSE 폴백 동작 확인. 방어주 유니버스 전체 점수 미달(최고 신한지주 7.5, 임계치 12) 확인 |
| 2026-06-28 | 코인 | **스테이블코인 제외**: `TRADING_BLACKLIST` 확장 (USDT·USDC 등 8종). log-analyzer: 승률 46.2%, EV -0.418%. param-optimizer 96조합 전 음수 → 신호 재설계 필요 판단 |
| 2026-06-28 | 주식 | stock-log-analyzer·stock-param-optimizer: FALLBACK_UNIVERSE 방어주와 추세추종 전략 미스매치 확인. 추세섹터 종목(삼성·SK하이닉스 등) 일부 양수 기대값 확인 |
| 2026-06-29 | 주식 | **유니버스 교체 + 예산 상향**: 방어주 19종 → 추세섹터(반도체·2차전지·방산·자동차·바이오·플랫폼·조선·철강·엔터·반도체장비) 18종. `STOCK_SIM_BUDGET` 100만 → 500만원 |
| 2026-06-29 | 인프라 | **app.py 재시작 복원 버그 수정**: `enabled:true` + 포지션 없음 상태로 재시작 시 워커 직접기동 → 주말 스캔·예산 미초기화 문제 수정 |
| 2026-07-02 | 코인 | **USDT 3,388만원 영구 동결 버그 수정**: `TRADING_BLACKLIST`를 청산 스킵에도 적용하던 버그 수정 → 개인보유(`PERSONAL_HOLDINGS_BLACKLIST`)만 청산 스킵, 스테이블코인은 `bot_bought=True`면 청산 허용 |
| 2026-07-02 | 코인 | **섀도우 로그 추가**: 매수 시점 `regime_ok`·`fg_block`·`atr_pct`·`pullback_pct`·`bb_pct`를 `shadow` 필드로 JSONL 기록. `indicators.py`에 ATR·고점대비낙폭 추가 |
| 2026-07-02 | 코인 | **신호 재설계 백테스트 실험**: BB 부호반전·ATR 하한게이트를 `--bb-mode`/`--min-atr` 플래그로 구현. 방향성 개선 확인되나 채택기준(두 구간 모두 양(+) EV) 미달 → 운영 코드 미반영 |
| 2026-07-02 | 주식 | **KIS 일봉/분봉 페이징 버그 수정**: `df.index[0]` → `df.index.min()`. 과거 백테스트가 ~5개월 데이터로만 수행됐음 드러남 → `STOCK_MAX_LOSS_PERCENT`·`STOCK_MAX_HOLD_DAYS` 재검증 필요 |
| 2026-07-02 | 품질 | 죽은 코드 제거(`stock/trader.py`), Windows 레거시 파일 3종 삭제, 오해성 주석 정정 |
| 2026-07-05 | 코인 | **청산 파라미터 스윕 완료 — 변경 없음**: MAX_HOLD_HOURS {8,12,16,24} × TP {5,6,7} × SL {3,4} 24조합 × 90d/180d = 48회. 채택기준 통과 조합 0개 — 전 조합 음수 EV. 현행 파라미터(TP=6.0, SL=4.0, MAX_HOLD=48h) 유지. 파라미터 축 소진 → 다음 단계는 섀도우 로그 기반 신호 재설계 |
| 2026-07-05 | 코인 | **backtest.py `--tp`/`--sl`/`--max-hold` CLI 인자 추가**: 파라미터 스윕 자동화를 위해 스크립트 레벨 config 오버라이드 가능하도록 수정 |
| 2026-07-05 | 인프라 | **폴더 정리**: `project_report.html`, `draft/plan_stock_module_*.md`, `draft/us_stock_implementation_plan.md`, `draft/_sheets_export/` 삭제. `.gitignore`에 `skills-lock.json` 추가 |
| 2026-07-05 | 방향 | **신호 재설계 방향 채택**: log-analyzer(corr=0.049)·bot-enhancer(corr=0.051)·param-optimizer 스윕(전조합 음수) 3개 독립 분석 일치 → 섀도우 로그 30건 이상 축적 후 진입 신호 재설계 단계 진입 예정 |
| 2026-07-14 | 코인 | **신호 재설계 채택 — BB 추격 가점 제거 + 임계치 13 + 시뮬 레짐 정합화**: 섀도우 78건 매칭(bb_pct 하위 +0.58% vs 상위 -0.81%)·주식 백테스트 교차 확인 후 `bb-mode off + regime-gate` 스윕에서 채택기준 최초 통과(th13 in +0.12%/out +0.18%, th14 연속 통과). `score_signal(ind, bb_mode)` 인자 추가(코인 "off"·주식 기본값 "current"로 무영향), `BUY_SCORE_THRESHOLD` 12→13, 시뮬 레짐 필터 바이패스 제거(F&G 바이패스는 유지). pullback 가점 모드는 전 구간 음수로 기각 |

## VPS 인프라

- Oracle Cloud ap-osaka-1 / VM.Standard.E2.1.Micro / Ubuntu 22.04
- `upbit-bot.service` (systemd, 자동 재시작)
- Tailscale 대시보드: `http://100.92.237.11:5000`
- SSH 키: `C:\Users\jso84\.ssh\upbit-vps-key`
- Swap 2GB (`/swapfile`) — OOM 방지
- Claude Code v2.1.177 설치됨 — Remote Control 지원

## 타임존

모든 `datetime.now()` 호출은 `datetime.now(config.KST)` 사용 (KST = UTC+9).
