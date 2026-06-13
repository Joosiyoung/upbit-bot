# -*- coding: utf-8 -*-
"""매매 시작/중지/상태 공통 로직

대시보드(Flask 라우트)와 Telegram 명령 봇이 같은 코드를 공유한다.
별도 모듈로 분리한 이유: app.py(Flask)와 telegram_bot.py가 서로 import하지 않고
이 모듈만 바라보게 하여 순환 import를 방지.
"""

import logging
from datetime import datetime

from core import config
from core import notifier
from core.upbit_client import UpbitClient
from core.trader import (
    _trading_state, _trading_lock, _sell_cooldown,
    TRADING_BLACKLIST, MAX_POSITIONS, FEE_RATE,
    _log_trade, _execute_live_sell, save_state, get_risk_snapshot,
)
from core.data_builder import _market_cache, _market_lock, _cache, _cache_lock


# ─────────────────────────────────────────────
# 매매 시작
# ─────────────────────────────────────────────

def start_trading(live_mode: bool) -> dict:
    """자동 매매 시작. 반환: {"ok": bool, ...} (Flask 응답과 동일 구조)"""
    # 중복 시작 가드 — 이미 실행 중이면 포지션/가상잔고가 리셋되는 사고 방지
    with _trading_lock:
        if _trading_state["enabled"]:
            cur = "실거래" if _trading_state["live"] else "시뮬레이션"
            return {"ok": False, "error": f"이미 {cur} 매매가 실행 중입니다. 먼저 중지하세요."}

    now_str = datetime.now().strftime("%H:%M:%S")
    client  = UpbitClient()

    # 기존 보유 코인(BLACKLIST 제외)을 초기 포지션으로 가져오기
    initial_positions = {}
    try:
        if client.upbit:
            for b in client.upbit.get_balances():
                currency = b['currency']
                if currency == 'KRW':
                    continue
                ticker = f"KRW-{currency}"
                if ticker in TRADING_BLACKLIST:
                    continue
                volume = float(b['balance'])
                if volume <= 0:
                    continue
                avg_price = float(b.get('avg_buy_price', 0))
                # avg_buy_price가 0인 경우(에어드롭·이벤트 지급 등) 현재가를 진입가로 사용
                if avg_price <= 0:
                    try:
                        avg_price = client.get_current_price(ticker) or 0
                    except Exception:
                        pass
                if avg_price <= 0:
                    continue
                # 최소주문금액 미만 dust는 슬롯을 점유시키지 않음
                if volume * avg_price < config.MIN_ORDER_KRW:
                    continue
                if len(initial_positions) >= MAX_POSITIONS:
                    break
                initial_positions[ticker] = {
                    "entry_price": avg_price,
                    "amount_krw":  round(volume * avg_price),
                    "quantity":    volume,
                    "entry_time":  now_str,
                    "entry_ts":    datetime.now().isoformat(),
                    "bot_bought":  False,
                    "slot_count":  1,
                    "peak_price":  avg_price,
                }
    except Exception as e:
        logging.warning("초기 포지션 로드 실패 (API 오류로 빈 포지션으로 시작): %s", e)

    # 시뮬레이션: 현재 KRW 잔고를 가상 예산으로 사용
    sim_krw = 0.0
    if not live_mode:
        try:
            sim_krw = client.get_balance_krw()
        except Exception:
            sim_krw = 100_000.0  # API 오류 시 폴백

    imported = len(initial_positions)
    initial_invested = sum(p["amount_krw"] for p in initial_positions.values())
    with _trading_lock:
        if _trading_state["enabled"]:   # 위 가드 이후 경쟁 시작 방지 (이중 확인)
            cur = "실거래" if _trading_state["live"] else "시뮬레이션"
            return {"ok": False, "error": f"이미 {cur} 매매가 실행 중입니다."}
        _trading_state["enabled"]    = True
        _trading_state["live"]       = live_mode
        _trading_state["sim_krw"]    = sim_krw
        _trading_state["sim_initial_total"] = sim_krw + initial_invested  # 시뮬 성과 기준점
        _trading_state["positions"]  = initial_positions
        _trading_state["last_check"] = None
        _trading_state["epoch"]     += 1   # 진행 중이던 사이클의 낡은 쓰기 무효화
        _trading_state["status_msg"] = (
            f"실거래 활성화됨 (보유 {imported}종목 포함, 슬롯 {imported}/{MAX_POSITIONS})" if live_mode
            else f"시뮬레이션 활성화됨 (보유 {imported}종목 포함, 가상잔고 {sim_krw:,.0f}원)"
        )
    save_state()

    # 주의: _ai_cache(F&G·활성마켓)는 비우지 않는다 — 비우면 워커가 다시 채울 때까지
    # (최대 5분) F&G 매수 게이트와 상장폐지 감지가 공백이 되어 리스크 보호가 약화됨.

    notifier.notify_event(
        "▶️ 자동 매매 시작" if live_mode else "▶️ 시뮬레이션 시작",
        f"모드: {'실거래' if live_mode else '시뮬레이션'}\n"
        f"초기 포지션: {imported}종목 (슬롯 {imported}/{MAX_POSITIONS})"
        + ("" if live_mode else f"\n가상잔고: {sim_krw:,.0f}원"))
    return {"ok": True, "enabled": True, "live": live_mode, "sim_krw": sim_krw, "imported": imported}


