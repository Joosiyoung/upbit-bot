# -*- coding: utf-8 -*-
"""
백테스터 -- 라이브 매매 룰을 과거 OHLCV에 적용해 성과를 측정한다.

라이브와 동일한 점수 함수(core.analysis.score_signal)·지표(core.indicators)를 재사용하므로
진입 신호가 실거래 봇과 일치한다. 청산은 trader.py Phase 1 우선순위(익절→손절→트레일링→
매도신호→time-stop)를 그대로 따른다. 수수료(왕복 0.05%×2)를 반영한다.

설계상 한계 (정직하게 명시):
  - 1분봉 항목(가중치 0.5)은 과거 대량 수집이 어려워 0으로 둔다. 일봉(×2.5)·1시간봉(×2)이
    점수의 대부분이라 threshold 8 도달 판정에 미치는 영향은 작지만, 라이브보다 진입이
    약간 보수적이 된다(=실제보다 후보가 덜 잡힘).
  - 일봉 점수는 '완성된 직전 일봉'만 사용한다(룩어헤드 제거). 라이브는 진행 중 일봉을
    쓰므로(리페인팅) 백테스트가 더 보수적·현실적이다. 이 차이 자체가 C6(리페인팅) 이슈.
  - 청산 판정은 봉 종가 기준(라이브 30초 스냅샷의 근사). 장중 고저 터치는 --intrabar로.
  - 단일 종목 순차 매매를 종목별 독립 시뮬 후 합산한다(포트폴리오 동시성·슬롯 경합 미반영).
    포지션 사이징·추가매수 같은 포트폴리오 효과는 이 백테스터 범위 밖이며, 진입 신호 품질과
    청산 룰의 기대값을 측정하는 것이 목적이다.

사용 예:
  python scripts/backtest.py --tickers KRW-BTC,KRW-ETH,KRW-SOL --days 90 --confirm 1
  python scripts/backtest.py --days 90 --confirm 2     # 리페인팅 디바운스 효과 비교
"""

import argparse
import sys
import os
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyupbit
from core.indicators import add_all_indicators
from core.analysis import score_signal, action_from_score
from core import config

FEE_RATE = 0.0005   # 업비트 수수료 0.05% (편도)

DEFAULT_TICKERS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE",
                   "KRW-ADA", "KRW-LINK", "KRW-AVAX", "KRW-DOT", "KRW-TRX"]


# ─────────────────────────────────────────────
# 데이터 수집 (페이지네이션)
# ─────────────────────────────────────────────

def fetch_ohlcv(ticker: str, interval: str, count: int) -> pd.DataFrame:
    """count개 캔들을 페이지네이션으로 수집 (pyupbit는 호출당 최대 200개)."""
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
        return pd.DataFrame()
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    return out


# ─────────────────────────────────────────────
# 지표 → score_signal 입력 dict (get_latest_indicators 로직을 행 단위로 재현)
# ─────────────────────────────────────────────

def row_to_ind(df: pd.DataFrame, i: int) -> dict:
    last = df.iloc[i]
    prev = df.iloc[i - 1] if i > 0 else last
    return {
        "rsi": last.get("rsi", 50),
        "macd_hist": last.get("macd_hist", 0),
        "prev_macd_hist": prev.get("macd_hist", 0),
        "bb_pct": last.get("bb_pct", 0.5),
        "ema_short": last.get("ema_short", 0),
        "ema_mid": last.get("ema_mid", 0),
        "ema_long": last.get("ema_long", 0),
        "stoch_k": last.get("stoch_k", 50),
        "volume_ratio": last.get("volume_ratio", 1.0),
        "atr_pct": last.get("atr_pct", 0.0),
        "pullback_pct": last.get("pullback_pct", 0.0),
    }


# ─────────────────────────────────────────────
# 실험용 score_signal 래퍼 (draft/coin_signal_redesign_spec_2026-07-02.md 실험 1·2)
# core.analysis.score_signal은 수정하지 않는다. bb_mode="current"·min_atr_pct=0.0
# (둘 다 기본값)이면 score_signal(ind)과 완전히 동일한 값을 반환한다(회귀 없음).
# ─────────────────────────────────────────────

