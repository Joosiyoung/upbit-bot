import logging

_logger = logging.getLogger(__name__)

# 하드코딩 fallback 유니버스 (KIS API 실패 시 사용)
FALLBACK_UNIVERSE = [
    # 통신
    ("017670", "SK텔레콤"),
    ("030200", "KT"),
    # 금융
    ("055550", "신한지주"),
    ("086790", "하나금융지주"),
    ("316140", "우리금융지주"),
    ("032830", "삼성생명"),
    # IT·플랫폼
    ("035720", "카카오"),
    ("323410", "카카오뱅크"),
    ("036570", "엔씨소프트"),
    # 에너지·소재
    ("096770", "SK이노베이션"),
    ("015760", "한국전력"),
    ("003670", "포스코퓨처엠"),
    ("034020", "두산에너빌리티"),
    ("011170", "롯데케미칼"),
    # 자동차·기계
    ("000270", "기아"),
    ("028260", "삼성물산"),
    ("003550", "LG"),
    # 바이오
    ("068270", "셀트리온"),
    # 조선
    ("009540", "HD한국조선해양"),
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
