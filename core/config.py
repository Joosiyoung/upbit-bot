import os
from datetime import timezone, timedelta
from dotenv import load_dotenv

# 한국 표준시 (UTC+9) — datetime.now(KST) 로 사용
KST = timezone(timedelta(hours=9))

# .env는 프로젝트 루트에 위치 (이 파일은 core/ 안에 있으므로 상위 폴더 기준)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

# Upbit API 키
ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")

# 1회 매수 금액 (KRW)
TRADE_AMOUNT_KRW = float(os.getenv("TRADE_AMOUNT_KRW", "100000"))

# 손절/익절 설정 (2026-06-22 알트 유니버스 재튜닝: 3→4 / 5→6, draft/coin_strategy_review_2026-06-22.md)
MAX_LOSS_PERCENT = float(os.getenv("MAX_LOSS_PERCENT", "4.0"))
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "6.0"))

# 전략 선택: rsi / macd / bollinger / combined
STRATEGY = os.getenv("STRATEGY", "combined")

# 캔들 단위 (표시용)
INTERVAL = os.getenv("INTERVAL", "minute60")

# 실제 매매 여부
LIVE_TRADING = os.getenv("LIVE_TRADING", "False").lower() == "true"

# 자동 매매 파라미터
MAX_POSITIONS     = int(os.getenv("MAX_POSITIONS",     "5"))    # 최대 동시 보유 종목 수
MIN_ORDER_KRW     = float(os.getenv("MIN_ORDER_KRW",   "5000")) # 업비트 최소 주문 금액 (원)
SELL_COOLDOWN_MIN = int(os.getenv("SELL_COOLDOWN_MIN", "30"))   # 매도 후 재매수 금지 시간 (분)

# ─── 리스크 관리 ───
DAILY_LOSS_LIMIT_PCT     = float(os.getenv("DAILY_LOSS_LIMIT_PCT",     "5.0"))  # 일일 실현손실 한도 (% of 당일 시작 자산) → 초과 시 당일 신규 매수 차단
MAX_CONSECUTIVE_STOPLOSS = int(os.getenv("MAX_CONSECUTIVE_STOPLOSS",   "3"))    # 연속 손절 허용 횟수 → 초과 시 전역 매수 쿨다운
GLOBAL_BUY_COOLDOWN_MIN  = int(os.getenv("GLOBAL_BUY_COOLDOWN_MIN",    "120"))  # 연속 손절 후 전역 매수 금지 시간 (분)
MAX_HOLD_HOURS           = float(os.getenv("MAX_HOLD_HOURS",           "48"))   # 최대 보유 시간 (시간) → 초과 시 청산 (time-stop)
MARKET_STALE_SEC         = int(os.getenv("MARKET_STALE_SEC",           "180"))  # 시장 캐시 신선도 한계 (초) → 초과 시 신규 매수 차단

# ─── 트레일링 스탑 ───
# 2026-06-22: 트레일링이 +5% 익절 도달을 100% 차단(승자 조기절단) → 9999로 OFF (주식 모듈과 동일 방식, 로직 무변경)
TRAILING_START_PCT = float(os.getenv("TRAILING_START_PCT", "9999"))  # 최고 수익률이 이 값 이상이면 트레일링 활성화 (%). 9999=비활성
TRAILING_STOP_PCT  = float(os.getenv("TRAILING_STOP_PCT",  "1.5"))  # 고점 대비 하락폭 한도 (%) → 초과 시 청산 (트레일링 OFF로 미사용)

# ─── 추가매수 (Phase 2.5) ───
ADD_BUY_MIN_PROFIT   = float(os.getenv("ADD_BUY_MIN_PROFIT",   "0.0"))  # 추가매수 허용 최소 수익률 (%) — 손실 중 물타기 방지
ADD_BUY_MAX_PROFIT   = float(os.getenv("ADD_BUY_MAX_PROFIT",   "3.0"))  # 추가매수 허용 최대 수익률 (%) — 추격매수 방지
MAX_SLOTS_PER_TICKER = int(os.getenv("MAX_SLOTS_PER_TICKER",   "2"))    # 한 종목이 점유 가능한 최대 슬롯 수

# ─── 매수 실패 백오프 ───
BUY_FAIL_LIMIT        = int(os.getenv("BUY_FAIL_LIMIT",        "3"))   # 연속 매수 실패 허용 횟수
BUY_FAIL_COOLDOWN_MIN = int(os.getenv("BUY_FAIL_COOLDOWN_MIN", "30"))  # 실패 한도 초과 시 해당 종목 매수 금지 시간 (분)

# ─── 진입 디바운스 (리페인팅 방지) ───
# 신규 매수 진입에 필요한 연속 매매 사이클 수. 1=즉시 진입(현행). 2+면 score≥임계치가
# 연속 N사이클 유지돼야 진입 → 미완성 캔들의 순간 스파이크 진입을 거른다.
# 주의: scripts/backtest.py 측정 결과 현 룰에서는 2+가 개선이 없어 기본 1로 둔다.
BUY_CONFIRM_TICKS = int(os.getenv("BUY_CONFIRM_TICKS", "1"))

