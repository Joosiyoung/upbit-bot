"""score_signal / action_from_score 특성 테스트 (characterization).

현재 동작을 스펙으로 고정한다 — 리팩토링(Phase B) 시 이 테스트가 깨지면
의도치 않은 로직 변경이 발생한 것. 점수 재설계 원칙(2026-06-13)의 핵심 가정
(추세 게이트, 비추세 과매도 무가점 = knife-catch 차단)을 함께 검증한다.
"""

from core.analysis import action_from_score, score_signal


def make_ind(**overrides):
    """중립 기본값: 어떤 블록에서도 점수가 나지 않는 지표 세트."""
    ind = {
        "ema_short": 100.0, "ema_mid": 100.0, "ema_long": 100.0,  # 혼조 (정배열 아님)
        "rsi": 50.0,
        "macd_hist": 0.0, "prev_macd_hist": 0.0,
        "bb_pct": 0.5,
        "stoch_k": 50.0,
        "volume_ratio": 1.0,
    }
    ind.update(overrides)
    return ind


def test_neutral_inputs_score_zero():
    assert score_signal(make_ind()) == 0


def test_bullish_alignment_scores_trend_and_pullback():
    # 정배열 +2, RSI 50(눌림목 40~60) +2, stoch<40 +1
    ind = make_ind(ema_short=110, ema_mid=105, ema_long=100, stoch_k=30)
    assert score_signal(ind) == 5


def test_bearish_alignment_penalized():
    ind = make_ind(ema_short=90, ema_mid=95, ema_long=100)
    assert score_signal(ind) == -2


def test_macd_golden_cross_adds_3():
    ind = make_ind(prev_macd_hist=-1.0, macd_hist=1.0)
    assert score_signal(ind) == 3


def test_macd_dead_cross_subtracts_3():
    ind = make_ind(prev_macd_hist=1.0, macd_hist=-1.0)
    assert score_signal(ind) == -3


def test_knife_catch_blocked_no_bonus_for_nontrend_oversold():
    """재설계 핵심: 비추세(혼조) 깊은 과매도·BB 하단 이탈에 가점 없음."""
    ind = make_ind(rsi=20.0, bb_pct=-0.1)
    assert score_signal(ind) == 0


def test_bullish_deep_pullback_small_bonus():
    # 정배열 +2, RSI<40 (추세가 받쳐주는 깊은 눌림) +1
    ind = make_ind(ema_short=110, ema_mid=105, ema_long=100, rsi=35.0)
    assert score_signal(ind) == 3


def test_volume_amplifies_direction():
    base = make_ind(ema_short=110, ema_mid=105, ema_long=100)  # +2 +2(RSI 50) = 4
    boosted = dict(base, volume_ratio=2.5)
    assert score_signal(base) == 4
    assert score_signal(boosted) == 5  # 방향 있는 점수에 거래량 +1


def test_volume_does_not_amplify_zero_score():
    assert score_signal(make_ind(volume_ratio=3.0)) == 0


def test_full_bullish_combo():
    # 정배열+2, 골든크로스+3, RSI 50 +2, BB 상단 주행 +1, stoch 30 +1, 거래량 +1
    ind = make_ind(
        ema_short=110, ema_mid=105, ema_long=100,
        prev_macd_hist=-0.5, macd_hist=0.5,
        rsi=50, bb_pct=0.85, stoch_k=30, volume_ratio=2.0,
    )
    assert score_signal(ind) == 10


def test_action_thresholds():
    assert action_from_score(8) == ("강한 매수", "buy-strong")
    assert action_from_score(7.9) == ("매수 준비", "buy-watch")
    assert action_from_score(5) == ("매수 준비", "buy-watch")
    assert action_from_score(3) == ("매수 관심", "buy-watch")
    assert action_from_score(1) == ("매수 우호", "buy-mild")
    assert action_from_score(0) == ("관망", "hold")
    assert action_from_score(-1) == ("매도 우호", "sell-mild")
    assert action_from_score(-3) == ("매도 관심", "sell-watch")
    assert action_from_score(-5) == ("강한 매도", "sell-strong")
