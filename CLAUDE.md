# Upbit 자동매매 봇

Flask + pyupbit 기반 코인 자동매매 + KIS API 기반 국내주식 시뮬 봇. 대시보드(웹), Telegram 원격 제어, Oracle Cloud VPS 24시간 운영.

## 실행 명령어

```bash
python app.py                                      # 로컬 실행
pip install -r requirements.txt                    # 의존성 설치

# 백테스트
python scripts/backtest.py --days 365             # 코인 전체
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
  indicators.py         # RSI, MACD, 볼린저, EMA
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
  backtest.py           # 코인 백테스터
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
| `BUY_SCORE_THRESHOLD` | 12 | 진입 점수 임계치 (365일 백테스트: +0.15% 기대값) |
| `MARKET_REGIME_FILTER` | True | BTC EMA 하락 시 전 종목 매수 차단 |
| `MAX_LOSS_PERCENT` | 4.0% | 손절 (2026-06-22 알트 유니버스 재튜닝: 3.0→4.0) |
| `TAKE_PROFIT_PERCENT` | 6.0% | 익절 (2026-06-22 재튜닝: 5.0→6.0, 알트 변동성에 넓은 타깃) |
| `TRAILING_START_PCT` | 9999 | **트레일링 비활성** (트레일링이 +5% 익절 도달을 100% 차단·승자 조기절단 → 9999로 OFF, 주식 모듈과 동일 방식) |
| `TRAILING_STOP_PCT` | 1.5% | 고점 대비 하락 한도 (트레일링 OFF로 미사용) |
| `MAX_HOLD_HOURS` | 48h | time-stop |
| `DAILY_LOSS_LIMIT_PCT` | 5.0% | 일일 손실 한도 초과 시 당일 매수 차단 |
| `EQUAL_WEIGHT_SIZING` | True | 종목당 금액을 (총자산÷MAX_POSITIONS)로 상한 |

### 주식 (config.py STOCK_*)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `STOCK_BUY_SCORE_THRESHOLD` | 12 | 진입 점수 임계치 (일봉 × 2.5 가중치) |
| `STOCK_MAX_LOSS_PERCENT` | 5.0% | 손절 (3년 KOSPI 백테스트 재튜닝: 3.0→5.0, ⚠️ 2026-07-02 페이징 버그 수정 전 ~5개월 데이터 기반 — 재검증 필요) |
| `STOCK_TAKE_PROFIT_PERCENT` | 5.0% | 익절 |
| `STOCK_TRAILING_START_PCT` | 9999 | **트레일링 비활성** (백테스트상 이익 조기 절단으로 손해 → 도달 불가능 값으로 OFF) |
| `STOCK_TRAILING_STOP_PCT` | 1.5% | 고점 대비 하락 한도 (트레일링 OFF로 미사용) |
| `STOCK_MAX_HOLD_DAYS` | 20 | time-stop (영업일 기준, 재튜닝: 5→20, ⚠️ 2026-07-02 페이징 버그 수정 전 ~5개월 데이터 기반 — 재검증 필요) |
| `STOCK_MAX_PRICE` | 200,000 | 1주 가격 상한 (투자금÷슬롯 기준) |
| `STOCK_BUY_CLOSE_TIME` | 15:20 | 신규 매수 마감 시각 |
| `STOCK_ENTRY_CHANGE_MAX` | +2.0% | 당일 등락률 상한 — 갭업 추격 차단 |
| `STOCK_ENTRY_CHANGE_MIN` | -3.0% | 당일 등락률 하한 — 폭락 종목 진입 차단 |
| `STOCK_ADD_BUY_ENABLED` | True | 추가매수 활성화 여부 |
| `STOCK_ADD_BUY_MIN_PROFIT` | 0.0% | 추가매수 허용 최소 수익률 — 손실 중 물타기 방지 |
| `STOCK_ADD_BUY_MAX_PROFIT` | 2.0% | 추가매수 허용 최대 수익률 — 추격매수 방지 |
| `STOCK_MAX_SLOTS_PER_TICKER` | 2 | 종목당 최대 슬롯 수 (추가매수 상한) |

> **미국주식 모듈 제거됨 (2026-06-17)**: 3년 백테스트 결과 미국 일봉 스윙은 환전 스프레드 포함 왕복 2.5% 수수료로 72조합 전부 손실 → `us_trader.py`·`us_universe.py`·`US_*` 파라미터·미국 Telegram 명령 전면 삭제. 같은 신호가 국내(왕복 0.6%)에선 흑자라 국내 전용으로 롤백. 상세는 메모리 `project_us_vs_kr_backtest` 참조.

## 매수 차단 게이트

**코인** (순서)
1. 일일 손실 한도 / 연속 손절 쿨다운
2. 시장 레짐 필터 (BTC 단기EMA < 중기EMA) — 시뮬 바이패스
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
- **`.gitignore` 수정** — PowerShell `echo >>` 금지 (UTF-16 인코딩). Write 도구 사용.
- **주식 시뮬 시작** — 장 외 시간 호출 시 즉시 거부. 재시작 시 기존 포지션·잔고 유지(`_stock_positions.clear()` 없음).
- **시뮬 모드 목적** — 레짐 필터·F&G 바이패스는 데이터 축적 목적. 진입 점수 임계치(12)는 유지.
- **VPS git push** — SSH 키 인증(`~/.ssh/id_ed25519`)으로 해결됨(2026-06-16). `git push origin main` VPS에서 직접 가능.
- **`_stock_sold_today` 비지속** — 서비스 재시작 시 초기화됨. 당일 매도 이력은 메모리에만 보관. 의도적 설계(재시작 후 당일 재진입은 허용).
- **`_daily_signal_cache` 단일 워커 전용** — `_stock_worker` 외 다른 스레드에서 접근 금지. 락 없이 설계됨.
- **`settings.json` VPS 미동기화 (의도적)** — Windows 전용 경로·명령(taskkill, powershell, cmd.exe 등) 포함. `.claude/*` gitignore로 제외됨. VPS는 Remote Control 최초 실행 시 자체 Linux 설정 자동 생성.
- **`build_stock_status_msg` 현재가 조회** — `get_current_price()`는 예외를 던지지 않고 None 반환. `try/except` 사용 금지. `if not price: price = entry_price` 패턴 사용. None 반환 시 진입가를 fallback으로 표시하고 `(진입가)` suffix 추가.
- **KIS sandbox 상시 500 오류 종목** — `universe.py`에서 KB금융(105560) 제거됨. KIS sandbox 환경에서 해당 종목 조회 시 항상 500 에러 반환. 이후 2026-06-29 방어주 19종 → 추세섹터 18종으로 전면 교체.
- **Sheets "주식" 시트** — 국내주식 16컬럼(`_STOCK_HEADERS`)으로 운영. 코인·국내주식 2개 시트 + 테이블정의서. 국내주식 Sheets 로깅 활성(`trader._log_trade`).
- **주식 봇 Telegram 명령** — 현재 9개 활성 (`/start_sim`, `/stop`, `/status`, `/perf`, `/positions`, `/history`, `/params`, `/market`, `/help`). 모두 국내주식 전용.
- **트레일링 비활성 방식** — `STOCK_TRAILING_START_PCT=9999`로 트레일링 분기가 절대 발동하지 않게 설정만으로 OFF (trader.py 로직 무변경). 재활성화 시 값을 정상 범위(예: 3.0)로 되돌리면 됨.
- **`_stock_market_notifier` 기동** — `app.py`에서 국내주식 장 시작(09:00) 알림 스레드 기동. 시뮬 상태와 무관하게 항상 동작. 2026-06-24부터 장 시작 감지 시 `start_stock_trading()` 자동 호출 포함 (이미 실행 중이면 skip).
- **프로젝트 서브에이전트 미등록 가능성** — 일부 런타임 세션(예: VPS Remote Control)에서 `.claude/agents/`의 coder·tester·pm·deployer 등이 Agent 툴 `subagent_type`으로 로드되지 않을 수 있다(빌트인만 노출). 정의 파일·설정은 정상인데 세션 레벨 미등록 문제. `'coder' not found. Available agents: claude, ...` 에러로 나타남. 복구: 프로젝트 디렉터리에서 세션 재시작. 그래도 안 되면 메인이 coder→tester→pm→deployer 역할을 직접 대행(구현+구문/임포트/스모크 검사+체크리스트+배포)한다(2026-06-17 실제 발생). `/restart_claude` Telegram 명령으로 폰에서 세션 재시작 가능(2026-06-22).
- **`ANTHROPIC_API_KEY` 프롬프트 차단** — `.env`에 `ANTHROPIC_API_KEY`가 환경에 있으면 새 `claude --remote-control` 기동 시 "이 키 사용할까요?" 대화형 프롬프트에서 멈춰 리모트 컨트롤이 뜨지 않는다. 해결: `restart-claude.sh` 기동 명령에 `unset ANTHROPIC_API_KEY` 포함(Remote Control은 Claude Pro 로그인으로 동작, API 키 불필요). 한 번 "No" 응답이 `~/.claude.json` `customApiKeyResponses.rejected`에 영구저장됨.
- **deploy/*.sh 실행권한 git 추적** — Windows에서 커밋 시 `.sh` 실행 비트가 유실되어 리포에 100644로 저장됨. VPS에서 pull마다 +x가 사라져 watchdog cron(직접 실행)이 `Permission denied`로 silent fail. 해결: `git update-index --chmod=+x deploy/restart-claude.sh deploy/claude-remote-watchdog.sh`로 100755 기록 → pull해도 +x 유지.
- **claude-remote 세션 운영** — `deploy/claude-remote-watchdog.sh`가 cron(`@reboot` + `*/5 * * * *`)으로 세션 자동복구. `deploy/restart-claude.sh`는 폰 `/restart_claude` Telegram 명령으로 호출. 봇은 `upbit-bot.service` 별도 프로세스라 claude-remote 세션 종료와 무관하게 동작. 단 세션 *내부* 자기재시작은 자기참조로 불가(setsid 분리 필요) — `restart-claude.sh`는 봇이 아닌 외부 Telegram 명령을 통해 호출되므로 문제 없음.
- **`TRADING_BLACKLIST` 이원적 의미** — 매수 차단(신규·추가매수)에는 `PERSONAL_HOLDINGS_BLACKLIST`(XRP·CRO·RVN) ∪ `config.STABLE_COINS`(8종) 합집합 11종이 전부 적용되지만, 청산(Phase 1)에서는 개인 보유분만 무조건 스킵하고 스테이블코인은 `bot_bought=True`(봇이 실수로 매수한 경우)면 청산을 허용한다. 두 세트를 하나로 취급해 청산 로직까지 스킵하면 봇이 실수로 산 스테이블코인이 영구 동결된다 (2026-07-02 USDT 3,388만원 동결 버그로 발견, `trader.py` Phase 1 분기 참조).
- **`_sync_live_positions` 라이브 모드 잠재 리스크** — 실거래(`live:true`) 전환 시, 이 함수가 잔고 동기화 과정에서 블랙리스트 제외 종목을 "매도됨"으로 오인해 봇이 보유 중인 스테이블코인 포지션을 추적 상태에서 조용히 지울 수 있다(Phase 1 청산 로직 도달 전에 상태가 사라짐). 현재 `live:false`라 비활성 상태이지만, 라이브 전환 전 반드시 검토·수정 필요 (2026-07-02 핫픽스 검토 중 tester·pm 공동 발견, 미수정 상태로 이월).
- **KIS 일봉/분봉 API 응답은 내림차순(최신순)** — `inquire-daily-itemchartprice` 등 KIS OHLCV 응답은 날짜가 최신→과거 순으로 내려온다. 페이징 코드가 오름차순을 가정해 `df.index[0]`을 "가장 오래된 날짜"로 쓰면 실제로는 최신 날짜가 되어, 다음 페이지 `end_date`가 하루씩만 뒤로 이동하는 버그가 발생한다(하루치 중복 조회 반복 → 유효 조회 기간이 요청한 `count`와 무관하게 100~110행으로 고정). `df.index.min()`으로 명시적으로 최솟값을 구해야 한다. 향후 KIS 페이징 로직을 추가/수정할 때 반드시 응답 정렬 순서를 먼저 확인할 것 (2026-07-02 `kis_client.py` `_get_daily`/`_get_minute`에서 발견·수정).

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
| 2026-06-16 | 주식 | fix(stock): `build_stock_status_msg` 현재가 None fallback·평단가 수수료 이중반영·astimezone naive 분기·중복 KIS API 호출·락 일관성 수정 6건 |
| 2026-06-16 | 주식 | universe: KB금융(105560) 제거 (KIS sandbox 상시 500 오류 종목) → 19종목 |
| 2026-06-16 | 인프라 | Google Sheets 실시간 거래 적재 (`core/sheets_client.py`) — 코인/주식 2개 시트, 네트워크 오류 시 `data/sheets_buffer.jsonl` 버퍼링, sell 레코드에 전략 파라미터 임베드 |
| 2026-06-16 | 인프라 | feat(sheets): `sheets_client.py` 전면 재작성 — 기존 JSONL 자동 마이그레이션(`_migrate_historical`), 컬럼명 전체 대문자 통일, DATE+TIME 분리, "📋 테이블정의서" 시트 자동 생성 |
| 2026-06-16 | 인프라 | 미국주식 KIS API 검증 (코드 변경 없음) — `/uapi/overseas-price/v1/` 경로, OHLCV TR `HHDFS76240000`, 현재가 TR `HHDFS00000300`, 소수점 주문 TR `TTTT1007U/1008U` 응답 정상 확인 |
| 2026-06-16 | 인프라 | draft/us_stock_implementation_plan.md 작성 — 미국 소수점 거래로 코인·KOSPI 자금 스케일 미스매치 해결 방안 |
| 2026-06-16 | 미국주식 | feat(us-stock): 미국주식 시뮬 모듈 신규 구축 (`core/stock/us_trader.py`, `core/stock/us_universe.py`) — KRW 기준 예산, 편도 1.25% 수수료, 매수 시 환율 기록(`entry_exchange_rate`), JSONL+Sheets에 `EXCHANGE_RATE`/`AMOUNT_KRW` 컬럼 추가 |
| 2026-06-16 | 인프라 | refactor(sheets/telegram): 국내주식 Sheets 로깅 비활성, 주식 봇 Telegram 명령 13개 → 4개 (`/us_start_sim`, `/us_stop`, `/us_status`, `/help`) — 미국주식 전용 운영 전환 |
| 2026-06-16 | 인프라 | fix(sheets): "주식" 시트 재시작 시 `clear()` 후 `_US_STOCK_HEADERS` 18컬럼으로 재초기화, 테이블정의서 레이블 "주식" 통일 |
| 2026-06-17 | 미국주식 | feat(us-stock): KIS 거래량순위 기반 동적 유니버스 (`HHDFS76310010`) — NAS/NYS 각 50종목 조회, 당일 캐시, fallback 19종목 고정 (`us_universe.py`, `kis_client.py`) |
| 2026-06-17 | 미국주식 | fix(us-stock): 유니버스 스캔 대상 NAS+NYS 각 50→10 (총 20종목) — KIS OHLCV API 과부하 해소, MAX_POSITIONS=5 기준 충분 |
| 2026-06-17 | 미국주식 | fix(us-stock): fallback 캐시 하루 고정→30분 TTL 변경 (`us_universe.py`), `/us_status` 포지션 현재가 워커 캐시 `last_price` 우선 사용 (KIS 직렬 호출 제거) |
| 2026-06-17 | 미국주식 | fix(us-stock): KRW 기준 수익률(`ret_pct_krw`) 추가 — 매수·매도 환율 모두 반영, Telegram 알림 KRW%/USD% 병기, Sheets `PROFIT_PCT_KRW` 컬럼 추가 |
| 2026-06-17 | 미국주식 | fix(us-stock): 장 외 시작 거부(`start_us_sim()` ok=False 반환), `_us_sold_today` 초기화 기준 KST 자정→05:00(장마감)으로 변경, `_load_us_state()` entry_time 파싱 실패 시 now() fallback |
| 2026-06-17 | 인프라 | feat(telegram): 코인봇 `/universe` 추가 (스캔 코인 목록·점수), 미국주식봇 명령 4→9개 확장 (`/us_perf`, `/us_history`, `/us_params`, `/us_universe`, `/us_market`) |
| 2026-06-17 | 인프라 | fix(telegram): `/us_start_sim` 장 외 시간 호출 시 '현재 장 외 시간, 개장 시 자동 시작' 안내 메시지 추가 |
| 2026-06-17 | 인프라 | fix(app): 국내주식 장 시작 알림 스레드(`_stock_market_notifier`) 무조건 기동 코드 제거 — 미국주식 전용 운영 전환 반영 |
| 2026-06-17 | 검증 | 3시장 yfinance 백테스트 — 미국 일봉 스윙 3년×72조합 전부 손실(왕복 수수료 2.5%), 국내 KOSPI는 48/72 흑자, 코인은 29/72(현 라이브 파라미터는 3시장 모두 손실). 메모리 `project_us_vs_kr_backtest` |
| 2026-06-17 | 미국주식 | **refactor: 미국주식 전면 제거** — `us_trader.py`·`us_universe.py`·kis_client 해외 메서드·`US_*` config·`/api/us/*` 라우트·미국 Telegram 명령 9개 삭제 (수수료 구조상 수익 불가 확정) |
| 2026-06-17 | 주식 | **국내 복귀** — `_stock_market_notifier` 기동 복구, 국내 Sheets 로깅 복구("주식" 시트 16컬럼), 주식봇 Telegram 국내 명령 9개 복구 |
| 2026-06-17 | 주식 | **파라미터 재튜닝(KOSPI 3년 수익최대형)** — 손절 3→5%, 보유 5→20일, 트레일링 OFF(`STOCK_TRAILING_START_PCT=9999`), 익절 5% 유지 |
| 2026-06-17 | 인프라 | 서브에이전트(`.claude/agents/`)가 이 세션 Agent 툴에 미등록 → coder/tester/pm/deployer 호출 불가. 정의·설정 정상, 세션 레벨 문제. 메인이 파이프라인 전 단계 직접 대행하여 배포 완료 (gotcha 추가) |
| 2026-06-22 | 코인 | **파라미터 재튜닝(알트 유니버스 적합)** — 익절 5→6%, 손절 3→4%, 트레일링 OFF(`TRAILING_START_PCT=9999`), 임계치 12·보유 48h 유지. 라이브 로그(6/12~6/22, 140건) 분석 결과 봇이 매매하는 알트 유니버스가 백테스트 검증 대상(대형주)과 불일치 → 실제 알트 10종 백테스트(90일+12일 교차검증)에서 TP6/SL4/trailOFF가 두 레짐 모두 우위(12일 기대값 +0.075%→+0.271%). 트레일링은 +5% 익절 도달을 100% 차단(승자 조기절단)하여 OFF. 상세 `draft/coin_strategy_review_2026-06-22.md` |
| 2026-06-22 | 인프라 | **폰 Remote Control 운영 체계 구축** — `/restart_claude` Telegram 명령 신설(코인봇, 2단계 `/confirm` 패턴, chat_id 인증). `deploy/restart-claude.sh`(세션 kill+재기동, `unset ANTHROPIC_API_KEY` 포함)·`deploy/claude-remote-watchdog.sh`(멱등 기동, cron `@reboot`+`*/5`) 신규. `.sh` 실행권한 `git update-index --chmod=+x`로 100755 git 추적. 코인봇 명령 12→13개 |
| 2026-06-24 | 주식 | feat(stock): 장 자동 시작 — `_stock_market_notifier`에서 09:00 장 시작 감지 시 `start_stock_trading()` 자동 호출. 이미 실행 중이면 skip, 실패 시 Telegram 알림. 수동 `/start_sim` 불필요 |
| 2026-06-24 | 주식 | feat(stock): 진입 스캔 진단 로깅 — 스캔 시작(슬롯/예산/종목 수), 종목별 스킵 이유(점수미달 INFO / 가격초과 DEBUG / 등락률 INFO / 수량0 INFO / 비용초과 INFO), 스캔 완료(N종목 중 M건 매수) 로깅 추가 |
| 2026-06-25 | 주식 | 장애 분석 — KIS 거래량순위 API 404 상시 발생, FALLBACK_UNIVERSE(19종목) 폴백 정상 동작. 매수 0건 원인: FALLBACK_UNIVERSE 종목 전체 점수 미달(최고 신한지주 +7.5, 임계치 12). KOSPI 약세장에서 방어주 유니버스가 임계치 미달 구조 확인 |
| 2026-06-25 | 코인 | 손익 현황 점검 — 2026-06-12~06-25 176건 청산. 승률 44.9%(79승 97패), 순손익 -9,223,117원. 손절(79건) : 익절(33건) = 2.4:1. 총 평가액 95,678,218원 / 초기 100M → -4.32% |
| 2026-06-25 | 인프라 | Upbit API Rate Limit 변경 확인 (2026-06-25): 계정 단위 → 포켓 단위. 단일 포켓 운영 중인 봇에는 영향 없음 |
| 2026-06-28 | 코인 | **주말 전략 분석(log-analyzer)**: 6/12~6/25 212건 청산 분석. 승률 46.2%, EV -0.418%. 손절 102 : 익절 41 = 2.5:1. USDT 14회 슬롯 점유 버그 발견 |
| 2026-06-28 | 코인 | **스테이블코인 제외**: `data_builder.py` `_STABLE_COINS` 필터 + `trader.py` `TRADING_BLACKLIST` 확장 (USDT·USDC 등 8종) 배포 (`8df38d1`) |
| 2026-06-28 | 코인 | **param-optimizer**: 90~180일 96조합 스윕 → 전 조합 기대값 음수. 현 파라미터(TP6/SL4/임계치12) 유지. 신호 자체 재설계 필요 판단 |
| 2026-06-28 | 주식 | **stock-log-analyzer**: FALLBACK_UNIVERSE(방어주 19종) 추세추종 전략과 구조적 미스매치 확인. 전 종목 임계치 12 미달 (최고 신한지주 7.5) |
| 2026-06-28 | 주식 | **stock-param-optimizer**: 90일 스윕에서 삼성전자·SK하이닉스 등 추세섹터 종목 일부 양수 기대값 확인 |
| 2026-06-29 | 주식 | **유니버스 교체 + 예산 상향**: `FALLBACK_UNIVERSE` 방어주 19종 → 추세섹터(반도체·2차전지·방산·자동차·바이오·플랫폼·조선·철강·엔터·반도체장비) 18종. `STOCK_SIM_BUDGET` 100만 → 500만원 배포 (`f3973de`) |
| 2026-06-29 | 인프라 | **app.py 재시작 복원 버그 수정**: `enabled:true` + 포지션 없음 상태로 재시작 시 워커 직접기동 → 주말 스캔·예산 미초기화 문제. 포지션 없으면 `enabled:false` 리셋 후 장 시작 시 notifier가 정상 초기화하도록 수정 배포 (`40072c9`) |
| 2026-07-02 | 코인 | **USDT 3,388만원 영구 동결 버그 수정**: `TRADING_BLACKLIST`(개인보유+스테이블코인 통합 11종)를 청산(Phase 1) 스킵에도 그대로 적용해 봇이 실수로 매수한 USDT 포지션이 영원히 청산 불가 상태였음. `PERSONAL_HOLDINGS_BLACKLIST`(XRP·CRO·RVN)와 `config.STABLE_COINS`(8종)로 분리해 매수 차단은 기존과 동일(11종 유지)하되, 청산은 개인보유만 무조건 스킵·스테이블코인은 `bot_bought=True`면 허용하도록 수정. 추가매수(Phase 2.5) 사이징도 `slot_count` 누적을 반영해 종목당 자산상한(EQUAL_WEIGHT_SIZING)을 정확히 캡핑하도록 수정. 분석·검증에 각 에이전트(qa·bot-enhancer·strategy-researcher·stock-strategy-researcher) 의견 종합 후 우선순위 재정리 → coder→tester→pm 파이프라인으로 수정 |
| 2026-07-02 | 코인 | **섀도우 로그 추가**: 매수 시점 게이트 상태(`regime_ok`·`fg_block`·`atr_pct`·`pullback_pct`·`bb_pct`)를 매매 판단에는 반영하지 않고 로그(`shadow` 필드)로만 기록 시작. `core/indicators.py`에 ATR·고점대비낙폭 계산 추가. 목적: 실전 데이터로 향후 신호 재설계 임계값을 실측 검증(과적합 방지). `draft/coin_signal_redesign_spec_2026-07-02.md` 참조 |
| 2026-07-02 | 코인 | **신호 재설계 실험 2건 백테스트 — 운영 미반영**: strategy-researcher 스펙 기반 BB 부호반전(flip)·BB 제거(off)·ATR 하한게이트를 `scripts/backtest.py` 전용 플래그(`--bb-mode`, `--min-atr`)로 구현해 90일/180일 교차검증. `off` 모드가 두 기간 모두 방향성 개선(-0.284%→-0.075%, -0.404%→-0.208%)이나 채택기준(90+180일 모두 양(+) EV) 미달로 `core/analysis.py` 미반영. ATR 게이트는 1h/1d 지표에 동일 임계 적용되는 설계 결함(confound)으로 무효 처리 — 재설계 필요. 운영 코드 변경 없음(dev-tool 스크립트만 추가) |
| 2026-07-02 | 주식 | **KIS 일봉/분봉 페이징 버그 수정**: `kis_client.py` `_get_daily`/`_get_minute`에서 `oldest = df.index[0]` → `df.index.min()`. KIS 응답이 내림차순(최신순)인데 오름차순으로 가정해 페이지당 하루치만 중복 이동 → 과거 "3년 KOSPI 백테스트"가 실제로는 ~5개월(100~110행)치 데이터로만 수행됐음이 드러남. 수정 후 `count=1095` 요청 시 2022-01-07~2026-07-02 정상 반환 확인(실거래 API로 직접 검증). `STOCK_MAX_LOSS_PERCENT`·`STOCK_MAX_HOLD_DAYS` 파라미터 표에 재검증 필요 캐비어트 추가 |
| 2026-07-02 | 품질 | **코드 정리**: 도달 불가능한 죽은 코드 제거(`stock/trader.py`), Windows 레거시 파일 3종 삭제(`restart.bat`·`scripts/restart_helper.py`·`scripts/start_server.py`, README 보안 표의 관련 항목도 함께 제거), 오해성 주석 정정(`data_builder.py`/`trader.py` — 코인 두 점수함수의 타임프레임 가중치 차이가 "진입 스캔 1h+1m, 보유평가 4h+1h"임을 명확화). 위 4건(USDT 동결·섀도우로그·신호재설계실험·KIS페이징)과 함께 단일 커밋(`d15c06a`)으로 통합 배포 완료 — VPS 재시작 확인(2026-07-02 11:13 UTC), 이후 정상 가동 중 |

## VPS 인프라

- Oracle Cloud ap-osaka-1 / VM.Standard.E2.1.Micro / Ubuntu 22.04
- `upbit-bot.service` (systemd, 자동 재시작)
- Tailscale 대시보드: `http://100.92.237.11:5000`
- SSH 키: `C:\Users\jso84\.ssh\upbit-vps-key`
- Swap 2GB (`/swapfile`) — OOM 방지
- Claude Code v2.1.177 설치됨 — Remote Control 지원

## 타임존

모든 `datetime.now()` 호출은 `datetime.now(config.KST)` 사용 (KST = UTC+9).