# ─── 진입 점수 임계치 ───
# 가중 종합점수(일봉×2.5 + 1h×2 + 1m×0.5)가 이 값 이상이면 '강한 매수' 후보로 진입.
# 진입 신호 재설계(추세정렬형) + 시장 레짐 필터와 함께 backtest.py로 검증한 값:
#   365일 구간(Buy&Hold -39%)에서 임계치 12 + 레짐필터 = 거래당 기대값 +0.15%(양수).
# 구버전 역추세 룰의 기본값은 8이었으나, 재설계 룰에서는 12가 양의 기대값 구간이다.
BUY_SCORE_THRESHOLD = float(os.getenv("BUY_SCORE_THRESHOLD", "12"))

# ─── 시장 레짐 필터 ───
# True면 시장 프록시(BTC) 일봉 추세가 하락(ema_short < ema_mid)일 때 전 종목 신규 매수를
# 차단한다. 하락장에서 롱 진입을 막아 기대값을 양으로 전환하는 핵심 장치(backtest 검증).
MARKET_REGIME_FILTER = os.getenv("MARKET_REGIME_FILTER", "True").lower() == "true"
MARKET_REGIME_PROXY  = os.getenv("MARKET_REGIME_PROXY", "KRW-BTC")

# ─── 포지션 사이징 ───
# True: 코인당 매수금액을 (총자산 / MAX_POSITIONS)로 제한해 단일 코인 집중을 방지.
#       (빈 슬롯이 1개 남아도 잔고 전액을 한 코인에 몰지 않음)
# False: 종전 방식 — 잔고 / 빈 슬롯 수로 균등 분할(마지막 슬롯이면 전액).
EQUAL_WEIGHT_SIZING = os.getenv("EQUAL_WEIGHT_SIZING", "True").lower() == "true"

# ─── 추가매수 (Phase 2.5) on/off ───
# 추가매수 자체를 끄는 스위치. 백테스트로 기대값을 검증하기 전 비활성화하고 싶을 때 사용.
# 기본 True(현행 동작 유지).
ADD_BUY_ENABLED = os.getenv("ADD_BUY_ENABLED", "True").lower() == "true"

# ─── 로그 보존 ───
# 운영 로그(logs/bot.log)를 자정마다 일 단위로 회전하고 최근 N일치를 보관한다.
# 종전 크기 기반(1MB×4)은 로그량에 따라 보존 기간이 며칠로 들쭉날쭉했다. log-analyzer를
# 주/월 단위로 돌려도 그 기간 운영 로그가 남아 있도록 일수 기반으로 전환(기본 35일 = 월 주기 커버).
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "35"))

# 거래 이력(data/trade_history.jsonl) 보존 일수. 하루 1회 이보다 오래된 레코드를 정리한다.
# 레코드가 작아(~180B) 1년이면 수 MB 수준 — 분석엔 시장 한 사이클을 덮어 충분.
# 0으로 두면 영구 보존(정리 안 함).
TRADE_HISTORY_RETENTION_DAYS = int(os.getenv("TRADE_HISTORY_RETENTION_DAYS", "365"))

# ─── 스테이블코인 (공용) ───
# 거래량 상위에 포함되더라도 추세 매매 불가 → 유니버스 스캔·신규 매수 후보에서 제외.
# data_builder.py(유니버스 필터)와 trader.py(TRADING_BLACKLIST)가 공유하는 단일 소스.
STABLE_COINS = {
    "KRW-USDT", "KRW-USDC", "KRW-DAI", "KRW-BUSD", "KRW-TUSD",
    "KRW-USDP", "KRW-USDS", "KRW-FDUSD",
}

# 기술적 지표 파라미터
RSI_PERIOD = 14

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0

# 이동평균선
EMA_SHORT = 9
EMA_MID = 21
EMA_LONG = 50

# ─── 대시보드 바인딩 ───
# 로컬 PC: 기본값 127.0.0.1 유지.
# VPS: Tailscale IP(100.x.x.x)를 지정하면 tailnet 기기에서만 접속 가능 (공개 노출 방지).
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))

# ─── 대시보드 API 토큰 ───
# 상태 변경 엔드포인트(/api/trading/start, /api/trading/stop, /api/refresh) 인증용.
# 빈 문자열이면 경고만 출력하고 통과 (로컬 개발 편의). VPS에는 반드시 설정 권장.
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

