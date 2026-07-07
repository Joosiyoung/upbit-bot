"""indicators 특성 테스트 — 현재 계산 동작을 스펙으로 고정."""

import numpy as np
import pandas as pd
import pytest

from core.indicators import (
    add_all_indicators,
    calculate_rsi,
    get_latest_indicators,
)


def make_df(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs if highs is not None else [c * 1.01 for c in closes],
            "low": lows if lows is not None else [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes if volumes is not None else [100.0] * n,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="h"),
    )


def test_rsi_extremes():
    # 상승 우위(가끔 소폭 하락 포함) → RSI 고점, 하락 우위 → RSI 저점
    up_closes = [100 + i + (-2.0 if i % 5 == 0 else 0) for i in range(60)]
    down_closes = [200 - i + (2.0 if i % 5 == 0 else 0) for i in range(60)]
    assert calculate_rsi(make_df(up_closes)).iloc[-1] > 80
    assert calculate_rsi(make_df(down_closes)).iloc[-1] < 20


def test_rsi_quirk_pure_monotonic_returns_neutral():
    """[현재 스펙의 특이점] 손실이 전혀 없는 순수 단조 상승은 avg_loss=0 →
    rs=NaN → RSI가 100이 아니라 중립값 50으로 채워진다. (하락도 대칭)
    실전에서는 무손실 구간이 드물어 영향이 작지만, RSI 지표의 알려진 동작으로 고정.
    """
    pure_up = make_df(list(np.linspace(100, 200, 60)))
    assert calculate_rsi(pure_up).iloc[-1] == 50.0


def test_rsi_warmup_filled_with_50():
    df = make_df(list(np.linspace(100, 110, 60)))
    rsi = calculate_rsi(df)
    assert rsi.iloc[0] == 50.0  # min_periods 미달 구간은 중립값


def test_add_all_indicators_columns_and_bounds():
    df = add_all_indicators(make_df(list(100 + 10 * np.sin(np.arange(80) / 5))))
    for col in ["rsi", "macd_hist", "bb_pct", "ema_short", "ema_mid", "ema_long",
                "volume_ratio", "stoch_k", "atr_pct", "pullback_pct"]:
        assert col in df.columns, col
    assert df["rsi"].between(0, 100).all()
    assert df["stoch_k"].between(0, 100).all()
    assert not df["bb_pct"].isna().any()   # NaN은 0.5로 대체되는 것이 현재 스펙
    assert (df["pullback_pct"] <= 0).all()  # 고점 대비 낙폭은 항상 ≤ 0


def test_short_df_returned_unchanged():
    df = make_df([100.0] * 10)  # MACD_SLOW+SIGNAL 미달
    out = add_all_indicators(df)
    assert "rsi" not in out.columns


def test_get_latest_indicators_completed_uses_second_last_bar():
    df = add_all_indicators(make_df(list(np.linspace(100, 150, 80))))
    live = get_latest_indicators(df, completed=False)
    completed = get_latest_indicators(df, completed=True)
    assert live["close"] == pytest.approx(float(df["close"].iloc[-1]))
    assert completed["close"] == pytest.approx(float(df["close"].iloc[-2]))
    # prev_macd_hist는 기준봉의 직전 봉
    assert completed["prev_macd_hist"] == pytest.approx(float(df["macd_hist"].iloc[-3]))


def test_get_latest_indicators_empty_df():
    assert get_latest_indicators(pd.DataFrame()) == {}
