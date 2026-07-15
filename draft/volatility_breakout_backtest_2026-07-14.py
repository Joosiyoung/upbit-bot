# -*- coding: utf-8 -*-
"""
변동성 돌파(Larry Williams volatility breakout) 사전 검증 백테스트 — 독립 실험 스크립트.

목적: 기존 점수제 추세추종 전략과 "같은 유니버스·같은 기간·같은 split"으로
변동성 돌파 전략의 in/out-of-sample 기대값을 산출해, 실시간 관찰 로거를 붙일
가치가 있는지 사전 점검한다.

★ 코드베이스 무영향: core/·scripts/backtest.py 를 일절 수정하지 않는다.
  청산 판정은 core.exit_rules.judge_exit 를 그대로 임포트해 재사용한다(회계 동일).
  진입 로직만 점수제 → 변동성 돌파로 교체한다.

전략 정의 (Larry Williams 원형):
  - 일봉 기준. 당일 목표가 = 당일 시가 + k × (전일 고가 − 전일 저가).
  - 당일 장중 가격이 목표가를 상향 돌파하면 매수.
  - k 스윕: {0.3, 0.5, 0.7}.
  - 원형은 익일 시가 청산이지만, 본 프로젝트 청산 인프라(judge_exit: TP/SL/트레일링/
    time-stop)를 그대로 재사용한다. → 진입만 교체, 청산 프레임은 기존과 동일.

구현 메모(정직하게 명시):
  - 청산 판정이 hourly 봉·held_h(시간) 기반이므로, 진입도 hourly 봉 위에서 해석한다.
    각 '일봉 구간(09:00~다음날 08:00 KST)'의 시가(첫 hourly open)를 그날 시가로 보고,
    목표가를 넘긴 첫 hourly 봉의 종가에 진입한다(= 라이브에서 30초 스냅샷이 목표가를
    넘긴 시점 근사). intrabar=True면 청산은 봉 고저 터치로 판정한다.
  - 룩어헤드 제거: 목표가 계산의 (전일 H−L)은 '완성된 전일 일봉'만 사용한다.
  - 포지션은 종목별 독립 순차 매매(동시 1포지션). 포트폴리오 슬롯 경합 미반영 —
    기존 backtest.py와 동일한 한계.
  - 수수료 왕복 0.05%×2, judge_exit 의 net 회계와 동일.

사용:
  python3 draft/volatility_breakout_backtest_2026-07-14.py
  python3 draft/volatility_breakout_backtest_2026-07-14.py --days 180 --split 2026-05-01
"""

import argparse
import os
import sys
import time
from collections import Counter

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyupbit
from core.indicators import add_all_indicators
from core.exit_rules import FEE_RATE, judge_exit
from core import config

TICKERS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE",
           "KRW-ADA", "KRW-LINK", "KRW-AVAX", "KRW-DOT", "KRW-TRX"]

_OHLCV_CACHE: dict = {}


def fetch_ohlcv(ticker: str, interval: str, count: int) -> pd.DataFrame:
    key = (ticker, interval, count)
    if key in _OHLCV_CACHE:
        return _OHLCV_CACHE[key]
    frames = []
    to = None
    remaining = count
    while remaining > 0:
        n = min(200, remaining)
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=n, to=to)
        if df is None or df.empty:
            break
        frames.append(df)
        to = df.index[0].strftime("%Y-%m-%d %H:%M:%S")
        remaining -= len(df)
        if len(df) < n:
            break
        time.sleep(0.12)
    if not frames:
        _OHLCV_CACHE[key] = pd.DataFrame()
        return _OHLCV_CACHE[key]
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    _OHLCV_CACHE[key] = out
    return out


# ── 시장 레짐 (BTC 일봉 EMA) — backtest.py build_market_regime 로직 재현 ──
_MARKET_REGIME_CACHE = None


def build_market_regime(days: int) -> list:
    global _MARKET_REGIME_CACHE
    if _MARKET_REGIME_CACHE is not None:
        return _MARKET_REGIME_CACHE
    btc = fetch_ohlcv("KRW-BTC", "day", max(220, days + 60))
    regime = []
    if not btc.empty:
        btc = add_all_indicators(btc)
        for i in range(len(btc)):
            es = btc["ema_short"].iloc[i]
            em = btc["ema_mid"].iloc[i]
            regime.append((btc.index[i].date(), bool(es >= em)))
    _MARKET_REGIME_CACHE = regime
    return regime


