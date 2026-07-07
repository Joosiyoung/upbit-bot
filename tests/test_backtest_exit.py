"""judge_exit 스펙 테스트 — 청산 우선순위·수수료 회계 (Phase B: 단일 구현).

core.exit_rules.judge_exit는 trader.py(라이브)와 backtest.py가 공유하는 유일한
청산 판정 구현. 회계는 양방향 수수료 반영 실수령 기준(라이브·Sheets와 동일).
"""

import pytest

from core.exit_rules import FEE_RATE, judge_exit, net_profit_pct
from backtest import split_trades

# 기본 파라미터: 익절 6%, 손절 4%, 트레일링 시작 5% / 폭 2%, 보유한도 48h
DEFAULTS = dict(tp=6.0, sl=4.0, tr_start=5.0, tr_stop=2.0, max_hold_h=48)


def judge(entry=100.0, close=100.0, high=None, low=None, peak=None,
          held_h=0, action_class="hold", intrabar=False,
          no_signal_exit=False, **overrides):
    params = dict(DEFAULTS, **overrides)
    return judge_exit(
        entry, close,
        high if high is not None else close,
        low if low is not None else close,
        peak if peak is not None else max(close, entry),
        held_h, action_class,
        params["tp"], params["sl"], params["tr_start"], params["tr_stop"],
        params["max_hold_h"], intrabar=intrabar, no_signal_exit=no_signal_exit,
    )


def test_fee_rate_matches_upbit():
    assert FEE_RATE == 0.0005


def test_live_and_backtest_share_single_implementation():
    """Phase B 핵심 보증: 라이브와 백테스터가 같은 함수 객체를 사용한다."""
    import backtest as bt
    from core import exit_rules, trader
    assert bt.judge_exit is exit_rules.judge_exit
    assert trader.judge_exit is exit_rules.judge_exit
    assert bt.FEE_RATE == trader.FEE_RATE == exit_rules.FEE_RATE


def test_net_profit_pct_round_trip_fee():
    # 같은 가격에 사고팔면 왕복 수수료만큼 손실 (~-0.0999%)
    assert net_profit_pct(100.0, 100.0) == pytest.approx(-0.09995, abs=1e-4)
    # 명목 +10%의 실수령: (109.945 - 100.05) / 100.05 = +9.89%
    assert net_profit_pct(100.0, 110.0) == pytest.approx(9.890, abs=1e-3)


def test_held_none_skips_time_stop():
    """entry_ts 유실(라이브 엣지케이스) 시 time-stop 판정을 건너뛴다."""
    assert judge(close=101.0, held_h=None) == (None, None)


def test_no_exit_when_neutral():
    assert judge(close=101.0) == (None, None)


def test_take_profit_close_based():
    # 수수료 반영 수익률 = (110×0.9995 − 100)/100 = +9.94% ≥ 6%
    price, reason = judge(close=110.0, peak=110.0)
    assert reason == "익절"
    assert price == 110.0  # 종가 기준 모드는 종가 체결


def test_take_profit_respects_fee():
    # 명목 +6%지만 수수료 차감 후 5.947% < 6% → 익절 아님 (실수령 기준 회계)
    assert judge(close=106.0, peak=106.0) == (None, None)
    # 수수료를 이겨내는 수준이면 익절
    _, reason = judge(close=106.4, peak=106.4)
    assert reason == "익절"


def test_stop_loss_close_based():
    price, reason = judge(close=96.0)
    assert reason == "손절"
    assert price == 96.0


def test_intrabar_tp_touch_fills_at_target():
    # 고가가 목표가 터치 → 체결가는 net_profit_pct == +6%가 되는 가격
    expected_tp_price = 100.0 * (1 + FEE_RATE) * 1.06 / (1 - FEE_RATE)
    price, reason = judge(close=104.0, high=107.0, low=103.0,
                          peak=107.0, intrabar=True)
    assert reason == "익절"
    assert price == pytest.approx(expected_tp_price)
    assert net_profit_pct(100.0, price) == pytest.approx(6.0)


def test_intrabar_both_touch_prefers_stop_loss():
    """한 봉에서 익절·손절 동시 터치 → 보수적으로 손절 우선 (현재 스펙)."""
    expected_sl_price = 100.0 * (1 + FEE_RATE) * 0.96 / (1 - FEE_RATE)
    price, reason = judge(close=100.0, high=108.0, low=95.0,
                          peak=108.0, intrabar=True)
    assert reason == "손절(intrabar 동시터치)"
    assert price == pytest.approx(expected_sl_price)
    assert net_profit_pct(100.0, price) == pytest.approx(-4.0)


def test_trailing_stop_after_peak_gain():
    # 고점 +10%(수수료 반영 후 ≥5%)에서 2% 하락, 익절/손절 범위 밖
    price, reason = judge(close=107.0, peak=110.0, tp=20.0, sl=10.0)
    assert reason == "트레일링"
    assert price == 107.0


def test_trailing_not_armed_below_start():
    # 고점 수익이 tr_start(5%) 미달이면 트레일링 미발동
    assert judge(close=101.0, peak=103.0, tp=20.0, sl=10.0) == (None, None)


def test_signal_exit_on_sell_strong():
    _, reason = judge(close=101.0, action_class="sell-strong")
    assert reason == "매도신호"


def test_signal_exit_disabled_flag():
    assert judge(close=101.0, action_class="sell-strong",
                 no_signal_exit=True) == (None, None)


def test_time_stop_at_max_hold():
    _, reason = judge(close=101.0, held_h=48)
    assert reason == "time-stop"
    assert judge(close=101.0, held_h=47) == (None, None)


def test_priority_tp_over_signal_and_timestop():
    """복수 조건 동시 충족 시 익절이 최우선 (Phase 1 우선순위)."""
    _, reason = judge(close=110.0, peak=110.0, held_h=100,
                      action_class="sell-strong")
    assert reason == "익절"


def test_split_trades_by_exit_ts():
    trades = [
        {"exit_ts": "2026-05-01 10:00:00", "ret_pct": 1.0},
        {"exit_ts": "2026-06-15 10:00:00", "ret_pct": 2.0},
        {"exit_ts": "2026-07-01 00:00:00", "ret_pct": 3.0},
    ]
    ins, outs = split_trades(trades, "2026-07-01")
    assert [t["ret_pct"] for t in ins] == [1.0, 2.0]
    assert [t["ret_pct"] for t in outs] == [3.0]
