# -*- coding: utf-8 -*-
"""
주식 백테스터 — KIS 일봉 데이터에 score_signal 기반 진입/청산 룰 적용.

코인 backtest.py와 동일한 score_signal·indicators를 재사용한다.
청산 우선순위: 익절 → 손절 → 트레일링 → 매도신호 → time-stop(일 기준).
수수료: STOCK_FEE_RATE (기본 0.3% 편도, 매수·매도 각각 적용).

일봉만 사용 — 단일 타임프레임 가중치 × 2.5 적용.

설계 한계:
  - 일봉 백테스트는 종가 기준. 장중 손절/익절 터치는 반영 안 됨.
  - KIS API 키 없이 실행 시 실데이터 조회 불가 → 명확한 에러 메시지 출력.
  - 포트폴리오 동시성(슬롯 경합) 미반영.

사용 예:
  python scripts/stock_backtest.py --tickers 005930,000660 --days 90
  python scripts/stock_backtest.py --days 90
  python scripts/stock_backtest.py --days 365
"""

import argparse
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config
from core.indicators import add_all_indicators
from core.analysis import score_signal, action_from_score
from core.stock.universe import get_universe

FEE_RATE = config.STOCK_FEE_RATE  # 편도 수수료 (기본 0.3%)


# ─────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────

