import logging

_logger = logging.getLogger(__name__)

# 하드코딩 fallback 유니버스 (KIS API 실패 시 사용)
# 반도체·2차전지·방산·성장 섹터 위주 — 추세추종 점수체계에서 매수 신호 발생 가능 종목으로 교체
FALLBACK_UNIVERSE = [
    # 반도체
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    # 2차전지
    ("006400", "삼성SDI"),
    ("247540", "에코프로비엠"),
    # 방산
    ("012450", "한화에어로스페이스"),
    ("064350", "현대로템"),
    # 자동차
    ("005380", "현대차"),
    ("000270", "기아"),
    # 바이오
    ("207940", "삼성바이오로직스"),
    ("068270", "셀트리온"),
    # 플랫폼·핀테크
    ("377300", "카카오페이"),
    ("035420", "NAVER"),
    # 철강·소재
    ("005490", "POSCO홀딩스"),
    # 조선
    ("009540", "HD한국조선해양"),
    ("010140", "삼성중공업"),
    # 금융 (점수 가장 높았던 유일한 종목 유지)
    ("055550", "신한지주"),
    # 엔터·콘텐츠
    ("035900", "JYP Ent."),
    # 반도체 장비
    ("042700", "한미반도체"),
]

# 하위 호환 — 기존 코드에서 직접 호출하는 곳이 있을 경우 유지
STOCK_UNIVERSE = FALLBACK_UNIVERSE


def get_universe() -> list[tuple[str, str]]:
    """하드코딩 유니버스 반환 (하위 호환용)."""
    return FALLBACK_UNIVERSE


def get_dynamic_universe(
    kis_client,
    slot_budget: float,
    limit: int = 30,
) -> list[tuple[str, str]]:
    """KIS 거래량 순위 기반 동적 유니버스 반환.

    1. KIS API로 상위 limit*2 종목 조회
    2. 실패 시 FALLBACK_UNIVERSE 사용
    3. slot_budget > 0 이면 현재가 <= slot_budget 종목만 통과
    4. limit개까지 반환
    5. 결과가 비면 FALLBACK_UNIVERSE 반환
    """
    candidates = kis_client.get_top_volume_stocks(limit * 2)

    if not candidates:
        _logger.info("동적 유니버스: KIS API 실패 → FALLBACK_UNIVERSE 사용 (%d종목)", len(FALLBACK_UNIVERSE))
        return FALLBACK_UNIVERSE

    _logger.info("동적 유니버스: KIS 거래량 순위 %d종목 후보 조회 완료", len(candidates))

    if slot_budget <= 0:
        result = candidates[:limit]
        _logger.info("동적 유니버스: 가격 필터 없음 → %d종목 반환", len(result))
        return result if result else FALLBACK_UNIVERSE

    filtered = []
    for code, name in candidates:
        if len(filtered) >= limit:
            break
        price = kis_client.get_current_price(code)
        if price is None:
            continue
        if price <= slot_budget:
            filtered.append((code, name))

    if not filtered:
        _logger.info("동적 유니버스: 가격 필터 후 0종목 → FALLBACK_UNIVERSE 사용 (%d종목)", len(FALLBACK_UNIVERSE))
        return FALLBACK_UNIVERSE

    _logger.info(
        "동적 유니버스: 슬롯 예산 %.0f원 이하 필터 → %d종목 반환",
        slot_budget, len(filtered),
    )
    return filtered
