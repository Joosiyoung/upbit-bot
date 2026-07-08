# -*- coding: utf-8 -*-
"""
섀도우 로그 분석 -- 매수 시점 게이트 상태(shadow)가 실제 거래 성과를 가르는지 실측 검증한다.

`core/trader.py`는 매수 시점마다 `shadow` 필드(regime_ok·fg_block·atr_pct·pullback_pct·bb_pct)를
trade_history.jsonl에 기록한다. 이 값들은 매매 판단에 반영되지 않는 순수 관찰용 데이터라
"만약 이 피처로 게이트를 걸었다면 성과가 갈렸을까"를 사후에 검증할 수 있다.
docs/UPGRADE_NOTES.md §1.5(신호 재설계) 재판정에 표본이 쌓일 때마다 재사용하는 진단 도구.

매칭 방법 (FIFO):
  같은 티커에서 shadow가 있는 매수를 발생 순서대로 큐에 쌓고, 그 티커의 다음 매도가 나오면
  큐의 맨 앞(가장 오래된) 매수와 짝지어 성과(profit_pct)를 확정한다.

한계:
  - 추가매수(물타기)도 개별 매수 이벤트로 큐에 들어가므로, 한 매도가 여러 매수 슬롯의 평균
    진입가에 대응하더라도 이 스크립트는 FIFO로 1:1 매칭한다. 정확한 슬롯 단위 매칭이 아니다.
  - 동일 티커를 반복 매매하면 그 티커의 시장 상황(변동성 레짐 등)이 여러 페어에 걸쳐 겹쳐
    표본 간 상관이 생길 수 있다(엄밀한 독립표본 아님) -- 분위 분석은 방향성 참고용으로만 쓴다.
  - 미청산(매도 미확정) 매수는 성과 계산에서 제외된다.

사용법:
  python scripts/shadow_analysis.py                              # data/trade_history.jsonl 기본
  python scripts/shadow_analysis.py --file data/trade_history.jsonl
  python scripts/shadow_analysis.py --verbose                    # 개별 페어 상세 덤프 포함
"""

import argparse
import json
import os
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(PROJECT_ROOT, "data", "trade_history.jsonl")

FEATURES = ["atr_pct", "pullback_pct", "bb_pct"]


def load_rows(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: (r.get("date", ""), r.get("time", "")))
    return rows


def build_pairs(rows: list) -> tuple:
    """shadow가 있는 매수를 같은 티커의 다음 매도와 FIFO 매칭한다.

    반환: (pairs, open_buys) -- pairs = [(shadow, profit_pct, ticker, date, reason), ...]
    """
    open_buys = defaultdict(list)
    pairs = []
    for r in rows:
        t = r.get("type")
        tk = r.get("ticker")
        if t == "buy":
            if r.get("shadow"):
                open_buys[tk].append(r)
        elif t == "sell":
            p = r.get("profit_pct")
            if p is None:
                p = r.get("profit")
            if open_buys[tk]:
                b = open_buys[tk].pop(0)
                if p is not None:
                    pairs.append((b["shadow"], float(p), tk, b.get("date"), r.get("reason", "")))
    return pairs, open_buys


def print_summary(pairs: list):
    rets = [p for _, p, *_ in pairs]
    wins = [p for p in rets if p > 0]
    print(f"매도와 매칭 완료(성과 확정): {len(pairs)}건")
    print(f"승률 {len(wins) / len(rets) * 100:.1f}%  평균 {sum(rets) / len(rets):+.3f}%")


def print_feature_quantiles(pairs: list):
    print("\n--- 피처별 분위 분석 (중앙값 기준 상/하 비교) ---")
    for ft in FEATURES:
        vals = sorted(x[0].get(ft) for x in pairs if x[0].get(ft) is not None)
        if len(vals) < 6:
            print(f"[{ft}] 표본 부족({len(vals)}건) -- 분위 분석 생략")
            continue
        med = vals[len(vals) // 2]
        lo = [p for s, p, *_ in pairs if s.get(ft) is not None and s[ft] < med]
        hi = [p for s, p, *_ in pairs if s.get(ft) is not None and s[ft] >= med]
        print(f"[{ft}] 중앙값={med:.2f}")
        if lo:
            wl = sum(1 for p in lo if p > 0)
            print(f"  하위 {len(lo)}건: 승률 {wl / len(lo) * 100:.1f}%  평균 {sum(lo) / len(lo):+.3f}%")
        if hi:
            wh = sum(1 for p in hi if p > 0)
            print(f"  상위 {len(hi)}건: 승률 {wh / len(hi) * 100:.1f}%  평균 {sum(hi) / len(hi):+.3f}%")


def print_reason_breakdown(pairs: list):
    print("\n--- 청산 사유별 분해 ---")
    by_reason = defaultdict(list)
    for _, p, _tk, _date, reason in pairs:
        key = reason.split("(")[0].strip() if reason else "(사유 없음)"
        by_reason[key].append(p)
    for key, rets in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        wins = sum(1 for p in rets if p > 0)
        print(f"  {key}: {len(rets)}건  승률 {wins / len(rets) * 100:.1f}%  "
              f"누적 {sum(rets):+.2f}%  평균 {sum(rets) / len(rets):+.3f}%")


def print_pair_dump(pairs: list):
    print("\n--- 개별 페어 상세 ---")
    for shadow, p, tk, date, reason in pairs:
        feat_str = " ".join(f"{k}={shadow.get(k):.2f}" for k in FEATURES if shadow.get(k) is not None)
        print(f"  {date} {tk:<10} {p:+7.2f}%  regime_ok={shadow.get('regime_ok')} "
              f"fg_block={shadow.get('fg_block')} {feat_str}  [{reason}]")


def main():
    parser = argparse.ArgumentParser(description="섀도우 로그(매수 게이트 상태) ↔ 거래 성과 분석")
    parser.add_argument("--file", default=DEFAULT_FILE, help="거래 이력 JSONL 경로 (기본: data/trade_history.jsonl)")
    parser.add_argument("--verbose", action="store_true", help="개별 페어 상세 덤프 출력")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"파일을 찾을 수 없음: {args.file}")
        sys.exit(0)

    rows = load_rows(args.file)
    pairs, open_buys = build_pairs(rows)

    shadow_buy_count = sum(1 for r in rows if r.get("type") == "buy" and r.get("shadow"))
    print(f"섀도우 매수 총: {shadow_buy_count}건")

    if not pairs:
        print("매도와 매칭된 성과 확정 페어가 없음 -- 분석할 표본 없음.")
        sys.exit(0)

    print_summary(pairs)
    print(f"미청산(성과 미확정): {sum(len(v) for v in open_buys.values())}건")

    print_feature_quantiles(pairs)
    print_reason_breakdown(pairs)

    if args.verbose:
        print_pair_dump(pairs)


if __name__ == "__main__":
    main()
