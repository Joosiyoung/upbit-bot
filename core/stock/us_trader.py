# -*- coding: utf-8 -*-
import html
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from core import config
from core import notifier
from core import sheets_client as _sheets_mod

_KST = config.KST

US_STATE_FILE     = os.path.join("data", "us_stock_state.json")
US_TRADE_LOG_FILE = os.path.join("data", "us_stock_trade_history.jsonl")


def _us_session_key(dt: datetime) -> object:
    """KST 05:00 기준 미국 거래 세션 식별자 (자정 초기화 버그 방지)."""
    return (dt - timedelta(hours=5)).date()


_us_running = False
_us_positions: dict[str, dict] = {}   # {symbol: {excd, name, entry_price, qty, entry_time, peak_price, slots, entry_exchange_rate}}
_us_sim_krw  = 0.0                    # KRW 기준 잔고
_us_lock     = threading.Lock()
_us_sold_today: set[str] = set()

_logger = logging.getLogger(__name__)


def is_us_market_hours() -> bool:
    now = datetime.now(_KST)
    if now.weekday() >= 5:   # 토/일 (KST 기준)
        return False
    h, m = now.hour, now.minute
    # KST 22:30 ~ 익일 05:00 (서머타임 미반영 — 단순화)
    if h == 22 and m >= 30:
        return True
    if h == 23 or 0 <= h < 5:
        return True
    return False


def _log_us_trade(record: dict):
    os.makedirs(os.path.dirname(US_TRADE_LOG_FILE), exist_ok=True)
    with open(US_TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if record.get("side") in ("buy", "sell"):
        try:
            sc = _sheets_mod.get_client()
            if sc is not None:
                ts_val = record.get("ts", "")
                date_str = ts_val[:10] if ts_val else ""
                time_str = ts_val[11:19] if len(ts_val) >= 19 else ""
                row = [
                    date_str,
                    time_str,
                    record.get("side", ""),
                    record.get("symbol", ""),
                    record.get("name", ""),
                    record.get("excd", ""),
                    record.get("price", ""),
                    record.get("qty", ""),
                    record.get("amount_usd", ""),
                    record.get("exchange_rate", ""),
                    record.get("amount_krw", ""),
                    record.get("ret_pct", ""),
                    record.get("ret_pct_krw", ""),
                    record.get("threshold", config.US_BUY_SCORE_THRESHOLD),
                    record.get("tp_pct", config.US_TAKE_PROFIT_PERCENT),
                    record.get("sl_pct", config.US_MAX_LOSS_PERCENT),
                    record.get("trailing_start_pct", config.US_TRAILING_START_PCT),
                    record.get("trailing_stop_pct", config.US_TRAILING_STOP_PCT),
                    record.get("max_hold_days", config.US_MAX_HOLD_DAYS),
                ]
                sc.append("주식", row)
        except Exception as e:
            _logger.warning("Sheets 미국주식 전송 실패: %s", e)


def _save_us_state():
    os.makedirs(os.path.dirname(US_STATE_FILE), exist_ok=True)
    state_copy = {
        "running": _us_running,
        "sim_krw": _us_sim_krw,
    }
    positions_copy = {}
    for sym, pos in _us_positions.items():
        p = dict(pos)
        if isinstance(p.get("entry_time"), datetime):
            p["entry_time"] = p["entry_time"].isoformat()
        positions_copy[sym] = p
    with open(US_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"state": state_copy, "positions": positions_copy}, f, ensure_ascii=False)


def _load_us_state():
    global _us_positions, _us_sim_krw, _us_running
    if not os.path.exists(US_STATE_FILE):
        return
    try:
        with open(US_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        state = data.get("state", {})
        raw_pos = data.get("positions", {})
        for sym, p in raw_pos.items():
            raw_et = p.get("entry_time")
            if raw_et:
                try:
                    dt = datetime.fromisoformat(raw_et)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_KST)
                    p["entry_time"] = dt
                except (ValueError, TypeError):
                    p["entry_time"] = datetime.now(_KST)
            else:
                p["entry_time"] = datetime.now(_KST)
        with _us_lock:
            # 구버전(sim_usd) 호환: sim_krw 키 없으면 기본값 사용
            if "sim_krw" in state:
                _us_sim_krw = float(state["sim_krw"])
            else:
                _us_sim_krw = config.US_SIM_BUDGET
            _us_positions = raw_pos
            _us_running = state.get("running", False)
    except Exception:
        _logger.exception("미국주식 상태 파일 로드 오류")


