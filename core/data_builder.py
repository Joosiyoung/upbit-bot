# ─────────────────────────────────────────────
# 데이터 빌더 — 캐시, 보유 코인 분석, 시장 분석
# ─────────────────────────────────────────────

import logging
import threading
import time
import requests as _req
from datetime import datetime, timezone
import pyupbit

from core.upbit_client import UpbitClient
from core.indicators import add_all_indicators, get_latest_indicators
from core import config
from core.analysis import (
    score_signal, action_from_score, support_resistance,
    rsi_label, rsi_class, macd_label, macd_class,
    bb_label, bb_class, trend_label, trend_class,
)

# ─────────────────────────────────────────────
# 캐시
# ─────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache = {"status": "initializing", "updated_at": None, "holdings": [], "krw_balance": 0}

# 대표 코인 시장 캐시
_market_lock = threading.Lock()
_market_cache = {"status": "initializing", "updated_at": None, "coins": []}

MARKET_COINS = [
    ("KRW-BTC",  "비트코인"),
    ("KRW-ETH",  "이더리움"),
    ("KRW-SOL",  "솔라나"),
    ("KRW-XRP",  "리플"),
    ("KRW-DOGE", "도지코인"),
    ("KRW-ADA",  "에이다"),
    ("KRW-AVAX", "아발란체"),
    ("KRW-DOT",  "폴카닷"),
    ("KRW-LINK", "체인링크"),
    ("KRW-ATOM", "코스모스"),
    ("KRW-LTC",  "라이트코인"),
    ("KRW-BCH",  "비트코인캐시"),
    ("KRW-ETC",  "이더리움클래식"),
    ("KRW-TRX",  "트론"),
    ("KRW-NEAR", "니어프로토콜"),
    ("KRW-SAND", "샌드박스"),
    ("KRW-MANA", "디센트럴랜드"),
    ("KRW-ALGO", "알고랜드"),
    ("KRW-POL",  "폴리곤"),
    ("KRW-FIL",  "파일코인"),
]

INTERVALS = [
    ("day",       "일봉"),
    ("minute240", "4시간봉"),
    ("minute60",  "1시간봉"),
]

# 데이터 없을 때 사용하는 중립 지표 기본값 (오류 전파 방지)
def _neutral_indicators() -> dict:
    return {
        'rsi': 50.0, 'macd': 0.0, 'macd_signal': 0.0, 'macd_hist': 0.0,
        'prev_macd_hist': 0.0, 'bb_upper': 0.0, 'bb_mid': 0.0, 'bb_lower': 0.0,
        'bb_pct': 0.5, 'bb_width': 0.0,
        'ema_short': 1.0, 'ema_mid': 1.0, 'ema_long': 1.0,
        'volume_ratio': 1.0, 'stoch_k': 50.0, 'stoch_d': 50.0,
        'price_change_pct': 0.0, 'close': 0.0, 'volume': 0.0,
    }


