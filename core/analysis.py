# ─────────────────────────────────────────────
# 순수 분석 함수 — 라벨·점수·지지저항
# ─────────────────────────────────────────────


def trend_label(ema_s, ema_m, ema_l):
    if ema_s > ema_m > ema_l:   return "상승 정배열"
    elif ema_s < ema_m < ema_l: return "하락 역배열"
    else:                        return "혼조"

def trend_class(ema_s, ema_m, ema_l):
    if ema_s > ema_m > ema_l:   return "bullish"
    elif ema_s < ema_m < ema_l: return "bearish"
    else:                        return "neutral"

def rsi_label(rsi):
    if rsi < 30:   return f"과매도 ({rsi:.1f})"
    elif rsi < 45: return f"약세 ({rsi:.1f})"
    elif rsi < 55: return f"중립 ({rsi:.1f})"
    elif rsi < 70: return f"강세 ({rsi:.1f})"
    else:          return f"과매수 ({rsi:.1f})"

def rsi_class(rsi):
    if rsi < 30:   return "oversold"
    elif rsi < 45: return "weak"
    elif rsi < 55: return "neutral"
    elif rsi < 70: return "strong"
    else:          return "overbought"

def macd_label(hist, prev_hist):
    if prev_hist < 0 and hist > 0:        return "골든크로스 발생! → 매수 신호"
    elif prev_hist > 0 and hist < 0:      return "데드크로스 발생! → 매도 신호"
    elif hist > 0 and hist > prev_hist:   return f"상승 모멘텀 강화"
    elif hist > 0 and hist < prev_hist:   return f"상승 모멘텀 약화"
    elif hist < 0 and hist < prev_hist:   return f"하락 모멘텀 강화"
    else:                                  return f"하락 모멘텀 약화 → 반등 준비"

def macd_class(hist, prev_hist):
    if prev_hist < 0 and hist > 0:  return "strong-buy"
    elif prev_hist > 0 and hist < 0: return "strong-sell"
    elif hist > 0:                   return "bullish"
    else:                            return "bearish"

def bb_label(bb_pct):
    if bb_pct <= 0.0:   return f"하단 이탈 → 강한 매수 구간"
    elif bb_pct <= 0.2: return f"하단 근접 → 매수 관심"
    elif bb_pct >= 1.0: return f"상단 이탈 → 강한 매도 구간"
    elif bb_pct >= 0.8: return f"상단 근접 → 매도 관심"
    else:               return f"밴드 중간"

def bb_class(bb_pct):
    if bb_pct <= 0.2:   return "buy-zone"
    elif bb_pct >= 0.8: return "sell-zone"
    else:               return "neutral"

def score_signal(ind):
    score = 0
    rsi = ind['rsi']
    if rsi < 25:   score += 3
    elif rsi < 35: score += 2
    elif rsi < 45: score += 1
    elif rsi > 75: score -= 3
    elif rsi > 65: score -= 2
    elif rsi > 55: score -= 1

    hist = ind['macd_hist']
    prev = ind['prev_macd_hist']
    if prev < 0 and hist > 0:     score += 3
    elif prev > 0 and hist < 0:   score -= 3
    elif hist > 0 and hist > prev: score += 1
    elif hist < 0 and hist > prev: score += 1
    elif hist < 0 and hist < prev: score -= 1

    bb_pct = ind['bb_pct']
    if bb_pct <= 0.0:   score += 3
    elif bb_pct <= 0.2: score += 1
    elif bb_pct >= 1.0: score -= 3
    elif bb_pct >= 0.8: score -= 1

    es, em, el = ind['ema_short'], ind['ema_mid'], ind['ema_long']
    if es > em > el:   score += 1
    elif es < em < el: score -= 1

    sk = ind['stoch_k']
    if sk < 20:   score += 1
    elif sk > 80: score -= 1

    # 거래량 급증이 방향성을 확인하면 +1, 거래량 없는 신호는 신뢰도 낮음
    vol = ind.get('volume_ratio', 1.0)
    if vol >= 2.0:
        if score > 0:   score += 1
        elif score < 0: score -= 1

    return score

def action_from_score(score):
    if score >= 8:    return ("강한 매수", "buy-strong")
    elif score >= 5:  return ("매수 준비", "buy-watch")
    elif score >= 3:  return ("매수 관심", "buy-watch")
    elif score >= 1:  return ("매수 우호", "buy-mild")
    elif score <= -5: return ("강한 매도", "sell-strong")
    elif score <= -3: return ("매도 관심", "sell-watch")
    elif score <= -1: return ("매도 우호", "sell-mild")
    else:             return ("관망", "hold")

def support_resistance(df, n=100):
    # 최근 100개 캔들 기준 (기존 20개보다 의미있는 지지/저항 수준 산출)
    # 데이터가 n보다 적으면 가용한 전체 데이터 사용
    recent = df.tail(n)
    return float(recent['low'].min()), float(recent['high'].max())