def _us_worker():
    from core.stock.kis_client import KisClient
    from core.stock.us_universe import get_us_universe
    from core.indicators import add_all_indicators, get_latest_indicators
    from core.analysis import score_signal

    global _us_running, _us_sold_today, _us_sim_krw

    client = KisClient()
    last_session_key = _us_session_key(datetime.now(_KST))

    while _us_running:
        try:
            now = datetime.now(_KST)

            # 미국장 마감(KST 05:00) 경과 시 당일 매도 이력 초기화
            if _us_session_key(now) != last_session_key:
                with _us_lock:
                    _us_sold_today.clear()
                last_session_key = _us_session_key(now)

            if not is_us_market_hours():
                time.sleep(60)
                continue

            # ── 청산 체크 ──
            with _us_lock:
                symbols_to_check = list(_us_positions.keys())

            for symbol in symbols_to_check:
                with _us_lock:
                    if not _us_running:
                        break
                    pos = _us_positions.get(symbol)
                    if pos is None:
                        continue

                current_price = client.get_us_current_price(symbol, pos["excd"])
                if not current_price:
                    _logger.warning("미국주식 현재가 조회 실패: %s", symbol)
                    continue

                with _us_lock:
                    pos = _us_positions.get(symbol)
                    if pos is None:
                        continue
                    entry_price = pos["entry_price"]
                    qty         = pos["qty"]
                    peak        = pos.get("peak_price", current_price)
                    entry_time  = pos["entry_time"]
                    name        = pos.get("name", symbol)
                    excd        = pos["excd"]
                    peak = max(peak, current_price)
                    _us_positions[symbol]["peak_price"] = peak
                    _us_positions[symbol]["last_price"] = current_price  # status 조회용 캐시

                profit_pct = (current_price * (1 - config.US_FEE_RATE) - entry_price * (1 + config.US_FEE_RATE)) / (entry_price * (1 + config.US_FEE_RATE)) * 100
                peak_profit = (peak * (1 - config.US_FEE_RATE) - entry_price * (1 + config.US_FEE_RATE)) / (entry_price * (1 + config.US_FEE_RATE)) * 100
                held_days = (now - entry_time).days

                sell_reason = None
                if profit_pct >= config.US_TAKE_PROFIT_PERCENT:
                    sell_reason = "익절"
                elif profit_pct <= -config.US_MAX_LOSS_PERCENT:
                    sell_reason = "손절"
                elif (peak_profit >= config.US_TRAILING_START_PCT
                      and current_price <= peak * (1 - config.US_TRAILING_STOP_PCT / 100)):
                    sell_reason = "트레일링"
                elif held_days >= config.US_MAX_HOLD_DAYS:
                    sell_reason = "time-stop"

                if sell_reason:
                    exit_exchange_rate = client.get_usd_krw_rate()
                    ret_pct = (current_price * (1 - config.US_FEE_RATE)) / (entry_price * (1 + config.US_FEE_RATE)) - 1
                    proceeds_usd = qty * current_price * (1 - config.US_FEE_RATE)
                    proceeds_krw = proceeds_usd * exit_exchange_rate
                    cost_krw = qty * entry_price * (1 + config.US_FEE_RATE) * pos.get("entry_exchange_rate", exit_exchange_rate)
                    ret_pct_krw = proceeds_krw / cost_krw - 1 if cost_krw else ret_pct
                    ts = datetime.now(_KST)

                    if not config.US_IS_LIVE:
                        with _us_lock:
                            if symbol in _us_positions:
                                _us_positions.pop(symbol)
                                _us_sim_krw += proceeds_krw
                                _us_sold_today.add(symbol)
                                _save_us_state()
                    else:
                        ok = client.place_us_order_fractional(symbol, excd, "sell", proceeds_usd, current_price)
                        if ok:
                            with _us_lock:
                                if symbol in _us_positions:
                                    _us_positions.pop(symbol)
                                    _us_sold_today.add(symbol)
                                    _save_us_state()

                    sign = "+" if ret_pct >= 0 else ""
                    emoji = "📈" if ret_pct >= 0 else "📉"
                    krw_sign = "+" if ret_pct_krw >= 0 else ""
                    krw_emoji = "📈" if ret_pct_krw >= 0 else "📉"
                    record = {
                        "symbol": symbol, "name": name, "excd": excd,
                        "mode": "sim" if not config.US_IS_LIVE else "live",
                        "side": "sell",
                        "price": current_price, "qty": qty,
                        "amount_usd": round(proceeds_usd, 4),
                        "exchange_rate": round(exit_exchange_rate, 2),
                        "amount_krw": round(proceeds_krw, 0),
                        "ret_pct": round(ret_pct * 100, 4),
                        "ret_pct_krw": round(ret_pct_krw * 100, 4), "reason": sell_reason,
                        "ts": ts.isoformat(),
                    }
                    _log_us_trade(record)

                    with _us_lock:
                        bal_krw = _us_sim_krw
                    notifier.send_stock(
                        f"<b>🔴 미국주식 매도</b> · {html.escape(name)}({html.escape(symbol)}) <i>({'시뮬' if not config.US_IS_LIVE else '실거래'})</i>\n"
                        f"{krw_emoji} 수익률: <b>{krw_sign}{ret_pct_krw*100:.2f}%</b> (KRW) / {sign}{ret_pct*100:.2f}% (USD)\n"
                        f"매수가: ${entry_price:.4f} → 매도가: ${current_price:.4f}\n"
                        f"수익: ₩{proceeds_krw - pos.get('entry_exchange_rate', exit_exchange_rate) * entry_price * qty * (1 + config.US_FEE_RATE):,.0f} | 환율: {exit_exchange_rate:,.1f}원\n"
                        f"잔고: ₩{bal_krw:,.0f} | 사유: {html.escape(sell_reason)}"
                    )
                    _logger.info("미국주식 매도: %s %s %.2f%%", symbol, sell_reason, ret_pct * 100)

            # ── 진입 스캔 ──
            now = datetime.now(_KST)
            with _us_lock:
                cur_slots = sum(p.get("slots", 1) for p in _us_positions.values())
                held_symbols = set(_us_positions.keys())

            if cur_slots >= config.US_MAX_POSITIONS:
                time.sleep(60)
                continue

            universe = get_us_universe(client)
            for symbol, name, excd in universe:
                with _us_lock:
                    if not _us_running:
                        break
                    cur_slots = sum(p.get("slots", 1) for p in _us_positions.values())
                    held_symbols = set(_us_positions.keys())

                if cur_slots >= config.US_MAX_POSITIONS:
                    break
                if symbol in held_symbols:
                    continue
                if symbol in _us_sold_today:
                    continue

                df_day = client.get_us_ohlcv(symbol, excd, 120)
                if df_day.empty or len(df_day) < 2:
                    continue

                try:
                    df_day = add_all_indicators(df_day)
                except Exception:
                    _logger.warning("미국주식 지표 계산 실패: %s", symbol)
                    continue

                ind = get_latest_indicators(df_day, completed=True)
                # 일봉만 있으므로 score_signal에 일봉 지표만 사용
                sc = score_signal(ind)

                if sc < config.US_BUY_SCORE_THRESHOLD:
                    continue

                current_price = client.get_us_current_price(symbol, excd)
                if not current_price:
                    continue

                prev_close = ind.get("close", 0)
                if prev_close > 0:
                    change_pct = (current_price / prev_close - 1) * 100
                    if not (config.US_ENTRY_CHANGE_MIN <= change_pct <= config.US_ENTRY_CHANGE_MAX):
                        _logger.debug("미국주식 등락률 필터: %s %.1f%%", symbol, change_pct)
                        continue

                exchange_rate = client.get_usd_krw_rate()

                with _us_lock:
                    cur_slots = sum(p.get("slots", 1) for p in _us_positions.values())
                    sim_krw   = _us_sim_krw
                    if cur_slots >= config.US_MAX_POSITIONS or symbol in _us_positions:
                        continue
                    empty_slots   = config.US_MAX_POSITIONS - cur_slots
                    per_slot_krw  = sim_krw / empty_slots
                    per_slot_usd  = per_slot_krw / exchange_rate

                if per_slot_usd <= 0 or sim_krw <= 0:
                    continue

                qty = round(per_slot_usd / current_price, 4)
                if qty <= 0:
                    continue
                cost_usd = qty * current_price * (1 + config.US_FEE_RATE)
                cost_krw = cost_usd * exchange_rate

                ts = datetime.now(_KST)
                if not config.US_IS_LIVE:
                    with _us_lock:
                        if not _us_running:
                            break
                        cur_slots2 = sum(p.get("slots", 1) for p in _us_positions.values())
                        if cur_slots2 >= config.US_MAX_POSITIONS or symbol in _us_positions:
                            continue
                        if _us_sim_krw < cost_krw:
                            continue
                        _us_sim_krw -= cost_krw
                        _us_positions[symbol] = {
                            "excd":                excd,
                            "name":                name,
                            "entry_price":         current_price,
                            "qty":                 qty,
                            "entry_time":          ts,
                            "peak_price":          current_price,
                            "slots":               1,
                            "entry_exchange_rate": exchange_rate,
                        }
                        _save_us_state()
                else:
                    ok = client.place_us_order_fractional(symbol, excd, "buy", per_slot_usd, current_price)
                    if not ok:
                        continue
                    with _us_lock:
                        _us_positions[symbol] = {
                            "excd":                excd,
                            "name":                name,
                            "entry_price":         current_price,
                            "qty":                 qty,
                            "entry_time":          ts,
                            "peak_price":          current_price,
                            "slots":               1,
                            "entry_exchange_rate": exchange_rate,
                        }
                        _save_us_state()

                record = {
                    "symbol": symbol, "name": name, "excd": excd,
                    "mode": "sim" if not config.US_IS_LIVE else "live",
                    "side": "buy",
                    "price": current_price, "qty": qty,
                    "amount_usd": round(cost_usd, 4),
                    "exchange_rate": round(exchange_rate, 2),
                    "amount_krw": round(cost_krw, 0),
                    "ret_pct": None, "reason": f"score={sc:.1f}",
                    "ts": ts.isoformat(),
                }
                _log_us_trade(record)
                with _us_lock:
                    bal_krw = _us_sim_krw
                notifier.send_stock(
                    f"<b>🟢 미국주식 매수</b> · {html.escape(name)}({html.escape(symbol)}) <i>({'시뮬' if not config.US_IS_LIVE else '실거래'})</i>\n"
                    f"진입가: ${current_price:.4f} | 수량: {qty}주\n"
                    f"원화: ₩{cost_krw:,.0f} | 환율: {exchange_rate:,.1f}원\n"
                    f"잔고: ₩{bal_krw:,.0f} | 점수: {sc:.1f}"
                )
                _logger.info("미국주식 매수: %s %s score=%.1f", symbol, name, sc)

        except Exception:
            _logger.exception("미국주식 워커 오류")

        time.sleep(60)