def fetch_daily(code: str, count: int) -> pd.DataFrame:
    """KIS 일봉 데이터 조회. 키 미설정 시 명확한 메시지로 종료."""
    if not config.KIS_APP_KEY or not config.KIS_APP_SECRET:
        print("[오류] KIS_APP_KEY / KIS_APP_SECRET 환경변수가 설정되지 않았습니다.")
        print("  .env 파일에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO를 추가하세요.")
        sys.exit(1)

    from core.stock.kis_client import KisClient
    client = KisClient()
    try:
        return client.get_ohlcv(code, "day", count)
    except Exception as e:
        print(f"[오류] {code} 일봉 조회 실패: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# 지표 행 → score_signal 입력 dict
# ─────────────────────────────────────────────

def row_to_ind(df: pd.DataFrame, i: int) -> dict:
    last = df.iloc[i]
    prev = df.iloc[i - 1] if i > 0 else last
    return {
        "rsi":            last.get("rsi",          50),
        "macd_hist":      last.get("macd_hist",     0),
        "prev_macd_hist": prev.get("macd_hist",     0),
        "bb_pct":         last.get("bb_pct",        0.5),
        "ema_short":      last.get("ema_short",     0),
        "ema_mid":        last.get("ema_mid",       0),
        "ema_long":       last.get("ema_long",      0),
        "stoch_k":        last.get("stoch_k",       50),
        "volume_ratio":   last.get("volume_ratio",  1.0),
    }


# ─────────────────────────────────────────────
# 단일 종목 백테스트
# ─────────────────────────────────────────────

def backtest_ticker(code: str, name: str, days: int, threshold: float,
                    verbose: bool) -> dict:
    # 지표 warmup(60봉) + 백테스트 기간(days봉) 확보
    count = days + 120
    daily = fetch_daily(code, count)

    if daily.empty or len(daily) < 60:
        return {"code": code, "name": name, "trades": [], "skipped": "데이터 부족"}

    # 최근 종가가 슬롯 예산(STOCK_MAX_PRICE)을 초과하면 스킵
    latest_close = float(daily["close"].iloc[-1])
    if latest_close > config.STOCK_MAX_PRICE:
        return {"code": code, "name": name, "trades": [],
                "skipped": f"주가 {latest_close:,.0f}원 > 상한 {config.STOCK_MAX_PRICE:,}원"}

    daily = add_all_indicators(daily)

    tp        = config.STOCK_TAKE_PROFIT_PERCENT
    sl        = config.STOCK_MAX_LOSS_PERCENT
    tr_start  = config.STOCK_TRAILING_START_PCT
    tr_stop   = config.STOCK_TRAILING_STOP_PCT
    max_days  = config.STOCK_MAX_HOLD_DAYS  # 봉(일) 기준

    trades   = []
    in_pos   = False
    entry_price = 0.0
    entry_idx   = 0
    peak        = 0.0

    # 지표 warmup 후 최근 days봉만 백테스트
    warmup    = len(daily) - days
    if warmup < 60:
        warmup = 60
    start_idx = max(warmup, 1)

    for i in range(start_idx, len(daily)):
        close = float(daily["close"].iloc[i])
        ind   = row_to_ind(daily, i)
        # 일봉 단독: score × 2.5
        sc    = score_signal(ind) * 2.5
        _, action_class = action_from_score(sc)

        if not in_pos:
            if sc >= threshold:
                in_pos      = True
                entry_price = close
                entry_idx   = i
                peak        = close
            continue

        # ── 청산 판정 (종가 기준, trader.py Phase 1 우선순위) ──
        peak = max(peak, close)

        net_close       = close * (1 - FEE_RATE)
        profit_pct      = (net_close - entry_price) / entry_price * 100
        peak_profit_pct = (peak * (1 - FEE_RATE) - entry_price) / entry_price * 100
        held_d          = i - entry_idx   # 일봉 수 = 보유 일수

        reason = None

        if profit_pct >= tp:
            reason = "익절"
        elif profit_pct <= -sl:
            reason = "손절"
        elif peak_profit_pct >= tr_start and close <= peak * (1 - tr_stop / 100):
            reason = "트레일링"
        elif action_class == "sell-strong":
            reason = "매도신호"
        elif held_d >= max_days:
            reason = "time-stop"

        if reason:
            exit_price = close
            ret = (exit_price * (1 - FEE_RATE)) / (entry_price * (1 + FEE_RATE)) - 1
            trades.append({
                "code":     code,
                "name":     name,
                "entry":    entry_price,
                "exit":     exit_price,
                "ret_pct":  ret * 100,
                "reason":   reason,
                "held_d":   held_d,
                "entry_ts": str(daily.index[entry_idx]),
                "exit_ts":  str(daily.index[i]),
            })
            in_pos = False

    # Buy&Hold 벤치마크
    bh_start = float(daily["close"].iloc[start_idx])
    bh_end   = float(daily["close"].iloc[-1])
    bh_ret   = (bh_end / bh_start - 1) * 100 if bh_start > 0 else 0.0

    return {"code": code, "name": name, "trades": trades, "skipped": None, "bh_ret": bh_ret}


# ─────────────────────────────────────────────
# 집계
# ─────────────────────────────────────────────

def summarize(all_trades: list) -> dict:
    if not all_trades:
        return {"n": 0}
    rets  = [t["ret_pct"] for t in all_trades]
    wins  = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    ordered = sorted(all_trades, key=lambda t: t["exit_ts"])
    equity  = 1.0
    peak_eq = 1.0
    mdd     = 0.0
    for t in ordered:
        equity *= (1 + t["ret_pct"] / 100)
        peak_eq = max(peak_eq, equity)
        mdd     = min(mdd, equity / peak_eq - 1)

    from collections import Counter
    reasons = Counter(t["reason"] for t in all_trades)

    return {
        "n":              len(all_trades),
        "win_rate":       len(wins) / len(rets) * 100,
        "avg_ret":        sum(rets) / len(rets),
        "avg_win":        sum(wins) / len(wins) if wins else 0,
        "avg_loss":       sum(losses) / len(losses) if losses else 0,
        "best":           max(rets),
        "worst":          min(rets),
        "total_compound": (equity - 1) * 100,
        "mdd":            mdd * 100,
        "avg_hold_d":     sum(t["held_d"] for t in all_trades) / len(all_trades),
        "reasons":        dict(reasons),
    }


# ─────────────────────────────────────────────
# 진입 기준
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="국내 주식 자동매매 백테스터")
    ap.add_argument("--tickers", default="",
                    help="쉼표구분 종목코드 목록 (미지정 시 전체 universe)")
    ap.add_argument("--days",      type=int,   default=90,   help="백테스트 기간(일)")
    ap.add_argument("--threshold", type=float, default=config.STOCK_BUY_SCORE_THRESHOLD,
                    help="매수 진입 점수 기준 (일봉 단독 × 2.5)")
    ap.add_argument("--verbose",   action="store_true", help="개별 트레이드 출력")
    args = ap.parse_args()

    if args.tickers.strip():
        codes = [c.strip() for c in args.tickers.split(",") if c.strip()]
        universe_map = {code: name for code, name in get_universe()}
        targets = [(c, universe_map.get(c, c)) for c in codes]
    else:
        targets = get_universe()

    print(f"\n주식 백테스트 시작 — {len(targets)}종목, {args.days}일, "
          f"threshold={args.threshold}")
    print(f"청산 룰: 익절 +{config.STOCK_TAKE_PROFIT_PERCENT}% / "
          f"손절 -{config.STOCK_MAX_LOSS_PERCENT}% / "
          f"트레일링 {config.STOCK_TRAILING_START_PCT}·{config.STOCK_TRAILING_STOP_PCT}% / "
          f"time-stop {config.STOCK_MAX_HOLD_DAYS}일 / "
          f"수수료 왕복 {FEE_RATE * 2 * 100:.2f}%")
    print("=" * 64)

    all_trades = []
    bh_rets    = []

    for code, name in targets:
        res = backtest_ticker(code, name, args.days, args.threshold, args.verbose)
        if res.get("skipped"):
            print(f"  [{code} {name}] 스킵 ({res['skipped']})")
            continue
        if res.get("bh_ret") is not None:
            bh_rets.append(res["bh_ret"])
        s = summarize(res["trades"])
        all_trades.extend(res["trades"])

        # 개별 종목 출력 (작업 지시서 형식)
        label = f"[{code} {name}]"
        print(f"\n{label} {args.days}일 백테스트")
        if s["n"] == 0:
            print("  진입: 0회")
        else:
            wins   = len([t for t in res["trades"] if t["ret_pct"] > 0])
            losses = s["n"] - wins
            print(f"  진입: {s['n']}회  승: {wins}  패: {losses}  승률: {s['win_rate']:.1f}%")
            print(f"  평균 수익률: {s['avg_ret']:+.2f}%  기대값: {s['avg_ret']:+.2f}%")
            print(f"  최고: {s['best']:+.1f}%  최저: {s['worst']:+.1f}%")
            if args.verbose:
                for t in res["trades"]:
                    print(f"    {t['entry_ts']} → {t['exit_ts']}  "
                          f"{t['ret_pct']:+.2f}%  {t['reason']}")

    print("\n" + "=" * 64)
    agg = summarize(all_trades)
    if agg["n"] == 0:
        print("전체: 거래 없음")
        return

    print(f"전체 {agg['n']}건  |  승률 {agg['win_rate']:.1f}%  |  "
          f"거래당 기대값 {agg['avg_ret']:+.3f}%")
    print(f"평균 수익 {agg['avg_win']:+.2f}% / 평균 손실 {agg['avg_loss']:+.2f}%  |  "
          f"평균 보유 {agg['avg_hold_d']:.1f}일")
    print(f"순차 복리 수익률 {agg['total_compound']:+.1f}%  |  최대낙폭(MDD) {agg['mdd']:.1f}%")
    print(f"청산 사유 분포: {agg['reasons']}")
    if bh_rets:
        bh_avg = sum(bh_rets) / len(bh_rets)
        print(f"[벤치마크] Buy&Hold 평균 종목 수익률 {bh_avg:+.1f}% "
              f"({len([r for r in bh_rets if r > 0])}/{len(bh_rets)} 종목 상승) "
              f"— 구간 시장 방향성")
    print()
    ev = agg["avg_ret"]
    verdict = ("양(+) 기대값 — 진입 신호에 통계적 우위 있음" if ev > 0.05
               else "기대값 ~0 — 우위 불명확, 파라미터 재검토 권장" if ev > -0.05
               else "음(-) 기대값 — 현 룰은 수수료 차감 후 손실")
    print(f"판정: {verdict}")


if __name__ == "__main__":
    main()