# ─────────────────────────────────────────────
# 매매 중지
# ─────────────────────────────────────────────

def stop_trading(mode: str = "liquidate") -> dict:
    """자동 매매 중지
    mode=liquidate: 봇 매수 코인 시장가 청산 후 중지
    mode=hold:      매도 없이 추적만 종료 후 중지
    """
    client  = UpbitClient()
    now_str = datetime.now().strftime("%H:%M:%S")

    # 먼저 매매를 비활성화하고 epoch을 올려 진행 중인 사이클의 추가 주문을 차단
    with _trading_lock:
        was_enabled = _trading_state["enabled"]
        _trading_state["enabled"]  = False
        _trading_state["epoch"]   += 1
        positions_snap = dict(_trading_state["positions"])
        live_mode      = _trading_state["live"]

    if not was_enabled and not positions_snap:
        return {"ok": False, "error": "실행 중인 매매가 없습니다.", "enabled": False, "sell_failed": []}

    # 마켓 캐시에서 현재가 가져오기
    with _market_lock:
        market_map = {c["ticker"]: c["current"] for c in _market_cache.get("coins", [])}

    sell_failed = []   # 청산 실패 종목 (포지션 유지하여 사용자에게 표시)

    for ticker, pos in positions_snap.items():
        if ticker in TRADING_BLACKLIST:
            continue

        current_price = market_map.get(ticker)
        if current_price is None:
            try:
                current_price = client.get_current_price(ticker)
            except Exception:
                pass

        entry_price = pos["entry_price"]
        profit_pct  = (
            (current_price - entry_price) / entry_price * 100
            if (current_price and entry_price > 0) else 0
        )

        log_type      = "sell"
        reason_prefix = "매매 중지 보유 유지"

        if mode == "liquidate":
            reason_prefix = "매매 중지 전액 청산"
            if live_mode and client.upbit:
                ok, _qty, err = _execute_live_sell(client, ticker, pos["quantity"])
                if not ok:
                    sell_failed.append(ticker)
                    log_type      = "sell_fail"
                    reason_prefix = f"청산 실패 [{err}]"
                    logging.warning("청산 매도 실패 [%s]: %s", ticker, err)
                else:
                    with _trading_lock:
                        _sell_cooldown[ticker] = datetime.now()   # 청산 후 즉시 재매수 방지
        else:
            # hold 모드: 실제 매도 없이 추적만 종료 → sell 로 기록하지 않음
            log_type = "hold"

        _log_trade({
            "time":       now_str,
            "type":       log_type,
            "ticker":     ticker,
            "reason":     f"{reason_prefix} ({profit_pct:+.1f}%)",
            "price":      current_price or entry_price,
            "amount":     pos["amount_krw"],
            "profit_pct": round(profit_pct, 2),
            "live":       live_mode,
        })

    if mode == "liquidate":
        if sell_failed:
            stop_msg = f"자동 매매 중지됨 (청산 실패 {len(sell_failed)}종목: {', '.join(sell_failed)} — 수동 확인 필요)"
        else:
            stop_msg = "자동 매매 중지됨 (포지션 청산 완료)"
    else:
        stop_msg = "자동 매매 중지됨 (포지션 보유 유지)"

    with _trading_lock:
        # 청산 실패 종목은 포지션에 남겨 두어 UI에서 확인 가능하게 함
        _trading_state["positions"]  = {
            t: p for t, p in _trading_state["positions"].items() if t in sell_failed
        }
        _trading_state["status_msg"] = stop_msg
    save_state()

    # 주의: _ai_cache(F&G·활성마켓)는 중지 시에도 비우지 않는다 — 다음 시작 시
    # 게이트 공백을 피하고, 워커가 계속 갱신하도록 둔다.

    notifier.notify_event("⏹️ 자동 매매 중지", stop_msg)
    return {"ok": True, "enabled": False, "sell_failed": sell_failed}