def start_us_sim(budget_krw: float | None = None) -> dict:
    global _us_running, _us_sim_krw

    if not is_us_market_hours():
        return {
            "ok": False,
            "msg": "⚠️ 현재 미국장 외 시간입니다.\n개장 시각은 /us_market 으로 확인하세요.",
        }

    with _us_lock:
        if _us_running:
            return {"ok": False, "msg": "이미 미국주식 시뮬이 실행 중입니다."}

    _load_us_state()

    with _us_lock:
        if _us_running:
            return {"ok": False, "msg": "이미 미국주식 시뮬이 실행 중입니다."}
        has_positions = bool(_us_positions)
        if not has_positions:
            _us_sim_krw = budget_krw if (budget_krw and budget_krw > 0) else config.US_SIM_BUDGET
        _us_running = True
        _save_us_state()

    t = threading.Thread(target=_us_worker, daemon=True, name="us-stock-worker")
    t.start()

    budget_val = _us_sim_krw
    if has_positions:
        msg = f"미국주식 시뮬 재개 — 기존 포지션 {len(_us_positions)}종목, 잔고 ₩{budget_val:,.0f}"
    else:
        msg = f"미국주식 시뮬 시작 — 예산 ₩{budget_val:,.0f}"

    notifier.send_stock(f"<b>미국주식 시뮬 시작</b>\n{html.escape(msg)}")
    return {"ok": True, "msg": msg}