def backtest_ticker(ticker: str, days: int, k: float, intrabar: bool,
                    regime_gate: bool, tp: float, sl: float,
                    tr_start: float, tr_stop: float, max_hold_h: float) -> dict:
    hourly = fetch_ohlcv(ticker, "minute60", days * 24)
    daily = fetch_ohlcv(ticker, "day", max(60, days + 60))
    if hourly.empty or daily.empty or len(hourly) < 48:
        return {"ticker": ticker, "trades": [], "skipped": "데이터 부족"}

    # 전일 (H−L) 레인지: 완성 일봉만 사용. date → prev_range
    prev_range = {}
    dates = list(daily.index)
    for i in range(1, len(daily)):
        d = dates[i].date()
        pr = float(daily["high"].iloc[i - 1] - daily["low"].iloc[i - 1])
        prev_range[d] = pr

    # 각 hourly 봉이 속한 '일봉 구간' key = 그 봉의 date (Upbit 일봉 경계 09:00 KST와 일치)
    market_regime = build_market_regime(days) if regime_gate else []

    def market_ok_before(d):
        ok = True
        for dt, v in market_regime:
            if dt < d:
                ok = v
            else:
                break
        return ok

    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    peak = 0.0

    # 일봉 구간별 당일 시가(첫 hourly open)·목표가 산출
    cur_day = None
    day_open = None
    target = None
    breakout_done_today = False

    warmup = 48  # 지표·레인지 워밍업
    for i in range(len(hourly)):
        ts = hourly.index[i]
        d = ts.date()
        close = float(hourly["close"].iloc[i])
        high = float(hourly["high"].iloc[i])
        low = float(hourly["low"].iloc[i])

        # 새 일봉 구간 진입 → 당일 시가·목표가 재설정
        if d != cur_day:
            cur_day = d
            day_open = float(hourly["open"].iloc[i])
            pr = prev_range.get(d)
            target = (day_open + k * pr) if pr is not None else None
            breakout_done_today = False

        if not in_pos:
            if i < warmup:
                continue
            if target is None:
                continue
            if breakout_done_today:
                continue
            regime_block = regime_gate and not market_ok_before(d)
            # 장중 고가가 목표가를 상향 돌파 → 진입 (체결가는 보수적으로 max(목표가, open))
            if high >= target and not regime_block:
                fill = max(target, float(hourly["open"].iloc[i]))
                fill = min(fill, high)  # 봉 범위 내로 클램프
                in_pos = True
                entry_price = fill
                entry_idx = i
                peak = fill
                breakout_done_today = True
            elif high >= target and regime_block:
                breakout_done_today = True  # 오늘 돌파는 발생했으나 레짐 차단 → 재시도 안 함
            continue

        # ── 보유 중: 청산 판정 (judge_exit 재사용) ──
        h = high if intrabar else close
        lo = low if intrabar else close
        peak = max(peak, h)
        held_h = i - entry_idx
        exit_price, reason = judge_exit(
            entry_price, close, h, lo, peak, held_h,
            action_class="hold",   # 변동성 돌파엔 매도신호(sell-strong) 없음
            tp=tp, sl=sl, tr_start=tr_start, tr_stop=tr_stop,
            max_hold_h=max_hold_h, intrabar=intrabar, no_signal_exit=True,
        )
        if reason:
            ret = (exit_price * (1 - FEE_RATE)) / (entry_price * (1 + FEE_RATE)) - 1
            trades.append({
                "ticker": ticker, "entry": entry_price, "exit": exit_price,
                "ret_pct": ret * 100, "reason": reason, "held_h": held_h,
                "entry_ts": str(hourly.index[entry_idx]), "exit_ts": str(ts),
            })
            in_pos = False

    return {"ticker": ticker, "trades": trades, "skipped": None}


