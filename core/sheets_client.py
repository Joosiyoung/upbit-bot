import json
import logging
import os

from core import config

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUFFER_FILE = os.path.join(_ROOT_DIR, "data", "sheets_buffer.jsonl")

_COIN_HEADERS = [
    "date", "time", "type", "ticker", "reason", "price", "amount_krw",
    "profit_pct", "live", "threshold", "tp_pct", "sl_pct",
    "trailing_start_pct", "trailing_stop_pct", "max_hold_hours",
]
_STOCK_HEADERS = [
    "date", "ts", "type", "code", "name", "reason", "price", "qty",
    "amount_krw", "profit_pct", "threshold", "tp_pct", "sl_pct",
    "trailing_start_pct", "trailing_stop_pct", "max_hold_days",
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
        except Exception as e:
            logging.warning("Sheets: 초기화 실패 — %s", e)

    def _get_or_create_sheet(self, title: str, headers: list) -> object | None:
        if title in self._worksheets:
            return self._worksheets[title]
        try:
            try:
                ws = self._spreadsheet.worksheet(title)
            except Exception:
                ws = self._spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
                ws.append_row(headers, value_input_option="USER_ENTERED")
            self._worksheets[title] = ws
            return ws
        except Exception as e:
            logging.warning("Sheets: 시트 접근 실패 (%s) — %s", title, e)
            return None

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
                headers = _COIN_HEADERS if sheet_title == "코인" else _STOCK_HEADERS
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
        headers = _COIN_HEADERS if sheet_title == "코인" else _STOCK_HEADERS
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