def stop_us() -> dict:
    global _us_running
    with _us_lock:
        _us_running = False
        _save_us_state()
    notifier.send_stock("<b>미국주식 시뮬 중지</b>")
    return {"ok": True, "msg": "미국주식 시뮬 중지됨"}


def get_us_status() -> dict:
    from core.stock.kis_client import KisClient
    with _us_lock:
        running   = _us_running
        sim_krw   = _us_sim_krw
        positions = dict(_us_positions)

    client = KisClient()
    exchange_rate = client.get_usd_krw_rate()
    positions_out = []
    total_value_krw = sim_krw

    for symbol, pos in positions.items():
        price = client.get_us_current_price(symbol, pos["excd"])
        price_fallback = not price
        if not price:
            price = pos["entry_price"]

        entry_price = pos["entry_price"]
        qty         = pos["qty"]
        profit_pct  = None
        if price and entry_price:
            profit_pct = round(
                (price * (1 - config.US_FEE_RATE) - entry_price * (1 + config.US_FEE_RATE))
                / (entry_price * (1 + config.US_FEE_RATE)) * 100, 2
            )

        value_krw = qty * price * exchange_rate
        total_value_krw += value_krw

        entry_time = pos.get("entry_time")
        positions_out.append({
            "symbol":               symbol,
            "name":                 pos.get("name", symbol),
            "excd":                 pos.get("excd", ""),
            "entry_price":          entry_price,
            "qty":                  qty,
            "current_price":        price if not price_fallback else None,
            "profit_pct":           profit_pct,
            "entry_time":           entry_time.isoformat() if hasattr(entry_time, "isoformat") else entry_time,
            "price_fallback":       price_fallback,
            "entry_exchange_rate":  pos.get("entry_exchange_rate"),
        })

    return {
        "running":           running,
        "sim_krw":           sim_krw,
        "exchange_rate":     exchange_rate,
        "positions":         positions_out,
        "total_value_krw":   round(total_value_krw, 0),
    }