# ─── Telegram 알림 ───
# @BotFather로 봇 생성 후 토큰 발급, 채팅 ID는 @userinfobot 등으로 확인.
# 둘 다 설정돼야 알림이 활성화됨 (미설정 시 알림 기능 전체 비활성).
TELEGRAM_BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_STOCK_BOT_TOKEN = os.getenv("TELEGRAM_STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "")
# TELEGRAM_STOCK_CHAT_ID는 별도로 두지 않음 — TELEGRAM_CHAT_ID와 동일하게 사용

# Fear & Greed 설정
FEAR_GREED_GREED_MAX = int(os.getenv("FEAR_GREED_GREED_MAX", "80"))  # 이 이상이면 극단 탐욕 → 신규 매수 차단
FEAR_GREED_FEAR_MIN  = int(os.getenv("FEAR_GREED_FEAR_MIN",  "20"))  # 이 이하면 극단 공포(폭락장) → 신규 매수 차단

# ─── KIS API (한국투자증권) ───
KIS_IS_SANDBOX       = os.getenv("KIS_IS_SANDBOX", "True").lower() != "false"

# 모의투자 자격증명
KIS_APP_KEY_SANDBOX    = os.getenv("KIS_APP_KEY_SANDBOX",    "")
KIS_APP_SECRET_SANDBOX = os.getenv("KIS_APP_SECRET_SANDBOX", "")
KIS_ACCOUNT_NO_SANDBOX = os.getenv("KIS_ACCOUNT_NO_SANDBOX", "")

# 실거래 자격증명
KIS_APP_KEY_REAL    = os.getenv("KIS_APP_KEY_REAL",    "")
KIS_APP_SECRET_REAL = os.getenv("KIS_APP_SECRET_REAL", "")
KIS_ACCOUNT_NO_REAL = os.getenv("KIS_ACCOUNT_NO_REAL", "")

# KIS_IS_SANDBOX에 따라 활성 자격증명 선택
KIS_APP_KEY    = KIS_APP_KEY_SANDBOX    if KIS_IS_SANDBOX else KIS_APP_KEY_REAL
KIS_APP_SECRET = KIS_APP_SECRET_SANDBOX if KIS_IS_SANDBOX else KIS_APP_SECRET_REAL
KIS_ACCOUNT_NO = KIS_ACCOUNT_NO_SANDBOX if KIS_IS_SANDBOX else KIS_ACCOUNT_NO_REAL

# ─── 주식 봇 파라미터 ───
STOCK_MAX_POSITIONS        = int(os.getenv("STOCK_MAX_POSITIONS",        "5"))
STOCK_BUY_SCORE_THRESHOLD  = float(os.getenv("STOCK_BUY_SCORE_THRESHOLD", "12"))
STOCK_MAX_LOSS_PERCENT     = float(os.getenv("STOCK_MAX_LOSS_PERCENT",    "5.0"))   # 3년 KOSPI 백테스트 재튜닝(수익최대형): 3.0→5.0
STOCK_TAKE_PROFIT_PERCENT  = float(os.getenv("STOCK_TAKE_PROFIT_PERCENT", "5.0"))
# 트레일링 비활성: 백테스트상 트레일링이 이익을 일찍 끊어 손해 → 도달 불가능 값으로 OFF
STOCK_TRAILING_START_PCT   = float(os.getenv("STOCK_TRAILING_START_PCT",  "9999"))
STOCK_TRAILING_STOP_PCT    = float(os.getenv("STOCK_TRAILING_STOP_PCT",   "1.5"))
STOCK_MAX_HOLD_DAYS        = int(os.getenv("STOCK_MAX_HOLD_DAYS",         "20"))    # 재튜닝(수익최대형): 5→20
STOCK_DAILY_LOSS_LIMIT_PCT = float(os.getenv("STOCK_DAILY_LOSS_LIMIT_PCT","5.0"))
STOCK_FEE_RATE             = float(os.getenv("STOCK_FEE_RATE",            "0.003"))
STOCK_BUY_CLOSE_TIME       = os.getenv("STOCK_BUY_CLOSE_TIME",           "15:20")
STOCK_REGIME_FILTER        = os.getenv("STOCK_REGIME_FILTER", "True").lower() != "false"
STOCK_MAX_PRICE             = int(os.getenv("STOCK_MAX_PRICE", "200000"))
STOCK_ADD_BUY_ENABLED       = os.getenv("STOCK_ADD_BUY_ENABLED", "True").lower() == "true"
STOCK_ADD_BUY_MIN_PROFIT    = float(os.getenv("STOCK_ADD_BUY_MIN_PROFIT", "0.0"))   # 손실 중 물타기 방지
STOCK_ADD_BUY_MAX_PROFIT    = float(os.getenv("STOCK_ADD_BUY_MAX_PROFIT", "2.0"))   # 추격매수 방지 (주식은 2%)
STOCK_MAX_SLOTS_PER_TICKER  = int(os.getenv("STOCK_MAX_SLOTS_PER_TICKER", "2"))      # 종목당 최대 슬롯
STOCK_ENTRY_CHANGE_MAX  = float(os.getenv("STOCK_ENTRY_CHANGE_MAX",  "2.0"))   # 당일 등락률 상한 (%)
STOCK_ENTRY_CHANGE_MIN  = float(os.getenv("STOCK_ENTRY_CHANGE_MIN",  "-3.0"))  # 당일 등락률 하한 (%)
STOCK_SIM_BUDGET = int(os.getenv("STOCK_SIM_BUDGET", "5000000"))  # 주식 시뮬 초기 예산 (KRW)

# ─── Google Sheets 연동 ───
GOOGLE_SHEETS_ID       = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_KEY_FILE = os.getenv("GOOGLE_SHEETS_KEY_FILE", "warm-alliance-279008-58e85ac09da1.json")