# ─────────────────────────────────────────────
# 상태 요약 (Telegram /status 용)
# ─────────────────────────────────────────────

def status_summary() -> str:
    """현재 매매 상태를 Telegram HTML 메시지로 요약"""
    with _market_lock:
        market_map = {c["ticker"]: c["current"] for c in _market_cache.get("coins", [])}
    with _cache_lock:
        for h in _cache.get("holdings", []):
            if h["ticker"] not in market_map:
                market_map[h["ticker"]] = h.get("current_price")

    with _trading_lock:
        enabled    = _trading_state["enabled"]
        live_mode  = _trading_state["live"]
        sim_krw    = _trading_state["sim_krw"]
        status_msg = _trading_state["status_msg"]
        last_check = _trading_state["last_check"]
        positions  = dict(_trading_state["positions"])

    lines = []
    if enabled:
        mode = "🔴 실거래" if live_mode else "🟡 시뮬레이션"
        lines.append(f"<b>상태: 매매 중</b> ({mode})")
    else:
        lines.append("<b>상태: 중지됨</b>")
    lines.append(f"메시지: {status_msg}")
    if last_check:
        lines.append(f"마지막 체크: {last_check}")

    # 잔고
    if enabled and not live_mode:
        lines.append(f"가상 잔고: {sim_krw:,.0f}원")
    else:
        try:
            krw = UpbitClient().get_balance_krw()
            lines.append(f"KRW 잔고: {krw:,.0f}원")
        except Exception:
            pass

    # 포지션
    if positions:
        lines.append(f"\n<b>보유 포지션 ({len(positions)}/{MAX_POSITIONS})</b>")
        total_invested = 0.0
        total_value    = 0.0
        for ticker, pos in positions.items():
            entry   = pos.get("entry_price", 0)
            amount  = pos.get("amount_krw", 0)
            current = market_map.get(ticker)
            total_invested += amount
            if current and entry > 0:
                # 시뮬·실거래 동일 회계: 매도 수수료 차감 실수령 기준 (대시보드와 일치)
                net = current * (1 - FEE_RATE)
                pct = (net - entry) / entry * 100
                total_value += amount * (1 + pct / 100)
                emoji = "📈" if pct >= 0 else "📉"
                lines.append(f"{emoji} {ticker}: {pct:+.2f}% ({amount:,.0f}원)")
            else:
                total_value += amount
                lines.append(f"• {ticker}: 현재가 조회 불가 ({amount:,.0f}원)")
        if total_invested > 0:
            total_pct = (total_value - total_invested) / total_invested * 100
            lines.append(f"합산: <b>{total_pct:+.2f}%</b> (투자 {total_invested:,.0f}원)")
    else:
        lines.append("\n보유 포지션 없음")

    # 리스크 상태
    risk = get_risk_snapshot()
    if risk.get("buy_block_reason"):
        lines.append(f"\n🛑 매수 차단: {risk['buy_block_reason']}")
    if risk.get("daily_realized_krw"):
        lines.append(f"당일 실현손익: {risk['daily_realized_krw']:+,.0f}원")

    return "\n".join(lines)
