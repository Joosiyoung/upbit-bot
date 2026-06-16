import json
import logging
import os

from core import config

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUFFER_FILE = os.path.join(_ROOT_DIR, "data", "sheets_buffer.jsonl")

_COIN_HEADERS = [
    "DATE", "TIME", "TYPE", "TICKER", "REASON", "PRICE", "AMOUNT_KRW",
    "PROFIT_PCT", "LIVE", "THRESHOLD", "TP_PCT", "SL_PCT",
    "TRAILING_START_PCT", "TRAILING_STOP_PCT", "MAX_HOLD_HOURS",
]
_STOCK_HEADERS = [
    "DATE", "TIME", "TYPE", "CODE", "NAME", "REASON", "PRICE", "QTY",
    "AMOUNT_KRW", "PROFIT_PCT", "THRESHOLD", "TP_PCT", "SL_PCT",
    "TRAILING_START_PCT", "TRAILING_STOP_PCT", "MAX_HOLD_DAYS",
]

_US_STOCK_HEADERS = [
    "DATE", "TIME", "TYPE", "SYMBOL", "NAME", "EXCHANGE", "PRICE_USD", "QTY",
    "AMOUNT_USD", "EXCHANGE_RATE", "AMOUNT_KRW", "PROFIT_PCT", "THRESHOLD", "TP_PCT", "SL_PCT",
    "TRAILING_START_PCT", "TRAILING_STOP_PCT", "MAX_HOLD_DAYS",
]

