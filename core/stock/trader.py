# -*- coding: utf-8 -*-
import html
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta

from core import config
from core import notifier

_KST = config.KST

FEE_RATE = config.STOCK_FEE_RATE

STOCK_STATE_FILE = os.path.join("data", "stock_state.json")
STOCK_TRADE_LOG_FILE = os.path.join("data", "stock_trade_history.jsonl")

_stock_trading_state: dict = {
    "enabled":    False,
    "mode":       "sim",
    "sim_krw":    0.0,
    "started_at": None,
}
_stock_positions: dict[str, dict] = {}
_stock_lock = threading.Lock()


def is_market_hours() -> bool:
    now = datetime.now(_KST)
    if now.weekday() > 4:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 15 * 60 + 30


def _trading_days_held(entry_time: datetime) -> int:
    now = datetime.now(_KST)
    days = 0
    cur = entry_time.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        if cur.weekday() <= 4:
            days += 1
        cur += timedelta(days=1)
    return max(0, days - 1)


def _log_trade(record: dict):
    os.makedirs(os.path.dirname(STOCK_TRADE_LOG_FILE), exist_ok=True)
    with open(STOCK_TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _save_state():
    os.makedirs(os.path.dirname(STOCK_STATE_FILE), exist_ok=True)
    state_copy = dict(_stock_trading_state)
    if isinstance(state_copy.get("started_at"), datetime):
        state_copy["started_at"] = state_copy["started_at"].isoformat()

    positions_copy = {}
    for code, pos in _stock_positions.items():
        p = dict(pos)
        if isinstance(p.get("entry_time"), datetime):
            p["entry_time"] = p["entry_time"].isoformat()
        positions_copy[code] = p

    with open(STOCK_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"state": state_copy, "positions": positions_copy}, f, ensure_ascii=False)


