"""프로젝트 루트를 import 경로에 추가 (scripts/backtest.py와 동일 방식).

또한 테스트가 외부 서비스를 건드리지 않도록 차단한다:
core.trader 임포트 → sheets_client 모듈이 import 시점에 Google Sheets에 연결하는데,
GOOGLE_SHEETS_ID를 비워 비활성화한다. (load_dotenv는 기존 환경변수를 덮어쓰지
않으므로 core.config 임포트 전에 설정해야 한다 — conftest가 가장 먼저 실행됨)
"""

import os
import sys

os.environ.setdefault("GOOGLE_SHEETS_ID", "")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_STOCK_BOT_TOKEN", "")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
