# -*- coding: utf-8 -*-
import html
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, date, timedelta

from core import config
from core import notifier
from core import sheets_client as _sheets_mod

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
_stock_sold_today: dict[str, "date"] = {}   # 당일 매도 종목 → 재진입 차단
_daily_signal_cache: dict[str, tuple] = {}   # code → (ind, score)
_daily_signal_cache_date: "date | None" = None
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
    if record.get("side") in ("buy", "sell"):
        try:
            sc = _sheets_mod.get_client()
            if sc is not None:
                ts_val = record.get("ts", "")
                date_str = ts_val[:10] if ts_val else ""
                amount_krw = record.get("price", 0) * record.get("quantity", 0)
                row = [
                    date_str,
                    ts_val,
                    record.get("side", ""),
                    record.get("code", ""),
                    record.get("name", ""),
                    record.get("reason", ""),
                    record.get("price", ""),
                    record.get("quantity", ""),
                    round(amount_krw),
                    record.get("ret_pct", ""),
                    record.get("threshold", config.STOCK_BUY_SCORE_THRESHOLD),
                    record.get("tp_pct", config.STOCK_TAKE_PROFIT_PERCENT),
                    record.get("sl_pct", config.STOCK_MAX_LOSS_PERCENT),
                    record.get("trailing_start_pct", config.STOCK_TRAILING_START_PCT),
                    record.get("trailing_stop_pct", config.STOCK_TRAILING_STOP_PCT),
                    record.get("max_hold_days", config.STOCK_MAX_HOLD_DAYS),
                ]
                sc.append("주식", row)
        except Exception as e:
            logging.warning("Sheets 주식 전송 실패: %s", e)


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


def _stock_market_notifier():
    """장 시작(09:00) 알림 전용 데몬. 시뮬 상태와 무관하게 항상 동작."""
    market_was_open = False
    while True:
        try:
            market_now = is_market_hours()
            if market_now and not market_was_open:
                market_was_open = True
                notifier.send_stock(
                    "<b>🔔 장 시작</b> — 평일 09:00 KST\n"
                    "/start_sim 으로 주식 시뮬을 시작하세요."
                )
            elif not market_now and market_was_open:
                market_was_open = False
        except Exception:
            logging.exception("장 시작 알림 워커 오류")
        time.sleep(30)


def start_stock_trading(budget_krw: float = 0.0) -> str:
    if not is_market_hours():
        return (
            "⚠️ 현재 장 시간이 아닙니다 (평일 09:00~15:30).\n"
            "장 중에 /start_sim 을 다시 입력해주세요."
        )

    with _stock_lock:
        if _stock_trading_state["enabled"]:
            return "이미 주식 시뮬 매매가 실행 중입니다."
        has_positions = bool(_stock_positions)
        saved_krw = _stock_trading_state["sim_krw"]

    if has_positions:
        effective_budget = saved_krw
    else:
        if budget_krw <= 0:
            budget_krw = config.STOCK_SIM_BUDGET
        effective_budget = budget_krw

    with _stock_lock:
        if _stock_trading_state["enabled"]:
            return "이미 주식 시뮬 매매가 실행 중입니다."
        _stock_trading_state["enabled"] = True
        _stock_trading_state["mode"] = "sim"
        if not has_positions:
            _stock_trading_state["sim_krw"] = effective_budget
            _stock_trading_state["started_at"] = datetime.now(_KST)
        _save_state()

    t = threading.Thread(target=_stock_worker, daemon=True, name="stock-worker")
    t.start()

    pos_count = len(_stock_positions)
    if pos_count > 0:
        msg = f"주식 시뮬 재개 — 기존 포지션 {pos_count}종목, 잔고 {effective_budget:,.0f}원"
    else:
        msg = f"주식 시뮬 시작 — 예산 {effective_budget:,.0f}원"
    notifier.send_stock(f"<b>주식 시뮬 매매 시작</b>\n{html.escape(msg)}")
    return msg


def stop_stock_trading() -> str:
    with _stock_lock:
        _stock_trading_state["enabled"] = False
        _save_state()
    notifier.send_stock("<b>주식 매매 중지</b>")
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
        client = KisClient()
        now_dt = datetime.now(_KST)
        lines.append("")
        for code, pos in positions.items():
            name = html.escape(pos.get("name", code))
            entry_price = pos.get("entry_price", 0)
            entry_time = pos.get("entry_time")

            current_price = client.get_current_price(code)
            price_fallback = not current_price
            if not current_price:
                current_price = entry_price

            if current_price and entry_price:
                ret = (current_price * (1 - FEE_RATE) - entry_price * (1 + FEE_RATE)) / (entry_price * (1 + FEE_RATE)) * 100
                pct_str = f"{ret:+.2f}%" + ("(진입가)" if price_fallback else "")
            else:
                pct_str = "조회 불가"

            held_days = 0
            if entry_time:
                held_days = _trading_days_held(entry_time)

            lines.append(f"• {name}({html.escape(code)})  {pct_str}  진입 {held_days}일차")

    return "\n".join(lines)