def score_signal_experimental(ind: dict, bb_mode: str = "current", min_atr_pct: float = 0.0) -> float:
    if bb_mode == "current":
        score = score_signal(ind)
    elif bb_mode in ("flip", "off"):
        # score_signal 본체 로직 복제(BB 블록만 교체) -- 일회성 실험 스크립트라 중복 허용
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
        if bullish:   score += 2
        elif bearish: score -= 2

        if prev < 0 and hist > 0:        score += 3
        elif prev > 0 and hist < 0:      score -= 3
        elif hist > 0 and hist > prev:   score += 2
        elif hist < 0 and hist < prev:   score -= 2

        if bullish:
            if 40 <= rsi <= 60:  score += 2
            elif rsi < 40:       score += 1
            elif rsi > 78:       score -= 1
        else:
            if rsi > 70:   score -= 2
            elif rsi > 60: score -= 1

        if bb_mode == "flip":
            if bullish:
                if bb_pct <= 0.25:   score += 1    # 상승추세 밴드 하단 눌림 = 진입 기회
                elif bb_pct >= 0.95: score -= 1    # 밴드 상단 과열 = 추격 회피
            else:
                if bb_pct >= 1.0:    score -= 2
                elif bb_pct >= 0.8:  score -= 1
        # bb_mode == "off": BB 블록 완전 생략

        if bullish and sk < 40:  score += 1
        elif sk > 85:            score -= 1

        if vol >= 2.0:
            if score > 0:   score += 1
            elif score < 0: score -= 1
    else:
        raise ValueError(f"알 수 없는 bb_mode: {bb_mode}")

    atr_pct = ind.get('atr_pct', 999)
    if atr_pct < min_atr_pct:
        return -999   # 변동성 미달 -- 진입 원천 차단(청산 판정에는 미사용)

    return score


# ─────────────────────────────────────────────
# 단일 종목 백테스트
# ─────────────────────────────────────────────

_MARKET_REGIME_CACHE = None

def build_market_regime(days: int) -> list:
    """시장 프록시(BTC) 일봉 추세 레짐을 (date, ok_to_buy) 목록으로 반환.
    ok_to_buy = BTC 일봉 ema_short >= ema_mid (중기 추세 상승/중립) → 하락장 진입 차단용.
    """
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


def backtest_ticker(ticker: str, days: int, threshold: float, confirm: int,
                    intrabar: bool, verbose: bool, no_signal_exit: bool = False,
                    regime_gate: bool = False, bb_mode: str = "current",
                    min_atr_pct: float = 0.0) -> dict:
    hourly = fetch_ohlcv(ticker, "minute60", days * 24)
    daily  = fetch_ohlcv(ticker, "day", max(220, days + 60))
    if hourly.empty or daily.empty or len(hourly) < 60:
        return {"ticker": ticker, "trades": [], "skipped": "데이터 부족"}

    hourly = add_all_indicators(hourly)
    daily  = add_all_indicators(daily)

    # 완성된 일봉별 점수 (date → sc_1d)
    daily_scores = []  # (date, sc)
    for i in range(len(daily)):
        sc = score_signal_experimental(row_to_ind(daily, i), bb_mode=bb_mode, min_atr_pct=min_atr_pct)
        daily_scores.append((daily.index[i].date(), sc))

    def daily_score_before(d):
        """날짜 d 이전(미포함)의 가장 최근 완성 일봉 점수. 룩어헤드 제거."""
        sc = 0
        for dt, s in daily_scores:
            if dt < d:
                sc = s
            else:
                break
        return sc

    market_regime = build_market_regime(days) if regime_gate else []
    def market_ok_before(d):
        """날짜 d 이전(미포함) 가장 최근 BTC 일봉 레짐. 룩어헤드 제거. 기본 True."""
        ok = True
        for dt, v in market_regime:
            if dt < d:
                ok = v
            else:
                break
        return ok

    tp = config.TAKE_PROFIT_PERCENT
    sl = config.MAX_LOSS_PERCENT
    tr_start = config.TRAILING_START_PCT
    tr_stop = config.TRAILING_STOP_PCT
    max_hold_h = config.MAX_HOLD_HOURS

    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    peak = 0.0
    consec = 0   # 연속 score>=threshold 카운트 (디바운스)

    warmup = 60
    for i in range(warmup, len(hourly)):
        ts = hourly.index[i]
        close = float(hourly["close"].iloc[i])
        sc_1h = score_signal_experimental(row_to_ind(hourly, i), bb_mode=bb_mode, min_atr_pct=min_atr_pct)
        sc_1d = daily_score_before(ts.date())
        total = sc_1d * 2.5 + sc_1h * 2.0   # 1m 항목 생략(가중치 0.5)
        _, action_class = action_from_score(total)

        if not in_pos:
            # 시장 레짐 게이트: BTC 일봉이 하락 추세면 신규 진입 차단
            regime_block = regime_gate and not market_ok_before(ts.date())
            # 진입 디바운스: confirm 연속 봉에서 score>=threshold 유지해야 진입
            if total >= threshold and not regime_block:
                consec += 1
            else:
                consec = 0
            if consec >= confirm:
                in_pos = True
                entry_price = close
                entry_idx = i
                peak = close
                consec = 0
            continue

        # ── 보유 중: 청산 판정 (trader.py Phase 1 우선순위) ──
        high = float(hourly["high"].iloc[i]) if intrabar else close
        low  = float(hourly["low"].iloc[i]) if intrabar else close
        peak = max(peak, high)

        net_close = close * (1 - FEE_RATE)
        profit_pct = (net_close - entry_price) / entry_price * 100
        peak_profit_pct = (peak * (1 - FEE_RATE) - entry_price) / entry_price * 100
        held_h = (i - entry_idx)  # 1시간봉이므로 봉 수 = 시간

        exit_price = close
        reason = None

        # 익절 (intrabar: 고가가 목표 도달)
        tp_price = entry_price * (1 + tp / 100) / (1 - FEE_RATE)
        sl_price = entry_price * (1 - sl / 100) / (1 - FEE_RATE)
        if intrabar and high >= tp_price and low <= sl_price:
            # 한 봉에서 둘 다 터치 → 보수적으로 손절 우선
            exit_price = sl_price
            reason = "손절(intrabar 동시터치)"
        elif (intrabar and high >= tp_price) or (not intrabar and profit_pct >= tp):
            exit_price = tp_price if intrabar else close
            reason = "익절"
        elif (intrabar and low <= sl_price) or (not intrabar and profit_pct <= -sl):
            exit_price = sl_price if intrabar else close
            reason = "손절"
        elif peak_profit_pct >= tr_start and close <= peak * (1 - tr_stop / 100):
            exit_price = close
            reason = "트레일링"
        elif (not no_signal_exit) and action_class == "sell-strong":
            exit_price = close
            reason = "매도신호"
        elif held_h >= max_hold_h:
            exit_price = close
            reason = "time-stop"

        if reason:
            ret = (exit_price * (1 - FEE_RATE)) / (entry_price * (1 + FEE_RATE)) - 1
            trades.append({
                "ticker": ticker, "entry": entry_price, "exit": exit_price,
                "ret_pct": ret * 100, "reason": reason, "held_h": held_h,
                "entry_ts": str(hourly.index[entry_idx]), "exit_ts": str(ts),
            })
            in_pos = False

    # Buy&Hold 벤치마크: 백테스트 구간(warmup 이후) 시가→종가 수익률
    bh_start = float(hourly["close"].iloc[warmup])
    bh_end   = float(hourly["close"].iloc[-1])
    bh_ret = (bh_end / bh_start - 1) * 100 if bh_start > 0 else 0.0

    return {"ticker": ticker, "trades": trades, "skipped": None, "bh_ret": bh_ret}