def summarize(all_trades: list) -> dict:
    if not all_trades:
        return {"n": 0}
    rets = [t["ret_pct"] for t in all_trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    ordered = sorted(all_trades, key=lambda t: t["exit_ts"])
    equity = 1.0
    peak_eq = 1.0
    mdd = 0.0
    for t in ordered:
        equity *= (1 + t["ret_pct"] / 100)
        peak_eq = max(peak_eq, equity)
        mdd = min(mdd, equity / peak_eq - 1)
    return {
        "n": len(all_trades),
        "win_rate": len(wins) / len(rets) * 100,
        "avg_ret": sum(rets) / len(rets),
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "total_compound": (equity - 1) * 100,
        "mdd": mdd * 100,
        "avg_hold_h": sum(t["held_h"] for t in all_trades) / len(all_trades),
        "reasons": dict(Counter(t["reason"] for t in all_trades)),
    }


def split_trades(trades, split_date):
    ins = [t for t in trades if t["exit_ts"] < split_date]
    outs = [t for t in trades if t["exit_ts"] >= split_date]
    return ins, outs


def fmt_cell(s):
    if not s["n"]:
        return f"{'거래없음':^40}"
    return (f"{s['n']:>4}건 {s['win_rate']:>5.1f}% {s['avg_ret']:>+6.3f}%"
            f" {s['total_compound']:>+8.1f}% {s['mdd']:>7.1f}%")


def run_combo(k, regime_gate, days, split, intrabar, tp, sl, tr_start, tr_stop, max_hold_h):
    all_trades = []
    for tk in TICKERS:
        res = backtest_ticker(tk, days, k, intrabar, regime_gate,
                              tp, sl, tr_start, tr_stop, max_hold_h)
        if res.get("skipped"):
            continue
        all_trades.extend(res["trades"])
    ins, outs = split_trades(all_trades, split)
    return all_trades, summarize(ins), summarize(outs), summarize(all_trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--split", default="2026-05-01")
    ap.add_argument("--intrabar", action="store_true", default=True)
    ap.add_argument("--ks", default="0.3,0.5,0.7")
    ap.add_argument("--tp", type=float, default=config.TAKE_PROFIT_PERCENT)
    ap.add_argument("--sl", type=float, default=config.MAX_LOSS_PERCENT)
    args = ap.parse_args()

    tp = args.tp
    sl = args.sl
    tr_start = config.TRAILING_START_PCT
    tr_stop = config.TRAILING_STOP_PCT
    max_hold_h = config.MAX_HOLD_HOURS
    ks = [float(x) for x in args.ks.split(",")]

    print(f"\n변동성 돌파 사전 검증 — {len(TICKERS)}종목, {args.days}일, split={args.split}, intrabar={args.intrabar}")
    print(f"청산 룰(judge_exit 재사용): 익절+{tp}% / 손절-{sl}% / 트레일링 {tr_start}·{tr_stop}% / "
          f"time-stop {max_hold_h:.0f}h / 수수료 왕복 {FEE_RATE*2*100:.2f}%")
    print("=" * 100)
    hdr = f"{'건수':>5} {'승률':>6} {'평균':>7} {'복리':>9} {'MDD':>8}"
    print(f"{'k':>4} {'regime':>7} | {'IN-SAMPLE':^40} | {'OUT-OF-SAMPLE':^40}")
    print(f"{'':>4} {'':>7} | {hdr:^40} | {hdr:^40}")
    print("-" * 100)

    results = []
    for k in ks:
        for rg in (False, True):
            _, sin, sout, sall = run_combo(k, rg, args.days, args.split, args.intrabar,
                                           tp, sl, tr_start, tr_stop, max_hold_h)
            rg_label = "ON" if rg else "OFF"
            print(f"{k:>4.1f} {rg_label:>7} | {fmt_cell(sin)} | {fmt_cell(sout)}")
            results.append((k, rg, sin, sout, sall))

    print("=" * 100)
    print("\n=== 채택 기준 판정 (in·out 두 구간 모두 avg_ret > 0) ===")
    passed = []
    for k, rg, sin, sout, sall in results:
        rg_label = "ON" if rg else "OFF"
        in_ev = sin.get("avg_ret", 0) if sin["n"] else None
        out_ev = sout.get("avg_ret", 0) if sout["n"] else None
        if in_ev is not None and out_ev is not None and in_ev > 0 and out_ev > 0:
            passed.append((k, rg, in_ev, out_ev))
            print(f"  [통과] k={k} regime={rg_label}: in {in_ev:+.3f}% / out {out_ev:+.3f}%")
    if not passed:
        print("  통과 조합 없음 — 어느 k·레짐 조합도 in·out 동시 양수 기대값을 만족하지 못함.")

    print("\n=== 기존 채택 전략(점수제) 대비 참고 ===")
    print("  점수제 채택안: in +0.12% / out +0.18% (180일, split 2026-05-01, threshold 13)")


if __name__ == "__main__":
    main()