def _build_stock_daily_report() -> str:
    today = datetime.now(_KST).date()
    buys, sells, wins, rets = 0, 0, 0, []

    if os.path.exists(STOCK_TRADE_LOG_FILE):
        with open(STOCK_TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_KST)
                    if ts.date() != today:
                        continue
                    if rec.get("side") == "buy":
                        buys += 1
                    elif rec.get("side") == "sell":
                        sells += 1
                        r = rec.get("ret_pct")
                        if r is not None:
                            rets.append(r)
                            if r > 0:
                                wins += 1
                except Exception:
                    continue

    win_rate = wins / len(rets) * 100 if rets else 0
    avg_ret = sum(rets) / len(rets) if rets else 0

    now = datetime.now(_KST)
    weekdays_ko = ["월", "화", "수", "목", "금", "토", "일"]
    dow = weekdays_ko[now.weekday()]

    lines = [
        f"<b>📊 주식 시뮬 일일 보고서</b>",
        f"{now.strftime('%Y-%m-%d')} ({dow}) · 장 마감",
        "",
        f"<b>당일 매매</b>",
        f"• 매수: {buys}건  매도: {sells}건",
    ]
    if rets:
        total_pnl = sum(rets)
        lines.append(f"• 승률: {win_rate:.0f}%  평균 수익률: {avg_ret:+.2f}%")
        lines.append(f"• 실현 손익 합계: {total_pnl:+.2f}%")
    else:
        lines.append("• 실현 거래 없음")

    with _stock_lock:
        positions = dict(_stock_positions)
        sim_krw = _stock_trading_state["sim_krw"]

    if positions:
        lines.append("")
        lines.append(f"<b>보유 포지션 ({len(positions)}종목)</b>")
        for code, pos in positions.items():
            name = html.escape(pos.get("name", code))
            held = _trading_days_held(pos.get("entry_time", datetime.now(_KST)))
            lines.append(f"• {name}({html.escape(code)})  진입 {held}일차")
    else:
        lines.append("")
        lines.append("보유 포지션 없음")

    lines.append("")
    lines.append(f"💵 시뮬 잔고: {sim_krw:,.0f}원")

    return "\n".join(lines)


def _get_daily_signal(client, code: str):
    """일봉 신호를 캐시에서 반환. 당일 캐시가 없으면 KIS 조회 후 저장."""
    from core.indicators import add_all_indicators, get_latest_indicators
    from core.analysis import score_signal
    global _daily_signal_cache, _daily_signal_cache_date
    today = datetime.now(_KST).date()
    if _daily_signal_cache_date != today:
        _daily_signal_cache.clear()
        _daily_signal_cache_date = today
    if code in _daily_signal_cache:
        return _daily_signal_cache[code]
    try:
        daily = client.get_ohlcv(code, "day", 120)
        daily = add_all_indicators(daily)
        if daily.empty or len(daily) < 2:
            return None
        ind = get_latest_indicators(daily, completed=True)
        sc = score_signal(ind) * 2.5
        _daily_signal_cache[code] = (ind, sc)
        return _daily_signal_cache[code]
    except Exception:
        logging.warning("일봉 신호 조회 실패: %s", code)
        return None


