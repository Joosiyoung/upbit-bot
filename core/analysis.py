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

def score_signal(ind, bb_mode: str = "current"):
    """추세 정렬 + 모멘텀 확인형 진입 점수.

    [재설계 배경] 구버전은 RSI 과매도·BB 하단 이탈에 큰 가점을 주는 순수
    평균회귀(역추세) 모델이었다. 일봉 가중치가 2.5배라 '강한 매수'가 곧
    '일봉 깊은 과매도 = 하락추세 한복판'을 의미했고, 백테스트에서 거래당
    기대값 -0.56%(승률 47.7%)로 일관된 음의 우위가 확인됐다(README 15절).

    [재설계 원칙]
      1) 추세 레짐을 1차 게이트로: 상승 정배열에 가점, 하락 역배열에 감점.
      2) 모멘텀(MACD) 전환을 핵심 동력으로: 골든/데드크로스·히스토그램 방향.
      3) RSI·BB·스토캐스틱은 '추세 안에서의 위치'로 재해석 —
         상승추세의 얕은 눌림목은 매수 기회, 비추세 과매도에는 가점하지 않아
         '떨어지는 칼 잡기'를 차단한다.

    bb_mode="off": 상승추세 BB 블록 생략 (코인 경로 전용).
                   2026-07-14 채택: bb-off+레짐게이트 스윕 in+0.12/out+0.18.
                   비추세 BB 블록은 유지 (채택 안에서 검증된 범위).
    bb_mode="current": 기본값 — 주식 및 기존 동작 완전 보존.
    """
    es, em, el = ind['ema_short'], ind['ema_mid'], ind['ema_long']
    rsi = ind['rsi']
    hist = ind['macd_hist']
    prev = ind['prev_macd_hist']
    bb_pct = ind['bb_pct']
    sk = ind['stoch_k']
    vol = ind.get('volume_ratio', 1.0)

    bullish = es > em > el
    bearish = es < em < el

    score = 0

    # ── 추세 레짐 (1차 게이트) ──
    if bullish:   score += 2
    elif bearish: score -= 2

    # ── MACD 모멘텀 (추세추종 핵심 동력) ──
    if prev < 0 and hist > 0:        score += 3   # 골든크로스
    elif prev > 0 and hist < 0:      score -= 3   # 데드크로스
    elif hist > 0 and hist > prev:   score += 2   # 상승 모멘텀 강화
    elif hist < 0 and hist < prev:   score -= 2   # 하락 모멘텀 강화

    # ── RSI: 추세에 따라 의미를 다르게 해석 ──
    # 상승추세의 얕은 눌림(40~60)은 매수 기회. 깊은 과매도(<40)는 추세가
    # 받쳐줄 때만 소폭 가점. 비추세 과매도에는 가점하지 않는다(knife-catch 차단).
    if bullish:
        if 40 <= rsi <= 60:  score += 2   # 추세 내 눌림목
        elif rsi < 40:       score += 1   # 깊은 눌림 (추세가 받쳐줌)
        elif rsi > 78:       score -= 1   # 과열
    else:
        if rsi > 70:   score -= 2         # 비추세 과매수 → 매도 압력
        elif rsi > 60: score -= 1

    # ── 볼린저: 추세 내 위치로 해석 ──
    # bb_mode="off": 상승추세 BB 블록 생략 (추격 가점 제거 — 코인 경로 전용).
    # bb_mode="current": 현행 동작 유지 (주식 경로 기본값).
    if bb_mode == "off":
        # 비추세 BB 블록만 적용 (상승추세 BB 완전 생략)
        if not bullish:
            if bb_pct >= 1.0:   score -= 2    # 비추세 상단 이탈 → 매도
            elif bb_pct >= 0.8: score -= 1
    else:
        # 현행 동작 (current)
        if bullish:
            if bb_pct >= 0.8:   score += 1    # 상단 주행 = 강세 지속
            elif bb_pct <= 0.0: score -= 1    # 추세 이탈 경고
        else:
            if bb_pct >= 1.0:   score -= 2    # 비추세 상단 이탈 → 매도
            elif bb_pct >= 0.8: score -= 1

    # ── 스토캐스틱 (보조) ──
    if bullish and sk < 40:  score += 1   # 추세 내 단기 눌림
    elif sk > 85:            score -= 1   # 과열

    # ── 거래량 확인: 방향성 신호를 거래량이 뒷받침하면 가중 ──
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