def build_us_status_msg() -> str:
    with _us_lock:
        running   = _us_running
        sim_krw   = _us_sim_krw
        positions = {sym: dict(pos) for sym, pos in _us_positions.items()}

    status_str = "실행 중" if running else "중지"
    lines = [
        f"<b>미국주식 시뮬 — {html.escape(status_str)}</b>",
        f"시뮬 잔고: ₩{sim_krw:,.0f} / ₩{config.US_SIM_BUDGET:,.0f} 초기 (원화 기준)",
        f"슬롯: {len(positions)}/{config.US_MAX_POSITIONS}",
    ]

    if positions:
        lines.append("")
        for symbol, pos in positions.items():
            name        = html.escape(pos.get("name", symbol))
            entry_price = pos.get("entry_price", 0)

            price = pos.get("last_price")           # 워커 캐시 우선
            price_fallback = False
            if not price:
                from core.stock.kis_client import KisClient
                client = KisClient()
                price = client.get_us_current_price(symbol, pos.get("excd", "NAS"))
                if not price:
                    price = entry_price
                    price_fallback = True

            if price and entry_price:
                ret = (price * (1 - config.US_FEE_RATE) - entry_price * (1 + config.US_FEE_RATE)) / (entry_price * (1 + config.US_FEE_RATE)) * 100
                pct_str = f"{ret:+.2f}%" + ("(진입가)" if price_fallback else "")
            else:
                pct_str = "조회 불가"

            entry_time = pos.get("entry_time")
            held_days = (datetime.now(_KST) - entry_time).days if entry_time else 0

            lines.append(f"• {name}({html.escape(symbol)})  {pct_str}  진입 {held_days}일차")

    return "\n".join(lines)
