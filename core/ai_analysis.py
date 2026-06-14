# ─────────────────────────────────────────────
# AI 분석 — Fear & Greed + 업비트 마켓 체크
# (뉴스/Haiku 없음: F&G와 상장폐지 감지만 담당)
# ─────────────────────────────────────────────

import logging
import threading
import time
import requests
from datetime import datetime, timedelta

_ai_lock  = threading.Lock()
_ai_cache = {}
# 구조:
#   "_fear_greed"   → {value: int, classification: str, cached_at: datetime}
#   "_upbit_active" → {markets: set, cached_at: datetime}
#   "_updated_at"   → str

AI_CHECK_INTERVAL    = 300   # 업비트 마켓 체크 주기 (5분)
AI_FG_INTERVAL_HOURS = 1     # Fear & Greed 갱신 주기 (시간)

_last_fg_ts: datetime | None = None


# ─────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────

def _fetch_fear_greed() -> dict | None:
    """Alternative.me Fear & Greed Index 조회 (API 키 불필요, 무료)"""
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=5)
        data = resp.json()["data"][0]
        value = int(data["value"])
        if not (0 <= value <= 100):
            logging.warning("Fear&Greed 값 범위 오류: %d (0~100 기대)", value)
            return None
        return {
            "value":          value,
            "classification": data["value_classification"],
            "cached_at":      datetime.now(),
        }
    except Exception as e:
        logging.warning("Fear&Greed 조회 실패: %s", e)
        return None


_UPBIT_MARKETS_MIN = 20   # 이 미만이면 비정상 응답으로 간주


def _check_upbit_active_markets() -> set:
    """업비트 활성 KRW 마켓 목록 조회. 비정상 응답(20개 미만)이면 빈 집합 반환."""
    try:
        resp = requests.get("https://api.upbit.com/v1/market/all", timeout=5)
        markets = {
            item["market"]
            for item in resp.json()
            if item["market"].startswith("KRW-")
        }
        if len(markets) < _UPBIT_MARKETS_MIN:
            logging.warning("업비트 마켓 목록 비정상 (%d개 < 최소 %d개) — 캐시 유지",
                            len(markets), _UPBIT_MARKETS_MIN)
            return set()
        return markets
    except Exception as e:
        logging.warning("업비트 마켓 조회 실패: %s", e)
        return set()


# ─────────────────────────────────────────────
# trader.py에서 사용하는 헬퍼
# ─────────────────────────────────────────────

def get_active_markets() -> set:
    """업비트 활성 KRW 마켓 집합. 알 수 없으면 빈 집합 반환."""
    with _ai_lock:
        entry = _ai_cache.get("_upbit_active")
    if not entry:
        return set()
    return entry.get("markets", set())


def get_fear_greed() -> dict | None:
    """Fear & Greed 캐시 반환. 없으면 None."""
    with _ai_lock:
        return _ai_cache.get("_fear_greed")


# ─────────────────────────────────────────────
# 백그라운드 워커
# ─────────────────────────────────────────────

def ai_worker():
    global _last_fg_ts

    # 기동 직후 즉시 1회 수행 (첫 5분간 상장폐지 감지·F&G 필터 공백 방지)
    while True:
        try:
            # ── 1. 업비트 활성 마켓 체크 (매 5분) ──
            active = _check_upbit_active_markets()
            if active:
                with _ai_lock:
                    _ai_cache["_upbit_active"] = {
                        "markets":   active,
                        "cached_at": datetime.now(),
                    }

            # ── 2. Fear & Greed (1시간마다, 실패 시 다음 사이클 재시도) ──
            now = datetime.now()
            if _last_fg_ts is None or (now - _last_fg_ts) >= timedelta(hours=AI_FG_INTERVAL_HOURS):
                fg = _fetch_fear_greed()
                if fg:
                    with _ai_lock:
                        _ai_cache["_fear_greed"] = fg
                        _ai_cache["_updated_at"] = now.strftime("%H:%M:%S")
                    _last_fg_ts = now   # 성공 시에만 갱신 → 실패하면 5분 뒤 재시도

        except Exception as e:
            logging.warning("AI 워커 오류: %s", e)

        time.sleep(AI_CHECK_INTERVAL)  # 5분 대기
