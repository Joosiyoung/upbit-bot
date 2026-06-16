import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from core import config
from core.stock.kis_auth import KisAuth

_KST = config.KST

# KIS 일봉 응답 필드 → 표준 컬럼 매핑
_DAY_FIELD_MAP = {
    "stck_bsop_date": "date",
    "stck_oprc":      "open",
    "stck_hgpr":      "high",
    "stck_lwpr":      "low",
    "stck_clpr":      "close",
    "acml_vol":       "volume",
}

# KIS 분봉 응답 필드 → 표준 컬럼 매핑
_MIN_FIELD_MAP = {
    "stck_bsop_date": "date",
    "stck_cntg_hour": "time",
    "stck_oprc":      "open",
    "stck_hgpr":      "high",
    "stck_lwpr":      "low",
    "stck_prpr":      "close",
    "cntg_vol":       "volume",
}


class KisClient:
    def __init__(self):
        self._auth = KisAuth(config.KIS_APP_KEY_REAL, config.KIS_APP_SECRET_REAL, is_sandbox=False)

    def _headers(self, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {self._auth.get_token()}",
            "appkey":        config.KIS_APP_KEY_REAL,
            "appsecret":     config.KIS_APP_SECRET_REAL,
            "tr_id":         tr_id,
            "custtype":      "P",
            "Content-Type":  "application/json; charset=utf-8",
        }

    def _check_rt(self, data: dict):
        if data.get("rt_cd") != "0":
            msg = data.get("msg1") or data.get("msg") or str(data)
            raise ValueError(f"KIS API 오류: {msg}")

    # ── 일봉 ──────────────────────────────────────────────────────────────────

    def _fetch_daily_page(self, code: str, end_date: str) -> list[dict]:
        """KIS 일봉 단일 페이지 (최대 100일치)."""
        url    = f"{self._auth.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":          code,
            "FID_INPUT_DATE_1":        "19000101",   # 최대한 이전부터
            "FID_INPUT_DATE_2":        end_date,
            "FID_PERIOD_DIV_CODE":     "D",
            "FID_ORG_ADJ_PRC":         "0",
        }
        resp = requests.get(url, headers=self._headers("FHKST03010100"),
                            params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._check_rt(data)
        return data.get("output2") or []

    def _fetch_minute_page(self, code: str, interval: str, end_time: str) -> list[dict]:
        """KIS 분봉 단일 페이지 (최대 30개)."""
        mins = "60" if interval == "minute60" else "240"
        url  = f"{self._auth.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        params = {
            "FID_ETC_CLS_CODE":        "",
            "FID_COND_MRKT_DIV_CODE":  "J",
            "FID_INPUT_ISCD":           code,
            "FID_INPUT_HOUR_1":         end_time,   # HHMMSS
            "FID_PW_DATA_INCU_YN":      "Y",
        }
        # FID_TIME_DIVS: 분봉 단위 ("0060" = 60분, "0240" = 240분)
        params["FID_TIME_DIVS"] = f"{int(mins):04d}"
        resp = requests.get(url, headers=self._headers("FHKST03010200"),
                            params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._check_rt(data)
        return data.get("output2") or []

    def get_ohlcv(self, code: str, interval: str, count: int = 100) -> pd.DataFrame:
        """OHLCV DataFrame 반환.

        interval: "day" | "minute60" | "minute240"
        반환 컬럼: open, high, low, close, volume (float)
        인덱스: datetime (KST-aware, 오름차순)
        """
        if interval == "day":
            return self._get_daily(code, count)
        elif interval in ("minute60", "minute240"):
            return self._get_minute(code, interval, count)
        else:
            raise ValueError(f"지원하지 않는 interval: {interval}")

    def _get_daily(self, code: str, count: int) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        end_date = datetime.now(_KST).strftime("%Y%m%d")
        collected = 0

        while collected < count:
            rows = self._fetch_daily_page(code, end_date)
            if not rows:
                break
            df = self._parse_daily_rows(rows)
            if df.empty:
                break
            frames.append(df)
            collected += len(df)
            # 다음 페이지: 현재 배치의 가장 오래된 날짜 하루 전
            oldest = df.index[0]
            end_date = (oldest - timedelta(days=1)).strftime("%Y%m%d")
            if len(rows) < 100:
                break
            time.sleep(0.2)

        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames).sort_index()
        out = out[~out.index.duplicated(keep="first")]
        return out.tail(count)

    def _parse_daily_rows(self, rows: list[dict]) -> pd.DataFrame:
        records = []
        for r in rows:
            date_str = r.get("stck_bsop_date", "")
            if not date_str:
                continue
            try:
                dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=_KST)
            except ValueError:
                continue
            records.append({
                "datetime": dt,
                "open":     float(r.get("stck_oprc",  0) or 0),
                "high":     float(r.get("stck_hgpr",  0) or 0),
                "low":      float(r.get("stck_lwpr",  0) or 0),
                "close":    float(r.get("stck_clpr",  0) or 0),
                "volume":   float(r.get("acml_vol",   0) or 0),
            })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).set_index("datetime")
        df = df[df["close"] > 0]
        return df

    def _get_minute(self, code: str, interval: str, count: int) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        end_time = datetime.now(_KST).strftime("%H%M%S")
        collected = 0

        while collected < count:
            rows = self._fetch_minute_page(code, interval, end_time)
            if not rows:
                break
            df = self._parse_minute_rows(rows)
            if df.empty:
                break
            frames.append(df)
            collected += len(df)
            oldest = df.index[0]
            end_time = oldest.strftime("%H%M%S")
            if len(rows) < 30:
                break
            time.sleep(0.2)

        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames).sort_index()
        out = out[~out.index.duplicated(keep="first")]
        return out.tail(count)

    def _parse_minute_rows(self, rows: list[dict]) -> pd.DataFrame:
        records = []
        for r in rows:
            date_str = r.get("stck_bsop_date", "")
            time_str = r.get("stck_cntg_hour", "")
            if not date_str or not time_str:
                continue
            try:
                dt = datetime.strptime(f"{date_str}{time_str[:6]}", "%Y%m%d%H%M%S")
                dt = dt.replace(tzinfo=_KST)
            except ValueError:
                continue
            records.append({
                "datetime": dt,
                "open":   float(r.get("stck_oprc", 0) or 0),
                "high":   float(r.get("stck_hgpr", 0) or 0),
                "low":    float(r.get("stck_lwpr", 0) or 0),
                "close":  float(r.get("stck_prpr", 0) or 0),
                "volume": float(r.get("cntg_vol",  0) or 0),
            })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).set_index("datetime")
        df = df[df["close"] > 0]
        return df

    # ── 현재가 ────────────────────────────────────────────────────────────────

    def get_current_price(self, code: str) -> float | None:
        url = f"{self._auth.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":          code,
        }
        try:
            resp = requests.get(url, headers=self._headers("FHKST01010100"),
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self._check_rt(data)
            output = data.get("output") or {}
            return float(output.get("stck_prpr", 0) or 0)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("KIS 현재가 조회 실패 (%s): %s", code, e)
            return None

    def get_top_volume_stocks(self, limit: int = 60) -> list[tuple[str, str]]:
        """KOSPI 거래량 순위 상위 종목 조회.

        반환: [(종목코드, 종목명), ...] — limit개 이내
        실패 시 빈 리스트 반환.
        """
        import logging
        url = f"{self._auth.base_url}/uapi/domestic-stock/v1/ranking/volume"
        params = {
            "FID_COND_MRKT_DIV_CODE":  "J",
            "FID_COND_SCR_DIV_CODE":   "20171",
            "FID_INPUT_ISCD":           "0001",
            "FID_DIV_CLS_CODE":        "0",
            "FID_BLNG_CLS_CODE":       "0",
            "FID_TRGT_CLS_CODE":       "111111111",
            "FID_TRGT_EXLS_CLS_CODE":  "000000",
            "FID_INPUT_PRICE_1":       "",
            "FID_INPUT_PRICE_2":       "",
            "FID_VOL_CNT":             "",
            "FID_INPUT_DATE_1":        "",
        }
        try:
            resp = requests.get(url, headers=self._headers("FHPST01710000"),
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                logging.getLogger(__name__).warning(
                    "KIS 거래량 순위 API 오류: %s", data.get("msg1", "")
                )
                return []
            output = data.get("output") or []
            result = []
            for item in output[:limit]:
                code = item.get("mksc_shrn_iscd", "").strip()
                name = item.get("hts_kor_isnm", "").strip()
                if code and name:
                    result.append((code, name))
            return result
        except Exception as e:
            logging.getLogger(__name__).warning("KIS 거래량 순위 조회 실패: %s", e)
            return []
