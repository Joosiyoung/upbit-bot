# 미국주식 동적 유니버스 — KIS 거래량순위 기반, 장 외 시간에는 고정 fallback
import logging
import threading
from datetime import datetime

from core import config

_KST = config.KST
_log = logging.getLogger(__name__)

# 고정 fallback 리스트 (KIS OHLCV 응답 확인 종목 위주)
_FALLBACK_UNIVERSE = [
    ("AAPL",  "Apple",           "NAS"),
    ("MSFT",  "Microsoft",       "NAS"),
    ("GOOGL", "Alphabet",        "NAS"),
    ("AMZN",  "Amazon",          "NAS"),
    ("NVDA",  "NVIDIA",          "NAS"),
    ("META",  "Meta",            "NAS"),
    ("TSLA",  "Tesla",           "NAS"),
    ("AMD",   "AMD",             "NAS"),
    ("INTC",  "Intel",           "NAS"),
    ("AVGO",  "Broadcom",        "NAS"),
    ("QCOM",  "Qualcomm",        "NAS"),
    ("ADBE",  "Adobe",           "NAS"),
    ("COST",  "Costco",          "NAS"),
    ("PYPL",  "PayPal",          "NAS"),
    ("JPM",   "JPMorgan",        "NYS"),
    ("BAC",   "Bank of America", "NYS"),
    ("MA",    "Mastercard",      "NYS"),
    ("V",     "Visa",            "NYS"),
    ("WMT",   "Walmart",         "NYS"),
]

_lock = threading.Lock()
_cached_universe: list[tuple[str, str, str]] = []
_cache_date: str = ""   # "YYYYMMDD" — 당일 날짜와 다르면 재조회


def _is_cache_valid() -> bool:
    today = datetime.now(_KST).strftime("%Y%m%d")
    return _cache_date == today and len(_cached_universe) > 0


def refresh_us_universe(kis_client=None) -> list[tuple[str, str, str]]:
    """KIS 거래량순위 API로 당일 유니버스 갱신.

    kis_client: KisClient 인스턴스 (None이면 fallback 반환, 캐시 갱신 없음)
    반환: [(symbol, name, excd), ...] 최대 100개
    """
    global _cached_universe, _cache_date

    if kis_client is None:
        _log.warning("KisClient 미전달 — fallback 유니버스 사용")
        return list(_FALLBACK_UNIVERSE)

    today = datetime.now(_KST).strftime("%Y%m%d")

    try:
        nas = kis_client.get_us_top_volume_stocks("NAS", limit=50, min_vol="4", min_prc="5")
        nys = kis_client.get_us_top_volume_stocks("NYS", limit=50, min_vol="4", min_prc="5")
        combined = nas + nys
        if not combined:
            _log.warning("거래량순위 API 결과 없음 — fallback 유니버스로 당일 캐시")
            with _lock:
                _cached_universe = list(_FALLBACK_UNIVERSE)
                _cache_date = today
            return list(_FALLBACK_UNIVERSE)

        # 중복 심볼 제거 (순서 유지)
        seen: set[str] = set()
        deduped: list[tuple[str, str, str]] = []
        for item in combined:
            if item[0] not in seen:
                seen.add(item[0])
                deduped.append(item)

        with _lock:
            _cached_universe = deduped
            _cache_date = today

        _log.info("미국주식 유니버스 갱신 완료: %d종목 (NAS %d + NYS %d)", len(deduped), len(nas), len(nys))
        return deduped

    except Exception as e:
        _log.warning("유니버스 갱신 실패: %s — fallback으로 당일 캐시", e)
        with _lock:
            _cached_universe = list(_FALLBACK_UNIVERSE)
            _cache_date = today
        return list(_FALLBACK_UNIVERSE)


def get_us_universe(kis_client=None) -> list[tuple[str, str, str]]:
    """당일 유니버스 반환. 캐시 유효 시 재사용, 만료 시 갱신.

    kis_client: KisClient 인스턴스 (None이면 캐시 또는 fallback)
    """
    with _lock:
        if _is_cache_valid():
            return list(_cached_universe)

    return refresh_us_universe(kis_client)