def build_analysis_data():
    client = UpbitClient()
    if not client.upbit:
        raise RuntimeError("API 키가 설정되지 않았습니다.")

    balances = client.upbit.get_balances()
    krw_balance = client.get_balance_krw()
    holdings_result = []
    total_eval = 0.0

    for b in balances:
        currency = b['currency']
        volume   = float(b['balance'])
        if currency == 'KRW' or volume <= 0:
            continue

        ticker    = f"KRW-{currency}"
        avg_price = float(b.get('avg_buy_price', 0))
        current   = client.get_current_price(ticker)
        if not current:
            continue

        eval_value  = volume * current
        profit_pct  = (current - avg_price) / avg_price * 100 if avg_price > 0 else 0
        total_eval += eval_value

        timeframes = {}
        scores = {}

        for interval, label in INTERVALS:
            df = client.get_ohlcv(ticker, interval, 200)
            if df.empty:
                continue
            df = add_all_indicators(df)
            ind = get_latest_indicators(df)
            sup, res = support_resistance(df)
            sc = score_signal(ind)
            scores[interval] = sc

            timeframes[interval] = {
                "label":        label,
                "score":        sc,
                "rsi":          round(ind['rsi'], 1),
                "rsi_label":    rsi_label(ind['rsi']),
                "rsi_class":    rsi_class(ind['rsi']),
                "rsi_pct":      round(ind['rsi'], 1),
                "macd_label":   macd_label(ind['macd_hist'], ind['prev_macd_hist']),
                "macd_class":   macd_class(ind['macd_hist'], ind['prev_macd_hist']),
                "bb_label":     bb_label(ind['bb_pct']),
                "bb_class":     bb_class(ind['bb_pct']),
                "bb_pct":       round(ind['bb_pct'] * 100, 1),
                "trend_label":  trend_label(ind['ema_short'], ind['ema_mid'], ind['ema_long']),
                "trend_class":  trend_class(ind['ema_short'], ind['ema_mid'], ind['ema_long']),
                "stoch_k":      round(ind['stoch_k'], 1),
                "volume_ratio": round(ind['volume_ratio'], 2),
                "support":      sup,
                "resistance":   res,
                "bb_lower":     round(ind['bb_lower'], 2),
                "bb_mid":       round(ind['bb_mid'], 2),
                "bb_upper":     round(ind['bb_upper'], 2),
                "current":      current,
            }
            time.sleep(0.1)  # API rate limit 방어

        # 가중 합산 점수
        total_score = (
            scores.get('day', 0) * 2 +
            scores.get('minute240', 0) * 1.5 +
            scores.get('minute60', 0) * 1
        )
        action_text, action_class = action_from_score(total_score)

        # 매수/매도 가이드 (일봉 기준)
        day_tf = timeframes.get('day', {})
        buy_points  = []
        sell_points = []
        if day_tf:
            bl = day_tf['bb_lower']
            bm = day_tf['bb_mid']
            bu = day_tf['bb_upper']
            buy_points  = [
                f"볼린저 하단 {bl:,.0f}원 — 강한 지지선",
                f"볼린저 중단 {bm:,.0f}원 — 돌파 시 추가 매수",
            ]
            sell_points = [
                f"볼린저 중단 {bm:,.0f}원 — 1차 저항, 부분 매도",
                f"볼린저 상단 {bu:,.0f}원 — 목표가, 익절",
            ]
            if day_tf.get('rsi', 50) < 35:
                buy_points.insert(0, f"RSI 과매도({day_tf['rsi']:.1f}) — 현재가 분할 매수 고려")
            if day_tf.get('macd_class') in ('strong-buy',):
                buy_points.insert(0, "MACD 골든크로스 — 매수 진입 신호")
            if day_tf.get('macd_class') in ('strong-sell',):
                sell_points.insert(0, "MACD 데드크로스 — 매도 진입 신호")

        holdings_result.append({
            "ticker":       ticker,
            "coin":         currency,
            "volume":       volume,
            "avg_price":    avg_price,
            "current_price": current,
            "eval_value":   eval_value,
            "profit_pct":   round(profit_pct, 2),
            "total_score":  round(total_score, 1),
            "action_text":  action_text,
            "action_class": action_class,
            "timeframes":   timeframes,
            "guide": {
                "buy_points":  buy_points,
                "sell_points": sell_points,
            },
        })

    return {
        "status":      "ok",
        "updated_at":  datetime.now(timezone.utc).isoformat(),
        "krw_balance": krw_balance,
        "total_eval":  total_eval,
        "holdings":    holdings_result,
        "strategy":    config.STRATEGY.upper(),
        "interval":    config.INTERVAL,
    }