_DEFINITION_HEADERS = ["시트", "컬럼명", "한국어명", "설명", "데이터타입", "형식/예시"]
_DEFINITION_ROWS = [
    ["코인", "DATE",               "일자",           "거래 발생 일자",                          "DATE",    "YYYY-MM-DD / 2026-06-16"],
    ["코인", "TIME",               "시각",           "거래 발생 시각",                          "TIME",    "HH:MM:SS / 14:32:01"],
    ["코인", "TYPE",               "거래유형",       "매수/매도 구분",                          "TEXT",    "buy / sell"],
    ["코인", "TICKER",             "종목코드",       "Upbit 마켓 코드",                         "TEXT",    "KRW-BTC"],
    ["코인", "REASON",             "거래사유",       "진입·청산 사유 메시지",                   "TEXT",    "강한 매수 (점수 15.0)"],
    ["코인", "PRICE",              "체결가",         "거래 단가 (원)",                          "NUMBER",  "정수 / 92400000"],
    ["코인", "AMOUNT_KRW",         "거래금액",       "거래 금액 (원)",                          "NUMBER",  "정수 / 18208"],
    ["코인", "PROFIT_PCT",         "수익률",         "실현 수익률(%), 매수 시 공백",             "NUMBER",  "소수점2자리 / 1.23"],
    ["코인", "LIVE",               "실거래여부",     "True=실거래 False=시뮬",                  "BOOLEAN", "True / False"],
    ["코인", "THRESHOLD",          "진입임계치",     "매수 신호 점수 임계값",                   "NUMBER",  "정수 / 12"],
    ["코인", "TP_PCT",             "익절비율",       "익절 기준 수익률(%)",                     "NUMBER",  "소수점1자리 / 5.0"],
    ["코인", "SL_PCT",             "손절비율",       "손절 기준 손실률(%)",                     "NUMBER",  "소수점1자리 / 3.0"],
    ["코인", "TRAILING_START_PCT", "트레일링활성수익", "트레일링 스탑 활성화 수익률(%)",        "NUMBER",  "소수점1자리 / 3.0"],
    ["코인", "TRAILING_STOP_PCT",  "트레일링하락한도", "고점 대비 하락 허용 한도(%)",           "NUMBER",  "소수점1자리 / 1.5"],
    ["코인", "MAX_HOLD_HOURS",     "최대보유시간",   "타임스탑 기준 보유 시간(시)",             "NUMBER",  "정수 / 48"],
    [],  # 빈 행
    # ── 국내주식 정의 비활성 ──
    # ["주식", "DATE",               "일자",           "거래 발생 일자",                          "DATE",    "YYYY-MM-DD / 2026-06-16"],
    # ["주식", "TIME",               "시각",           "거래 발생 시각",                          "TIME",    "HH:MM:SS / 09:35:00"],
    # ["주식", "TYPE",               "거래유형",       "매수/매도 구분",                          "TEXT",    "buy / sell"],
    # ["주식", "CODE",               "종목코드",       "KRX 6자리 종목코드",                      "TEXT",    "005930"],
    # ["주식", "NAME",               "종목명",         "종목 한국어 명칭",                        "TEXT",    "삼성전자"],
    # ["주식", "REASON",             "거래사유",       "진입·청산 사유 메시지",                   "TEXT",    "강한 매수 (점수 15.0)"],
    # ["주식", "PRICE",              "체결가",         "거래 단가 (원)",                          "NUMBER",  "정수 / 75000"],
    # ["주식", "QTY",                "수량",           "거래 주식 수량(주)",                      "NUMBER",  "정수 / 10"],
    # ["주식", "AMOUNT_KRW",         "거래금액",       "거래 금액 (원)",                          "NUMBER",  "정수 / 750000"],
    # ["주식", "PROFIT_PCT",         "수익률",         "실현 수익률(%), 매수 시 공백",             "NUMBER",  "소수점2자리 / 1.23"],
    # ["주식", "THRESHOLD",          "진입임계치",     "매수 신호 점수 임계값",                   "NUMBER",  "정수 / 12"],
    # ["주식", "TP_PCT",             "익절비율",       "익절 기준 수익률(%)",                     "NUMBER",  "소수점1자리 / 5.0"],
    # ["주식", "SL_PCT",             "손절비율",       "손절 기준 손실률(%)",                     "NUMBER",  "소수점1자리 / 3.0"],
    # ["주식", "TRAILING_START_PCT", "트레일링활성수익", "트레일링 스탑 활성화 수익률(%)",        "NUMBER",  "소수점1자리 / 3.0"],
    # ["주식", "TRAILING_STOP_PCT",  "트레일링하락한도", "고점 대비 하락 허용 한도(%)",           "NUMBER",  "소수점1자리 / 1.5"],
    # ["주식", "MAX_HOLD_DAYS",      "최대보유일수",   "타임스탑 기준 보유 영업일(일)",           "NUMBER",  "정수 / 5"],
    [],  # 빈 행
    ["미국주식", "DATE",               "일자",           "거래 발생 일자",                          "DATE",    "YYYY-MM-DD / 2026-06-16"],
    ["미국주식", "TIME",               "시각",           "거래 발생 시각",                          "TIME",    "HH:MM:SS / 22:35:00"],
    ["미국주식", "TYPE",               "거래유형",       "매수/매도 구분",                          "TEXT",    "buy / sell"],
    ["미국주식", "SYMBOL",             "종목심볼",       "NASDAQ/NYSE 티커 심볼",                   "TEXT",    "AAPL"],
    ["미국주식", "NAME",               "종목명",         "종목 영문 명칭",                          "TEXT",    "Apple"],
    ["미국주식", "EXCHANGE",           "거래소",         "거래소 코드",                             "TEXT",    "NAS / NYS"],
    ["미국주식", "PRICE_USD",          "체결가(USD)",    "거래 단가 (달러)",                        "NUMBER",  "소수점4자리 / 192.5000"],
    ["미국주식", "QTY",                "수량",           "소수점 주식 수량",                        "NUMBER",  "소수점4자리 / 5.1948"],
    ["미국주식", "AMOUNT_USD",         "거래금액(USD)",  "거래 금액 (달러)",                        "NUMBER",  "소수점2자리 / 200.00"],
    ["미국주식", "EXCHANGE_RATE",      "환율(USD/KRW)", "매수/매도 시 적용 환율 (원/달러)",          "NUMBER",  "소수점2자리 / 1384.50"],
    ["미국주식", "AMOUNT_KRW",         "거래금액(KRW)", "거래 금액 (원화 환산)",                     "NUMBER",  "정수 / 276900"],
    ["미국주식", "PROFIT_PCT",         "수익률",         "실현 수익률(%), 매수 시 공백",             "NUMBER",  "소수점2자리 / 1.23"],
    ["미국주식", "THRESHOLD",          "진입임계치",     "매수 신호 점수 임계값",                   "NUMBER",  "정수 / 12"],
    ["미국주식", "TP_PCT",             "익절비율",       "익절 기준 수익률(%)",                     "NUMBER",  "소수점1자리 / 5.0"],
    ["미국주식", "SL_PCT",             "손절비율",       "손절 기준 손실률(%)",                     "NUMBER",  "소수점1자리 / 3.0"],
    ["미국주식", "TRAILING_START_PCT", "트레일링활성수익", "트레일링 스탑 활성화 수익률(%)",        "NUMBER",  "소수점1자리 / 3.0"],
    ["미국주식", "TRAILING_STOP_PCT",  "트레일링하락한도", "고점 대비 하락 허용 한도(%)",           "NUMBER",  "소수점1자리 / 1.5"],
    ["미국주식", "MAX_HOLD_DAYS",      "최대보유일수",   "타임스탑 기준 보유 캘린더 일(일)",        "NUMBER",  "정수 / 5"],
]