def _load_state():
    global _stock_positions
    if not os.path.exists(STOCK_STATE_FILE):
        return
    try:
        with open(STOCK_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        state = data.get("state", {})
        if state.get("started_at"):
            dt = datetime.fromisoformat(state["started_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_KST)
            state["started_at"] = dt
        with _stock_lock:
            _stock_trading_state.update(state)
            raw_pos = data.get("positions", {})
            for code, p in raw_pos.items():
                if p.get("entry_time"):
                    dt = datetime.fromisoformat(p["entry_time"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_KST)
                    p["entry_time"] = dt
            _stock_positions = raw_pos
    except Exception:
        logging.exception("주식 상태 파일 로드 오류")


def start_stock_trading(budget_krw: float = 0.0) -> str:
    with _stock_lock:
        if _stock_trading_state["enabled"]:
            return "이미 주식 시뮬 매매가 실행 중입니다."

    if budget_krw <= 0:
        from core.stock.kis_client import KisClient
        client = KisClient(is_sandbox=config.KIS_IS_SANDBOX)
        budget_krw = client.get_cash_balance()
        if budget_krw <= 0:
            return "KIS 잔고 조회 실패 또는 주문가능현금 0원입니다. 금액을 직접 입력하세요.\n예: /stock_start_sim 1000000"

    with _stock_lock:
        if _stock_trading_state["enabled"]:
            return "이미 주식 시뮬 매매가 실행 중입니다."
        _stock_trading_state["enabled"] = True
        _stock_trading_state["mode"] = "sim"
        _stock_trading_state["sim_krw"] = budget_krw
        _stock_trading_state["started_at"] = datetime.now(_KST)
        _stock_positions.clear()
        _save_state()

    t = threading.Thread(target=_stock_worker, daemon=True, name="stock-worker")
    t.start()
    msg = f"주식 시뮬 매매 시작 — 예산 {budget_krw:,.0f}원"
    notifier.notify_event("주식 시뮬 매매 시작", f"예산 {budget_krw:,.0f}원")
    return msg


def stop_stock_trading() -> str:
    with _stock_lock:
        _stock_trading_state["enabled"] = False
        _save_state()
    notifier.notify_event("주식 매매 중지")
    return "주식 매매 중지됨"


def build_stock_status_msg() -> str:
    from core.stock.kis_client import KisClient
    with _stock_lock:
        enabled = _stock_trading_state["enabled"]
        sim_krw = _stock_trading_state["sim_krw"]
        positions = dict(_stock_positions)

    status_str = "실행 중" if enabled else "중지"
    lines = [
        f"<b>주식 시뮬 매매 — {html.escape(status_str)}</b>",
        f"시뮬 잔고: {sim_krw:,.0f}원",
        f"슬롯: {len(positions)}/{config.STOCK_MAX_POSITIONS}",
    ]

    if positions:
        client = KisClient(is_sandbox=config.KIS_IS_SANDBOX)
        now_dt = datetime.now(_KST)
        lines.append("")
        for code, pos in positions.items():
            name = html.escape(pos.get("name", code))
            entry_price = pos.get("entry_price", 0)
            entry_time = pos.get("entry_time")

            try:
                current_price = client.get_current_price(code)
            except Exception:
                current_price = entry_price

            if current_price and entry_price:
                ret = (current_price * (1 - FEE_RATE) - entry_price * (1 + FEE_RATE)) / (entry_price * (1 + FEE_RATE)) * 100
                pct_str = f"{ret:+.2f}%"
            else:
                pct_str = "조회 불가"

            held_days = 0
            if entry_time:
                held_days = _trading_days_held(entry_time)

            lines.append(f"• {name}({html.escape(code)})  {pct_str}  진입 {held_days}일차")

    return "\n".join(lines)


def _stock_worker():
    from core.stock.kis_client import KisClient
    from core.stock.universe import get_universe
    from core.indicators import add_all_indicators, get_latest_indicators
    from core.analysis import score_signal, action_from_score

    close_h, close_m = (int(x) for x in config.STOCK_BUY_CLOSE_TIME.split(":"))

    while _stock_trading_state["enabled"]:
        try:
            if not is_market_hours():
                time.sleep(30)
                continue

            client = KisClient(is_sandbox=config.KIS_IS_SANDBOX)
            now = datetime.now(_KST)

            # ── 청산 체크 ──
            with _stock_lock:
                codes_to_check = list(_stock_positions.keys())

            for code in codes_to_check:
                with _stock_lock:
                    if not _stock_trading_state["enabled"]:
                        break
                    pos = _stock_positions.get(code)
                    if pos is None:
                        continue

                try:
                    current_price = client.get_current_price(code)
                except Exception:
                    logging.warning("주식 현재가 조회 실패: %s", code)
                    continue

                if not current_price:
                    continue

                with _stock_lock:
                    pos = _stock_positions.get(code)
                    if pos is None:
                        continue
                    entry_price = pos["entry_price"]
                    quantity = pos["quantity"]
                    peak = pos["peak"]
                    entry_time = pos["entry_time"]
                    name = pos.get("name", code)
                    peak = max(peak, current_price)
                    _stock_positions[code]["peak"] = peak

                profit_pct = (current_price * (1 - FEE_RATE) - entry_price * (1 + FEE_RATE)) / (entry_price * (1 + FEE_RATE)) * 100
                peak_profit = (peak * (1 - FEE_RATE) - entry_price * (1 + FEE_RATE)) / (entry_price * (1 + FEE_RATE)) * 100
                held_days = _trading_days_held(entry_time)

                sell_reason = None
                if profit_pct >= config.STOCK_TAKE_PROFIT_PERCENT:
                    sell_reason = "익절"
                elif profit_pct <= -config.STOCK_MAX_LOSS_PERCENT:
                    sell_reason = "손절"
                elif (peak_profit >= config.STOCK_TRAILING_START_PCT
                      and current_price <= peak * (1 - config.STOCK_TRAILING_STOP_PCT / 100)):
                    sell_reason = "트레일링"
                elif held_days >= config.STOCK_MAX_HOLD_DAYS:
                    sell_reason = "time-stop"
                else:
                    # 매도 신호 체크
                    try:
                        daily = client.get_ohlcv(code, "day", 120)
                        daily = add_all_indicators(daily)
                        if not daily.empty and len(daily) >= 2:
                            ind = get_latest_indicators(daily, completed=True)
                            sc = score_signal(ind) * 2.5
                            _, action_cls = action_from_score(sc)
                            if action_cls == "sell-strong":
                                sell_reason = "매도신호"
                    except Exception:
                        logging.warning("주식 매도신호 체크 실패: %s", code)

                if sell_reason:
                    ret_pct = (current_price * (1 - FEE_RATE)) / (entry_price * (1 + FEE_RATE)) - 1
                    proceeds = quantity * current_price * (1 - FEE_RATE)
                    ts = datetime.now(_KST)

                    with _stock_lock:
                        if code in _stock_positions:
                            _stock_positions.pop(code)
                            _stock_trading_state["sim_krw"] += proceeds
                            _save_state()

                    record = {
                        "code": code, "name": name, "mode": "sim", "side": "sell",
                        "price": current_price, "quantity": quantity,
                        "ret_pct": round(ret_pct * 100, 4), "reason": sell_reason,
                        "ts": ts.isoformat(),
                    }
                    _log_trade(record)

                    sign = "+" if ret_pct >= 0 else ""
                    emoji = "📈" if ret_pct >= 0 else "📉"
                    notifier.send(
                        f"<b>🔴 주식 매도</b> · {html.escape(name)}({html.escape(code)}) <i>(시뮬)</i>\n"
                        f"{emoji} 수익률: <b>{sign}{ret_pct*100:.2f}%</b>\n"
                        f"체결가: {current_price:,.0f}원  수량: {quantity}주\n"
                        f"사유: {html.escape(sell_reason)}"
                    )
                    logging.info("주식 매도: %s %s %.2f%%", code, sell_reason, ret_pct * 100)

            # ── 진입 스캔 ──
            now = datetime.now(_KST)
            buy_cutoff = now.hour * 60 + now.minute > close_h * 60 + close_m

            with _stock_lock:
                cur_positions = len(_stock_positions)
                sim_krw = _stock_trading_state["sim_krw"]
                held_codes = set(_stock_positions.keys())

            if not buy_cutoff and cur_positions < config.STOCK_MAX_POSITIONS:
                universe = get_universe()
                for code, name in universe:
                    with _stock_lock:
                        if not _stock_trading_state["enabled"]:
                            break
                        cur_positions = len(_stock_positions)
                        sim_krw = _stock_trading_state["sim_krw"]
                        held_codes = set(_stock_positions.keys())

                    if cur_positions >= config.STOCK_MAX_POSITIONS:
                        break
                    if code in held_codes:
                        continue

                    try:
                        daily = client.get_ohlcv(code, "day", 120)
                        daily = add_all_indicators(daily)
                        if daily.empty or len(daily) < 2:
                            continue
                        ind = get_latest_indicators(daily, completed=True)
                    except Exception:
                        logging.warning("주식 데이터 수집 실패: %s", code)
                        continue

                    latest_close = ind.get("close", 0)
                    if latest_close <= 0 or latest_close > config.STOCK_MAX_PRICE:
                        continue

                    sc = score_signal(ind) * 2.5
                    if sc < config.STOCK_BUY_SCORE_THRESHOLD:
                        continue

                    try:
                        current_price = client.get_current_price(code)
                    except Exception:
                        logging.warning("주식 현재가 조회 실패(진입): %s", code)
                        continue

                    if not current_price:
                        continue

                    with _stock_lock:
                        cur_positions = len(_stock_positions)
                        sim_krw = _stock_trading_state["sim_krw"]
                        if cur_positions >= config.STOCK_MAX_POSITIONS or code in _stock_positions:
                            continue
                        empty_slots = config.STOCK_MAX_POSITIONS - cur_positions
                        slot_budget = sim_krw / empty_slots

                    quantity = math.floor(slot_budget / current_price)
                    if quantity <= 0:
                        continue
                    cost = quantity * current_price * (1 + FEE_RATE)
                    if cost > sim_krw:
                        continue

                    ts = datetime.now(_KST)
                    with _stock_lock:
                        if not _stock_trading_state["enabled"]:
                            break
                        if len(_stock_positions) >= config.STOCK_MAX_POSITIONS or code in _stock_positions:
                            continue
                        if cost > _stock_trading_state["sim_krw"]:
                            continue
                        _stock_trading_state["sim_krw"] -= cost
                        _stock_positions[code] = {
                            "name": name,
                            "entry_price": current_price,
                            "quantity": quantity,
                            "entry_time": ts,
                            "peak": current_price,
                        }
                        _save_state()

                    record = {
                        "code": code, "name": name, "mode": "sim", "side": "buy",
                        "price": current_price, "quantity": quantity,
                        "ret_pct": None, "reason": f"score={sc:.1f}",
                        "ts": ts.isoformat(),
                    }
                    _log_trade(record)
                    notifier.send(
                        f"<b>🟢 주식 매수</b> · {html.escape(name)}({html.escape(code)}) <i>(시뮬)</i>\n"
                        f"체결가: {current_price:,.0f}원  수량: {quantity}주\n"
                        f"금액: {cost:,.0f}원  점수: {sc:.1f}"
                    )
                    logging.info("주식 매수: %s %s score=%.1f", code, name, sc)

        except Exception:
            logging.exception("주식 워커 오류")

        time.sleep(60)
