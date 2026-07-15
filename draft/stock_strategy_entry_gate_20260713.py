# -*- coding: utf-8 -*-
"""
초안 — scripts/stock_backtest.py 진입 분기에 라이브 등락률 게이트 반영.

[목적]
라이브 `_stock_worker()`는 진입 전 당일 등락률 게이트(STOCK_ENTRY_CHANGE_MIN/MAX)를
적용하지만, 현행 백테스터는 score만 보고 진입해 라이브가 절대 안 살 갭업 추격까지 평가한다.
이 초안은 백테스터 진입 조건을 라이브와 일치시키는 "정합성 수정"이다(전략 변경 아님).

[통합 위치]
scripts/stock_backtest.py 의 backtest_ticker() 진입 분기(현재 124~130행) 교체.
score_signal() 시그니처는 건드리지 않는다. B안(vol 게이트)은 주석의 플래그로 함께 제안.

[실측 근거] draft/stock_strategy_entry_gate_20260713.md 참조.
  180일: 게이트 없음 복리 -16.5% → 등락률게이트 +3.2% → +vol<1.5 +13.7%
  90일 : 게이트 없음 복리 -56.2% → 등락률게이트 -39.3% → +vol<1.5 -18.4% (하락장, 여전히 음수)

[주의]
  - 백테스트는 장중가가 없으므로 '전일 종가 대비 당일 종가'로 등락률 계산(라이브는 현재가 기준).
  - config 값을 하드코딩하지 말고 그대로 읽어 라이브와 단일 소스 유지.
  - vol 게이트(APPLY_VOL_GATE)는 stock-param-optimizer 스윕 통과 전까지 기본 False.
"""

# ── 통합 시 stock_backtest.py 상단 근처에 둘 플래그 (또는 CLI 인자로 승격) ──
APPLY_ENTRY_CHANGE_GATE = True     # A안: 라이브 등락률 게이트 반영 (정합성 수정, 기본 ON)
APPLY_VOL_GATE = False             # B안: 거래량 상한 게이트 (스윕 검증 전 OFF)
VOL_GATE_MAX = 1.5                 # B안 채택 시 상한. optimizer로 {1.0,1.2,1.5} 스윕할 것


def entry_allowed(daily, i, ind, config) -> bool:
    """라이브 진입 게이트를 백테스트에 복제. 진입 허용 시 True.

    daily : 지표 부착된 일봉 DataFrame
    i     : 현재 봉 인덱스 (진입 후보 시점)
    ind   : row_to_ind(daily, i) 결과 dict (volume_ratio 포함)
    config: core.config 모듈
    """
    # ── 게이트 1: 당일 등락률 (라이브 STOCK_ENTRY_CHANGE_MIN/MAX 복제) ──
    if APPLY_ENTRY_CHANGE_GATE and i > 0:
        prev_close = float(daily["close"].iloc[i - 1])
        cur_close = float(daily["close"].iloc[i])
        if prev_close > 0:
            change_pct = (cur_close / prev_close - 1) * 100
            if not (config.STOCK_ENTRY_CHANGE_MIN
                    <= change_pct
                    <= config.STOCK_ENTRY_CHANGE_MAX):
                return False

    # ── 게이트 2 (B안, 조건부): 거래량 급증 진입 차단 ──
    # 실측: vol<1.0 진입이 +1.192%로 최고, 급증(>=1.5)은 전 구간 음수(180일).
    # 단 90일 표본이 작아 단독 근거 불가 → optimizer 통과 전 OFF.
    if APPLY_VOL_GATE:
        vol = ind.get("volume_ratio", 1.0)
        if vol > VOL_GATE_MAX:
            return False

    return True


# ─────────────────────────────────────────────
# stock_backtest.py backtest_ticker() 진입 분기 교체 예시 (diff 형태)
# ─────────────────────────────────────────────
#
# 현행 (124~130행):
#
#     if not in_pos:
#         if sc >= threshold:
#             in_pos      = True
#             entry_price = close
#             entry_idx   = i
#             peak        = close
#         continue
#
# 교체안:
#
#     if not in_pos:
#         if sc >= threshold and entry_allowed(daily, i, ind, config):
#             in_pos      = True
#             entry_price = close
#             entry_idx   = i
#             peak        = close
#         continue
#
# 이후 청산 로직·집계·수수료 회계는 전혀 변경하지 않는다.
# entry_allowed는 이 파일 상단 함수를 stock_backtest.py로 이식하거나 import.