class SheetsClient:
    def __init__(self):
        self._gc = None
        self._spreadsheet = None
        self._worksheets: dict[str, object] = {}
        self._enabled = False
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            key_file = config.GOOGLE_SHEETS_KEY_FILE
            if not os.path.isabs(key_file):
                key_file = os.path.join(_ROOT_DIR, key_file)
            if not os.path.exists(key_file):
                logging.warning("Sheets: 키 파일 없음 (%s) — Sheets 비활성", key_file)
                return
            if not config.GOOGLE_SHEETS_ID:
                logging.warning("Sheets: GOOGLE_SHEETS_ID 미설정 — Sheets 비활성")
                return
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(key_file, scopes=scopes)
            self._gc = gspread.authorize(creds)
            self._spreadsheet = self._gc.open_by_key(config.GOOGLE_SHEETS_ID)
            self._enabled = True
            logging.info("Sheets: 인증 성공 (spreadsheet_id=%s)", config.GOOGLE_SHEETS_ID)
            self._create_definition_sheet()
            self._migrate_historical()
        except Exception as e:
            logging.warning("Sheets: 초기화 실패 — %s", e)

    def _get_or_create_sheet(self, title: str, headers: list) -> object | None:
        if title in self._worksheets:
            return self._worksheets[title]
        try:
            try:
                ws = self._spreadsheet.worksheet(title)
                # 헤더 업데이트 (대문자 전환)
                ws.update("A1", [headers])
            except Exception:
                ws = self._spreadsheet.add_worksheet(title=title, rows=5000, cols=len(headers))
                ws.append_row(headers, value_input_option="USER_ENTERED")
            self._worksheets[title] = ws
            return ws
        except Exception as e:
            logging.warning("Sheets: 시트 접근 실패 (%s) — %s", title, e)
            return None

    def _create_definition_sheet(self):
        title = "📋 테이블정의서"
        try:
            try:
                ws = self._spreadsheet.worksheet(title)
                ws.clear()
            except Exception:
                ws = self._spreadsheet.add_worksheet(title=title, rows=100, cols=6)
            ws.append_row(_DEFINITION_HEADERS, value_input_option="USER_ENTERED")
            ws.append_rows(_DEFINITION_ROWS, value_input_option="USER_ENTERED")
            logging.info("Sheets: 테이블정의서 시트 업데이트 완료")
        except Exception as e:
            logging.warning("Sheets: 테이블정의서 생성 실패 — %s", e)

    def _migrate_historical(self):
        coin_path = os.path.join(_ROOT_DIR, "data", "trade_history.jsonl")
        stock_path = os.path.join(_ROOT_DIR, "data", "stock_trade_history.jsonl")

        # 코인 마이그레이션
        try:
            ws = self._get_or_create_sheet("코인", _COIN_HEADERS)
            if ws is not None:
                existing = len(ws.get_all_values()) - 1  # 헤더 제외
                if existing > 0:
                    logging.info("Sheets: 코인 시트 기존 데이터 %d행 — 마이그레이션 스킵", existing)
                elif os.path.exists(coin_path):
                    rows = []
                    with open(coin_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                                if rec.get("type") not in ("buy", "sell"):
                                    continue
                                rows.append([
                                    rec.get("date", ""),
                                    rec.get("time", ""),
                                    rec.get("type", ""),
                                    rec.get("ticker", ""),
                                    rec.get("reason", ""),
                                    rec.get("price", ""),
                                    rec.get("amount", ""),
                                    rec.get("profit_pct", ""),
                                    rec.get("live", ""),
                                    rec.get("threshold", ""),
                                    rec.get("tp_pct", ""),
                                    rec.get("sl_pct", ""),
                                    rec.get("trailing_start_pct", ""),
                                    rec.get("trailing_stop_pct", ""),
                                    rec.get("max_hold_hours", ""),
                                ])
                            except Exception:
                                continue
                    if rows:
                        ws.append_rows(rows, value_input_option="USER_ENTERED")
                        logging.info("Sheets: 코인 히스토리 %d행 마이그레이션 완료", len(rows))
        except Exception as e:
            logging.warning("Sheets: 코인 마이그레이션 실패 — %s", e)

        # ── 국내주식 마이그레이션 비활성 (미국주식 전용으로 전환) ──────────────
        # try:
        #     ws = self._get_or_create_sheet("주식", _STOCK_HEADERS)
        #     if ws is not None:
        #         existing = len(ws.get_all_values()) - 1
        #         if existing > 0:
        #             logging.info("Sheets: 주식 시트 기존 데이터 %d행 — 마이그레이션 스킵", existing)
        #         elif os.path.exists(stock_path):
        #             rows = []
        #             with open(stock_path, encoding="utf-8") as f:
        #                 for line in f:
        #                     line = line.strip()
        #                     if not line:
        #                         continue
        #                     try:
        #                         rec = json.loads(line)
        #                         if rec.get("side") not in ("buy", "sell"):
        #                             continue
        #                         ts_val = rec.get("ts", "")
        #                         date_str = ts_val[:10] if ts_val else ""
        #                         time_str = ts_val[11:19] if len(ts_val) >= 19 else ""
        #                         amount_krw = rec.get("price", 0) * rec.get("quantity", 0)
        #                         rows.append([
        #                             date_str,
        #                             time_str,
        #                             rec.get("side", ""),
        #                             rec.get("code", ""),
        #                             rec.get("name", ""),
        #                             rec.get("reason", ""),
        #                             rec.get("price", ""),
        #                             rec.get("quantity", ""),
        #                             round(amount_krw),
        #                             rec.get("ret_pct", ""),
        #                             "",
        #                             "",
        #                             "",
        #                             "",
        #                             "",
        #                             "",
        #                         ])
        #                     except Exception:
        #                         continue
        #             if rows:
        #                 ws.append_rows(rows, value_input_option="USER_ENTERED")
        #                 logging.info("Sheets: 주식 히스토리 %d행 마이그레이션 완료", len(rows))
        # except Exception as e:
        #     logging.warning("Sheets: 주식 마이그레이션 실패 — %s", e)

    def _flush_buffer(self):
        if not self._enabled or not os.path.exists(_BUFFER_FILE):
            return
        try:
            with open(_BUFFER_FILE, encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
        except Exception as e:
            logging.warning("Sheets: 버퍼 읽기 실패 — %s", e)
            return

        if not lines:
            return

        remaining = []
        for line in lines:
            try:
                item = json.loads(line)
                sheet_title = item["sheet"]
                row = item["row"]
                if sheet_title == "코인":
                    headers = _COIN_HEADERS
                else:
                    headers = _US_STOCK_HEADERS
                ws = self._get_or_create_sheet(sheet_title, headers)
                if ws is None:
                    remaining.append(line)
                    continue
                ws.append_row(row, value_input_option="USER_ENTERED")
            except Exception as e:
                logging.warning("Sheets: 버퍼 재전송 실패 — %s", e)
                remaining.append(line)

        try:
            with open(_BUFFER_FILE, "w", encoding="utf-8") as f:
                for line in remaining:
                    f.write(line + "\n")
        except Exception as e:
            logging.warning("Sheets: 버퍼 재기록 실패 — %s", e)

    def _write_buffer(self, sheet_title: str, row: list):
        try:
            os.makedirs(os.path.dirname(_BUFFER_FILE), exist_ok=True)
            with open(_BUFFER_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"sheet": sheet_title, "row": row}, ensure_ascii=False) + "\n")
        except Exception as e:
            logging.warning("Sheets: 버퍼 쓰기 실패 — %s", e)

    def append(self, sheet_title: str, row: list):
        if not self._enabled:
            return
        self._flush_buffer()
        if sheet_title in ("코인",):
            headers = _COIN_HEADERS
        else:
            # "주식"과 "미국주식" 모두 US 헤더 사용
            headers = _US_STOCK_HEADERS
        try:
            ws = self._get_or_create_sheet(sheet_title, headers)
            if ws is None:
                self._write_buffer(sheet_title, row)
                return
            ws.append_row(row, value_input_option="USER_ENTERED")
        except Exception as e:
            logging.warning("Sheets: append 실패 (%s) — %s, 버퍼링", sheet_title, e)
            self._write_buffer(sheet_title, row)


try:
    _client = SheetsClient()
except Exception:
    _client = None


def get_client() -> SheetsClient | None:
    return _client
