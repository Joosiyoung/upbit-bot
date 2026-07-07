# ─────────────────────────────────────────────
# 진입 점수 가중 합산 — 라이브(data_builder.py)와 백테스터가 공유
# ─────────────────────────────────────────────
#
# [Phase B 리팩토링 배경, 2026-07-07]
# 멀티 타임프레임 가중치(장기 2.5 · 중기 2 · 단기 0.5)가 세 곳에 중복돼 있었다:
#   - data_builder.build_market_data  (진입 스캔: 일봉·1시간봉·1분봉)
#   - data_builder.build_analysis_data (보유 평가: 일봉·4시간봉·1시간봉)
#   - scripts/backtest.py             (백테스트: 일봉·1시간봉, 1분봉은 0)
# 가중치가 한 곳에서 바뀌고 다른 곳이 누락되면 백테스트-라이브 신호가 조용히
# 어긋난다(청산 로직 이중 구현과 같은 종류의 드리프트). 단일 구현으로 통일한다.
#
# 주의: 세 경로가 공유하는 것은 '가중치 비율'이지 '타임프레임'이 아니다.
# 어떤 봉을 장기/중기/단기 슬롯에 넣을지는 호출부가 정한다(진입 스캔은 1분봉을,
# 보유 평가는 4시간봉을 중기에 사용하는 식). 백테스트는 1분봉 대량 수집이
# 어려워 단기 슬롯을 0으로 두므로 라이브보다 진입이 약간 보수적이다.

# 멀티 타임프레임 가중치 (장기 / 중기 / 단기)
WEIGHT_LONG = 2.5
WEIGHT_MID = 2.0
WEIGHT_SHORT = 0.5


def weighted_score(long_score: float, mid_score: float,
                   short_score: float = 0.0) -> float:
    """타임프레임별 점수를 가중 합산한 진입/보유 종합 점수.

    long_score : 장기(일봉) score_signal 값
    mid_score  : 중기(1시간봉 또는 4시간봉) score_signal 값
    short_score: 단기(1분봉) score_signal 값. 백테스트에선 0(1분봉 미수집).
    """
    return (long_score * WEIGHT_LONG
            + mid_score * WEIGHT_MID
            + short_score * WEIGHT_SHORT)