def _stock_worker():
    from core.stock.kis_client import KisClient
    from core.stock.universe import get_dynamic_universe
    from core.analysis import action_from_score

    close_h, close_m = (int(x) for x in config.STOCK_BUY_CLOSE_TIME.split(":"))
    market_was_open = is_market_hours()

    while _stock_trading_state["enabled"]:
        try:
            market_now = is_market_hours()

            if not market_now and market_was_open:
                market_was_open = False
                report = _build_stock_daily_report()
                notifier.send_stock(report)
                with _stock_lock:
                    _stock_trading_state["enabled"] = False
                    _save_state()
                logging.info("장 마감 — 주식 시뮬 자동 종료")
                break

            if not market_now:
                time.sleep(30)
                continue

            client = KisClient()
            now = datetime.now(_KST)

            # ── 청산 체크 ──
            with _stock_lock:
                codes_to_check = list(_stock_positions.keys())

            # 사이클 시작 시 보유 종목 현재가 일괄 조회 (중복 호출 방지)
            _price_cache: dict[str, float | None] = {}
            for _c in codes_to_check:
                _price_cache[_c] = client.get_current_price(_c)

            for code in codes_to_check:
                with _stock_lock:
                    if not _stock_trading_state["enabled"]:
                        break
                    pos = _stock_positions.get(code)
                    if pos is None:
                        continue

                current_price = _price_cache.get(code)
                if not current_price:
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
                    result = _get_daily_signal(client, code)
                    if result is not None:
                        ind_s, sc_s = result
                        _, action_cls = action_from_score(sc_s)
                        if action_cls == "sell-strong":
                            sell_reason = "매도신호"

                if sell_reason:
                    ret_pct = (current_price * (1 - FEE_RATE)) / (entry_price * (1 + FEE_RATE)) - 1
                    proceeds = quantity * current_price * (1 - FEE_RATE)
                    ts = datetime.now(_KST)

                    with _stock_lock:
                        if code in _stock_positions:
                            _stock_positions.pop(code)
                            _stock_trading_state["sim_krw"] += proceeds
                            _stock_sold_today[code] = now.date()
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
                    notifier.send_stock(
                        f"<b>🔴 주식 매도</b> · {html.escape(name)}({html.escape(code)}) <i>(시뮬)</i>\n"
                        f"{emoji} 수익률: <b>{sign}{ret_pct*100:.2f}%</b>\n"
                        f"체결가: {current_price:,.0f}원  수량: {quantity}주\n"
                        f"사유: {html.escape(sell_reason)}"
                    )
                    logging.info("주식 매도: %s %s %.2f%%", code, sell_reason, ret_pct * 100)

            # ── 진입 스캔 ──
            now = datetime.now(_KST)
            is_sim = _stock_trading_state.get("mode", "sim") == "sim"
            buy_cutoff = (not is_sim) and (now.hour * 60 + now.minute > close_h * 60 + close_m)

            with _stock_lock:
                cur_positions = len(_stock_positions)
                sim_krw = _stock_trading_state["sim_krw"]
                held_codes = set(_stock_positions.keys())

            if not buy_cutoff and cur_positions < config.STOCK_MAX_POSITIONS:
                _empty = max(1, config.STOCK_MAX_POSITIONS - cur_positions)
                _slot_budget = sim_krw / _empty
                universe = get_dynamic_universe(client, _slot_budget, limit=30)
                for code, name in universe:
                    with _stock_lock:
                        if not _stock_trading_state["enabled"]:
                            break
                        cur_positions = len(_stock_positions)
                        sim_krw = _stock_trading_state["sim_krw"]
                        held_codes = set(_stock_positions.keys())
                        sold_today_code = _stock_sold_today.get(code)

                    if cur_positions >= config.STOCK_MAX_POSITIONS:
                        break
                    if code in held_codes:
                        continue
                    if sold_today_code == now.date():
                        continue  # 당일 매도 종목 재진입 차단

                    result = _get_daily_signal(client, code)
                    if result is None:
                        continue
                    ind, sc = result

                    latest_close = ind.get("close", 0)
                    empty_slots = max(1, config.STOCK_MAX_POSITIONS - cur_positions)
                    price_cap = sim_krw / empty_slots
                    if latest_close <= 0 or latest_close > price_cap:
                        continue

                    if sc < config.STOCK_BUY_SCORE_THRESHOLD:
                        continue

                    try:
                        current_price = client.get_current_price(code)
                    except Exception:
                        logging.warning("주식 현재가 조회 실패(진입): %s", code)
                        continue

                    if not current_price:
                        continue

                    prev_close = ind.get("close", 0)
                    if prev_close > 0:
                        daily_change_pct = (current_price / prev_close - 1) * 100
                        if not (config.STOCK_ENTRY_CHANGE_MIN <= daily_change_pct <= config.STOCK_ENTRY_CHANGE_MAX):
                            logging.debug("등락률 필터: %s %.1f%%", code, daily_change_pct)
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
                    notifier.send_stock(
                        f"<b>🟢 주식 매수</b> · {html.escape(name)}({html.escape(code)}) <i>(시뮬)</i>\n"
                        f"체결가: {current_price:,.0f}원  수량: {quantity}주\n"
                        f"금액: {cost:,.0f}원  점수: {sc:.1f}"
                    )
                    logging.info("주식 매수: %s %s score=%.1f", code, name, sc)

            # ─── 추가매수 Phase ───
            if config.STOCK_ADD_BUY_ENABLED and not buy_cutoff:
                with _stock_lock:
                    cur_positions = len(_stock_positions)
                    sim_krw = _stock_trading_state["sim_krw"]
                    positions_snapshot = dict(_stock_positions)

                empty_slots = config.STOCK_MAX_POSITIONS - cur_positions
                if empty_slots > 0 and sim_krw > 0:
                    slot_budget = sim_krw / empty_slots
                    for code, pos in list(positions_snapshot.items()):
                        if empty_slots <= 0:
                            break
                        # 종목당 슬롯 상한 확인
                        if pos.get("slot_count", 1) >= config.STOCK_MAX_SLOTS_PER_TICKER:
                            continue
                        # 마지막 매수 이후 1 영업일(1일) 이상 경과 확인
                        last_ts = pos.get("last_add_ts") or pos.get("entry_ts")
                        if not last_ts:
                            entry_time_val = pos.get("entry_time")
                            if entry_time_val is None:
                                continue
                            last_ts = entry_time_val.isoformat() if hasattr(entry_time_val, "isoformat") else str(entry_time_val)
                        try:
                            last_dt = datetime.fromisoformat(last_ts)
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=_KST)
                            else:
                                last_dt = last_dt.astimezone(_KST)
                        except Exception:
                            continue
                        if (now - last_dt) < timedelta(days=1):
                            continue
                        # 현재가 조회 (사이클 캐시 우선, 없으면 신규 조회)
                        current_price = _price_cache.get(code) or client.get_current_price(code)
                        if not current_price:
                            continue
                        # 수익률 계산
                        entry_price = pos["entry_price"]
                        if entry_price <= 0:
                            continue
                        profit_pct = (current_price * (1 - FEE_RATE) - entry_price * (1 + FEE_RATE)) / (entry_price * (1 + FEE_RATE)) * 100
                        # 수익 구간 확인 (손실 중 물타기 방지)
                        if profit_pct < config.STOCK_ADD_BUY_MIN_PROFIT or profit_pct > config.STOCK_ADD_BUY_MAX_PROFIT:
                            continue
                        # 진입 점수 확인
                        result_add = _get_daily_signal(client, code)
                        if result_add is None:
                            continue
                        ind_add, sc_add = result_add
                        if sc_add < config.STOCK_BUY_SCORE_THRESHOLD:
                            continue
                        # 등락률 필터
                        prev_close_add = ind_add.get("close", 0)
                        if prev_close_add > 0:
                            daily_change_pct_add = (current_price / prev_close_add - 1) * 100
                            if not (config.STOCK_ENTRY_CHANGE_MIN <= daily_change_pct_add <= config.STOCK_ENTRY_CHANGE_MAX):
                                continue
                        # 매수 실행
                        quantity = math.floor(slot_budget / current_price)
                        if quantity <= 0:
                            continue
                        cost = quantity * current_price * (1 + FEE_RATE)
                        with _stock_lock:
                            if _stock_trading_state["sim_krw"] < cost:
                                continue
                            if code not in _stock_positions:
                                continue
                            p = _stock_positions[code]
                            name_add = p.get("name", code)
                            old_qty = p["quantity"]
                            old_entry = p["entry_price"]
                            new_qty = old_qty + quantity
                            # 순수 가격 기준 가중평균 (수수료 미포함) — 청산 시 entry_price * (1+FEE_RATE) 에서 한 번만 반영
                            p["entry_price"] = (old_entry * old_qty + current_price * quantity) / new_qty
                            p["quantity"] = new_qty
                            p["slot_count"] = p.get("slot_count", 1) + 1
                            p["last_add_ts"] = now.isoformat()
                            p["peak"] = max(p.get("peak", 0), current_price)
                            _stock_trading_state["sim_krw"] = max(0.0, _stock_trading_state["sim_krw"] - cost)
                            empty_slots -= 1
                            _save_state()
                        add_reason = f"추가매수: 강한 매수 (점수 {sc_add:.1f}, 수익 {profit_pct:+.1f}%)"
                        _log_trade({
                            "code":     code,
                            "name":     name_add,
                            "mode":     "sim",
                            "side":     "buy",
                            "price":    current_price,
                            "quantity": quantity,
                            "ret_pct":  None,
                            "reason":   add_reason,
                            "ts":       now.isoformat(),
                        })
                        notifier.send_stock(
                            f"<b>🟢 주식 추가매수</b> · {html.escape(name_add)}({html.escape(code)}) <i>(시뮬)</i>\n"
                            f"추가 {quantity}주 · {current_price:,.0f}원\n"
                            f"사유: {html.escape(add_reason)}"
                        )
                        logging.info("주식 추가매수: %s %s %.2f%%", code, add_reason, profit_pct)

        except Exception:
            logging.exception("주식 워커 오류")

        time.sleep(60)
