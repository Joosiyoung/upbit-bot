# ─────────────────────────────────────────────
# 청산 판정 단일 구현 — trader.py(라이브·시뮬)와 scripts/backtest.py가 공유
# ─────────────────────────────────────────────
#
# [Phase B 리팩토링 배경, 2026-07-07]
# 기존에는 trader.py와 backtest.py가 청산 로직을 각각 구현해 미묘한 회계 차이가
# 있었다: trader는 매수·매도 수수료를 모두 반영한 실수령 기준(양방향), backtest
# 판정은 매도 수수료만 반영(편도)이라 익절·손절 트리거 지점이 ~0.1% 어긋났다.
# 백테스트-라이브 괴리(실측: 백테스트 +0.15%/거래 vs 실시간 시뮬 -0.45%/거래)의
# 구조적 원인을 제거하기 위해 라이브 회계(양방향)를 표준으로 단일화한다.
# Google Sheets에 적재되는 profit_pct와 동일한 기준이다.
#
# 우선순위는 trader.py Phase 1과 동일:
#   익절 → 손절 → 트레일링 → 매도신호 → time-stop
# (상장폐지 감지는 라이브 전용 운영 로직이라 trader.py에 남는다.)

FEE_RATE = 0.0005   # 업비트 KRW 마켓 수수료 0.05% (편도)


def net_profit_pct(entry_price: float, price: float) -> float:
    """수수료 반영 실수령 기준 수익률(%). 시뮬·실거래·백테스트 동일 회계."""
    net_sell = price * (1 - FEE_RATE)
    effective_entry = entry_price * (1 + FEE_RATE)
    return (net_sell - effective_entry) / effective_entry * 100


def judge_exit(entry_price: float, close: float, high: float, low: float,
               peak: float, held_h, action_class,
               tp: float, sl: float, tr_start: float, tr_stop: float,
               max_hold_h: float, intrabar: bool = False,
               no_signal_exit: bool = False):
    """청산 판정. 우선순위: 익절 → 손절 → 트레일링 → 매도신호 → time-stop.

    - 수익률은 net_profit_pct(양방향 수수료 반영 실수령) 기준.
    - intrabar=True: 봉의 고저가 익절/손절가를 터치했는지로 판정(백테스트 전용).
      한 봉에서 둘 다 터치하면 보수적으로 손절 우선.
    - held_h=None이면 time-stop 판정을 건너뛴다(라이브에서 entry_ts 유실 시).
    - 반환: (exit_price, reason_key). 청산 없으면 (None, None).
      reason_key ∈ {"익절", "손절", "손절(intrabar 동시터치)", "트레일링",
                    "매도신호", "time-stop"}
    """
    profit_pct = net_profit_pct(entry_price, close)
    peak_profit_pct = net_profit_pct(entry_price, peak)

    # net_profit_pct == ±목표가 되는 가격 (intrabar 터치 판정·체결가)
    effective_entry = entry_price * (1 + FEE_RATE)
    tp_price = effective_entry * (1 + tp / 100) / (1 - FEE_RATE)
    sl_price = effective_entry * (1 - sl / 100) / (1 - FEE_RATE)

    if intrabar and high >= tp_price and low <= sl_price:
        # 한 봉에서 둘 다 터치 → 보수적으로 손절 우선
        return sl_price, "손절(intrabar 동시터치)"
    if (intrabar and high >= tp_price) or (not intrabar and profit_pct >= tp):
        return (tp_price if intrabar else close), "익절"
    if (intrabar and low <= sl_price) or (not intrabar and profit_pct <= -sl):
        return (sl_price if intrabar else close), "손절"
    if peak_profit_pct >= tr_start and close <= peak * (1 - tr_stop / 100):
        return close, "트레일링"
    if (not no_signal_exit) and action_class == "sell-strong":
        return close, "매도신호"
    if held_h is not None and held_h >= max_hold_h:
        return close, "time-stop"
    return None, None