# ─────────────────────────────────────────────
# 집계
# ─────────────────────────────────────────────

def summarize(all_trades: list) -> dict:
    if not all_trades:
        return {"n": 0}
    rets = [t["ret_pct"] for t in all_trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    # 순차 복리 자산곡선 (전체 트레이드를 시간순으로) → MDD
    ordered = sorted(all_trades, key=lambda t: t["exit_ts"])
    equity = 1.0
    peak_eq = 1.0
    mdd = 0.0
    for t in ordered:
        equity *= (1 + t["ret_pct"] / 100)
        peak_eq = max(peak_eq, equity)
        mdd = min(mdd, equity / peak_eq - 1)

    from collections import Counter
    reasons = Counter(t["reason"] for t in all_trades)

    return {
        "n": len(all_trades),
        "win_rate": len(wins) / len(rets) * 100,
        "avg_ret": sum(rets) / len(rets),
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "total_compound": (equity - 1) * 100,
        "mdd": mdd * 100,
        "avg_hold_h": sum(t["held_h"] for t in all_trades) / len(all_trades),
        "reasons": dict(reasons),
    }


def main():
    ap = argparse.ArgumentParser(description="Upbit 자동매매 봇 백테스터")
    ap.add_argument("--tickers", default=",".join(DEFAULT_TICKERS),
                    help="쉼표구분 티커 목록")
    ap.add_argument("--days", type=int, default=90, help="1시간봉 백테스트 기간(일)")
    ap.add_argument("--threshold", type=float, default=12.0,
                    help="매수 진입 점수 기준 (라이브 기본 config.BUY_SCORE_THRESHOLD와 일치)")
    ap.add_argument("--confirm", type=int, default=1,
                    help="진입 확정에 필요한 연속 봉 수(디바운스). 1=즉시, 2+=리페인팅 방지")
    ap.add_argument("--intrabar", action="store_true",
                    help="장중 고저 터치로 손절/익절 판정(기본: 종가)")
    ap.add_argument("--no-signal-exit", action="store_true",
                    help="신호반전(sell-strong) 청산 비활성화 -- 추세추종 진입의 휩쏘 청산 영향 측정용")
    ap.add_argument("--regime-gate", action="store_true",
                    help="시장 레짐 필터: BTC 일봉 하락 추세 구간 신규 진입 차단")
    ap.add_argument("--bb-mode", choices=["current", "flip", "off"], default="current",
                    help="볼린저밴드 점수 로직 실험(실험 1): current=현행, flip=눌림목 매수형, off=BB 미반영")
    ap.add_argument("--min-atr", type=float, default=0.0,
                    help="변동성 하한 게이트(실험 2): atr_pct 미달 종목 진입 차단. 0=비활성(기본, 회귀 없음)")
    ap.add_argument("--tp", type=float, default=config.TAKE_PROFIT_PERCENT,
                    help=f"익절 퍼센트 (기본: config.TAKE_PROFIT_PERCENT={config.TAKE_PROFIT_PERCENT})")
    ap.add_argument("--sl", type=float, default=config.MAX_LOSS_PERCENT,
                    help=f"손절 퍼센트 (기본: config.MAX_LOSS_PERCENT={config.MAX_LOSS_PERCENT})")
    ap.add_argument("--max-hold", type=int, default=config.MAX_HOLD_HOURS,
                    help=f"최대 보유 시간(h) (기본: config.MAX_HOLD_HOURS={config.MAX_HOLD_HOURS})")
    ap.add_argument("--verbose", action="store_true", help="개별 트레이드 출력")
    args = ap.parse_args()

    config.TAKE_PROFIT_PERCENT = args.tp
    config.MAX_LOSS_PERCENT = args.sl
    config.MAX_HOLD_HOURS = args.max_hold

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    print(f"\n백테스트 시작 -- {len(tickers)}종목, {args.days}일, "
          f"threshold={args.threshold}, confirm={args.confirm}, "
          f"intrabar={args.intrabar}, bb_mode={args.bb_mode}, min_atr={args.min_atr}")
    print(f"청산 룰: 익절 +{config.TAKE_PROFIT_PERCENT}% / 손절 -{config.MAX_LOSS_PERCENT}% / "
          f"트레일링 {config.TRAILING_START_PCT}·{config.TRAILING_STOP_PCT}% / "
          f"time-stop {config.MAX_HOLD_HOURS:.0f}h / 수수료 왕복 {FEE_RATE*2*100:.2f}%")
    print("=" * 64)

    all_trades = []
    per_ticker = []
    bh_rets = []
    for tk in tickers:
        res = backtest_ticker(tk, args.days, args.threshold, args.confirm,
                              args.intrabar, args.verbose, args.no_signal_exit,
                              args.regime_gate, args.bb_mode, args.min_atr)
        if res.get("skipped"):
            print(f"  {tk:12s} 스킵 ({res['skipped']})")
            continue
        if res.get("bh_ret") is not None:
            bh_rets.append(res["bh_ret"])
        s = summarize(res["trades"])
        per_ticker.append((tk, s))
        all_trades.extend(res["trades"])
        if s["n"]:
            print(f"  {tk:12s} 거래 {s['n']:3d}건  승률 {s['win_rate']:5.1f}%  "
                  f"평균 {s['avg_ret']:+5.2f}%  복리 {s['total_compound']:+7.1f}%  "
                  f"MDD {s['mdd']:6.1f}%")
        else:
            print(f"  {tk:12s} 거래 0건")
        if args.verbose:
            for t in res["trades"]:
                print(f"      {t['entry_ts']} → {t['exit_ts']}  "
                      f"{t['ret_pct']:+5.2f}%  {t['reason']}")

    print("=" * 64)
    agg = summarize(all_trades)
    if agg["n"] == 0:
        print("전체: 거래 없음")
        return
    print(f"전체 {agg['n']}건  |  승률 {agg['win_rate']:.1f}%  |  "
          f"거래당 기대값 {agg['avg_ret']:+.3f}%")
    print(f"평균 수익 {agg['avg_win']:+.2f}% / 평균 손실 {agg['avg_loss']:+.2f}%  |  "
          f"평균 보유 {agg['avg_hold_h']:.1f}h")
    print(f"순차 복리 수익률 {agg['total_compound']:+.1f}%  |  최대낙폭(MDD) {agg['mdd']:.1f}%")
    print(f"청산 사유 분포: {agg['reasons']}")
    if bh_rets:
        bh_avg = sum(bh_rets) / len(bh_rets)
        print(f"[벤치마크] Buy&Hold 평균 종목 수익률 {bh_avg:+.1f}% "
              f"({len([r for r in bh_rets if r > 0])}/{len(bh_rets)} 종목 상승) "
              f"-- 구간 시장 방향성")
    print()
    # 기대값 해석
    ev = agg["avg_ret"]
    verdict = ("양(+) 기대값 -- 진입 신호에 통계적 우위 있음" if ev > 0.05
               else "기대값 ~0 -- 우위 불명확, 파라미터 재검토 권장" if ev > -0.05
               else "음(-) 기대값 -- 현 룰은 수수료 차감 후 손실")
    print(f"판정: {verdict}")


if __name__ == "__main__":
    main()
