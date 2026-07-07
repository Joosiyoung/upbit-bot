"""weighted_score 특성 테스트 — 진입 점수 가중치를 스펙으로 고정 (Phase B).

core.scoring.weighted_score는 라이브(data_builder.py 두 함수)와 백테스터가
공유하는 유일한 가중 합산 구현. 가중치가 바뀌면 백테스트-라이브 신호가 함께
움직여야 하며, 어느 한쪽만 바뀌는 드리프트를 이 테스트가 막는다.
"""

import pytest

from core.scoring import WEIGHT_LONG, WEIGHT_MID, WEIGHT_SHORT, weighted_score


def test_weights_are_the_documented_ratio():
    # 재설계 문서·기존 하드코딩 값(2.5 / 2 / 0.5)과 일치해야 한다
    assert (WEIGHT_LONG, WEIGHT_MID, WEIGHT_SHORT) == (2.5, 2.0, 0.5)


def test_weighted_score_full_formula():
    # 4×2.5 + 3×2 + 2×0.5 = 10 + 6 + 1 = 17
    assert weighted_score(4, 3, 2) == pytest.approx(17.0)


def test_short_defaults_to_zero_for_backtest():
    # 백테스트는 1분봉 슬롯 미사용 → short 생략 시 0으로 취급
    assert weighted_score(4, 3) == pytest.approx(4 * 2.5 + 3 * 2.0)


def test_matches_legacy_hardcoded_expression():
    """리팩토링 전 하드코딩 식(sc_1d*2.5 + sc_1h*2 + sc_1m*0.5)과 동일함을 보증."""
    for sc_1d, sc_1h, sc_1m in [(0, 0, 0), (2, -1, 3), (-4, 5, -2), (8, 8, 8)]:
        legacy = sc_1d * 2.5 + sc_1h * 2 + sc_1m * 0.5
        assert weighted_score(sc_1d, sc_1h, sc_1m) == pytest.approx(legacy)


def test_negative_scores_propagate():
    assert weighted_score(-2, -2, -2) == pytest.approx(-10.0)


def test_threshold_12_reachable_from_daily_alone():
    """일봉 강세(+5) + 1시간봉 중립이면 12.5 → threshold 12 도달 가능 확인."""
    assert weighted_score(5, 0, 0) == pytest.approx(12.5)
    assert weighted_score(5, 0, 0) >= 12