def get_top_volume_tickers(held_tickers: set, limit: int = 20) -> list[tuple[str, str]]:
    """업비트 KRW 마켓 24시간 거래대금 상위 코인 (보유 코인 제외)"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        if not tickers:
            raise ValueError("티커 목록 조회 실패")

        # 업비트 REST API: 티커 목록 전체를 한 번에 조회 (acc_trade_price_24h 포함)
        chunk_size = 100
        ticker_data = []
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            resp = _req.get(
                "https://api.upbit.com/v1/ticker",
                params={"markets": ",".join(chunk)},
                timeout=5,
            )
            if resp.status_code == 200:
                ticker_data.extend(resp.json())
            time.sleep(0.1)

        # 24시간 거래대금 기준 정렬, 보유 코인 제외
        ticker_data.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)

        result = []
        for item in ticker_data:
            market = item["market"]
            if market in held_tickers:
                continue
            coin = market.replace("KRW-", "")
            result.append((market, coin))
            if len(result) >= limit:
                break
        return result

    except Exception:
        # 실패 시 하드코딩 목록 폴백
        return [(t, t.replace("KRW-", "")) for t, _ in MARKET_COINS if t not in held_tickers][:limit]


def build_market_data():
    """업비트 KRW 거래량 상위 20개 코인 분석 (BLACKLIST 제외)"""
    # BLACKLIST는 trader.py에서 정의되나, 순환 import 방지를 위해 직접 import
    from core.trader import TRADING_BLACKLIST

    client = UpbitClient()
    coins_result = []

    # BLACKLIST 코인만 제외 (그 외 보유 코인은 분석 대상에 포함)
    target_coins = get_top_volume_tickers(TRADING_BLACKLIST, limit=20)

    for ticker, name in target_coins:
        try:
            # 1분봉 (최신 신호용) + 1시간봉 (추세용)
            df_1m  = client.get_ohlcv(ticker, "minute1",  count=100)
            df_1h  = client.get_ohlcv(ticker, "minute60", count=100)
            # EMA50(adjust=False)·MACD(26) 수렴에 봉 수가 충분해야 함 — 60개로는 시드 편향 잔존
            df_1d  = client.get_ohlcv(ticker, "day",      count=200)

            current = client.get_current_price(ticker, warn=False)
            if not current or df_1m.empty:
                continue

            df_1m = add_all_indicators(df_1m)
            ind_1m = get_latest_indicators(df_1m)

            # 1h/1d 데이터가 없을 때 1m 데이터로 대체하지 않고 중립값 사용
            # (다른 타임프레임 데이터를 잘못된 봉 단위 지표로 사용하는 것 방지)
            if not df_1h.empty:
                df_1h = add_all_indicators(df_1h)
                ind_1h = get_latest_indicators(df_1h)
            else:
                ind_1h = _neutral_indicators()

            if not df_1d.empty:
                df_1d = add_all_indicators(df_1d)
                ind_1d = get_latest_indicators(df_1d)
            else:
                ind_1d = _neutral_indicators()

            # 1분봉 기준 종합 점수
            sc_1m = score_signal(ind_1m)
            sc_1h = score_signal(ind_1h)
            sc_1d = score_signal(ind_1d)
            total = sc_1d * 2.5 + sc_1h * 2 + sc_1m * 0.5

            action_text, action_class = action_from_score(total)

            # 24h 가격 변화 (일봉 기준)
            change_24h = ind_1d.get('price_change_pct', 0)

            # 볼린저 밴드 (1시간봉 기준 지지/저항)
            sup_1h, res_1h = support_resistance(df_1h) if not df_1h.empty else (0, 0)

            coins_result.append({
                "ticker":       ticker,
                "coin":         ticker.replace("KRW-", ""),
                "name":         name,
                "current":      current,
                "change_24h":   round(change_24h, 2),
                "total_score":  round(total, 1),
                "action_text":  action_text,
                "action_class": action_class,
                # 1분봉 지표
                "rsi_1m":        round(ind_1m['rsi'], 1),
                "rsi_class_1m":  rsi_class(ind_1m['rsi']),
                "rsi_label_1m":  rsi_label(ind_1m['rsi']),
                "macd_label_1m": macd_label(ind_1m['macd_hist'], ind_1m['prev_macd_hist']),
                "macd_class_1m": macd_class(ind_1m['macd_hist'], ind_1m['prev_macd_hist']),
                "bb_label_1m":   bb_label(ind_1m['bb_pct']),
                "bb_class_1m":   bb_class(ind_1m['bb_pct']),
                "bb_pct_1m":     round(ind_1m['bb_pct'] * 100, 1),
                # 1시간봉 지표
                "rsi_1h":        round(ind_1h['rsi'], 1),
                "rsi_class_1h":  rsi_class(ind_1h['rsi']),
                "trend_1h":      trend_label(ind_1h['ema_short'], ind_1h['ema_mid'], ind_1h['ema_long']),
                "trend_class_1h":trend_class(ind_1h['ema_short'], ind_1h['ema_mid'], ind_1h['ema_long']),
                # 일봉 지표
                "rsi_1d":        round(ind_1d['rsi'], 1),
                "rsi_class_1d":  rsi_class(ind_1d['rsi']),
                "trend_1d":      trend_label(ind_1d['ema_short'], ind_1d['ema_mid'], ind_1d['ema_long']),
                "trend_class_1d":trend_class(ind_1d['ema_short'], ind_1d['ema_mid'], ind_1d['ema_long']),
                # 지지/저항 (1시간봉)
                "support":       round(sup_1h, 2),
                "resistance":    round(res_1h, 2),
                "bb_lower_1h":   round(ind_1h['bb_lower'], 2),
                "bb_upper_1h":   round(ind_1h['bb_upper'], 2),
                "volume_ratio":  round(ind_1m['volume_ratio'], 2),
            })
            time.sleep(0.15)

        except Exception as e:
            logging.warning("마켓 데이터 수집 실패 [%s]: %s", ticker, e)
            continue

    return {
        "status":     "ok",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "coins":      sorted(coins_result, key=lambda x: x["total_score"], reverse=True),
    }


def cache_worker():
    while True:
        try:
            data = build_analysis_data()
            with _cache_lock:
                _cache.update(data)
        except Exception as e:
            with _cache_lock:
                _cache["status"]    = "error"
                _cache["error_msg"] = str(e)
        time.sleep(30)
