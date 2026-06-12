import logging
import logging.handlers
import os
import threading
from flask import Flask, render_template, jsonify, request

from core.data_builder import (
    _cache, _cache_lock,
    _market_cache, _market_lock,
    build_analysis_data, build_market_data,
    cache_worker,
)
from core.trader import (
    _trading_state, _trading_lock,
    TRADING_BLACKLIST, MAX_POSITIONS,
    auto_trade_worker, get_risk_snapshot, save_state,
)
from core.ai_analysis import (
    _ai_cache, _ai_lock,
    ai_worker,
)
from core import config

# ─────────────────────────────────────────────
# 로깅: 파일(회전, logs/) + 콘솔
# ─────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR  = os.path.join(_BASE_DIR, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(_LOG_DIR, "bot.log"),
            maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
)

# ─────────────────────────────────────────────
# 백그라운드 워커 시작
# ─────────────────────────────────────────────

_workers_started = False

def start_workers():
    """백그라운드 워커 기동 (중복 기동 방지)"""
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    threading.Thread(target=cache_worker,      daemon=True).start()
    threading.Thread(target=ai_worker,         daemon=True).start()
    threading.Thread(target=auto_trade_worker, daemon=True).start()

# ─────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analysis')
def api_analysis():
    with _cache_lock:
        data = dict(_cache)
    return jsonify(data)

@app.route('/api/market')
def api_market():
    with _market_lock:
        data = dict(_market_cache)
    with _ai_lock:
        ai_snap = dict(_ai_cache)

    ai_updated_at = ai_snap.pop("_updated_at", None)

    if ai_snap and data.get("coins"):
        merged = []
        for c in data["coins"]:
            ai = ai_snap.get(c["ticker"])
            if ai:
                c = {**c,
                     "action_class": ai["action_class"],
                     "action_text":  ai["action_text"],
                     "ai_reason":    ai["ai_reason"],
                     "ai_analyzed":  True}
            merged.append(c)
        data = {**data, "coins": merged, "ai_updated_at": ai_updated_at}

    return jsonify(data)

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """수동 새로고침: 보유 코인 + 대표 코인 동시 갱신"""
    def _run_holdings():
        try:
            data = build_analysis_data()
            with _cache_lock:
                _cache.update(data)
        except Exception as e:
            with _cache_lock:
                _cache["status"]    = "error"
                _cache["error_msg"] = str(e)

    def _run_market():
        try:
            data = build_market_data()
            with _market_lock:
                _market_cache.update(data)
        except Exception as e:
            with _market_lock:
                _market_cache["status"]    = "error"
                _market_cache["error_msg"] = str(e)

    threading.Thread(target=_run_holdings, daemon=True).start()
    threading.Thread(target=_run_market,   daemon=True).start()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
# 자동 매매 라우트
# ─────────────────────────────────────────────

@app.route('/api/trading/start', methods=['POST'])
def api_trading_start():
    from core.upbit_client import UpbitClient
    from datetime import datetime as _dt
    live_mode = request.json.get("live", False) if request.is_json else False
    now_str   = _dt.now().strftime("%H:%M:%S")

    client = UpbitClient()

    # 기존 보유 코인(BLACKLIST 제외)을 초기 포지션으로 가져오기
    initial_positions = {}
    try:
        if client.upbit:
            for b in client.upbit.get_balances():
                currency = b['currency']
                if currency == 'KRW':
                    continue
                ticker    = f"KRW-{currency}"
                if ticker in TRADING_BLACKLIST:
                    continue
                volume    = float(b['balance'])
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
                    "entry_ts":    _dt.now().isoformat(),
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
    # 시뮬레이션 모드이면 AI 캐시 초기화 (AI 배지 숨김)
    if not live_mode:
        with _ai_lock:
            _ai_cache.clear()
    from core.notifier import notify_event
    notify_event(
        "▶️ 자동 매매 시작" if live_mode else "▶️ 시뮬레이션 시작",
        f"모드: {'실거래' if live_mode else '시뮬레이션'}\n"
        f"초기 포지션: {imported}종목 (슬롯 {imported}/{MAX_POSITIONS})"
        + ("" if live_mode else f"\n가상잔고: {sim_krw:,.0f}원"))
    return jsonify({"ok": True, "enabled": True, "live": live_mode, "sim_krw": sim_krw, "imported": imported})


@app.route('/api/trading/stop', methods=['POST'])
def api_trading_stop():
    """자동 매매 중지
    mode=liquidate: 봇 매수 코인 시장가 청산 후 중지 (기본)
    mode=hold:      매도 없이 추적만 종료 후 중지
    """
    from core.upbit_client import UpbitClient
    from datetime import datetime as _dt
    from core.trader import _log_trade, _execute_live_sell, _sell_cooldown

    data      = request.get_json(silent=True) or {}
    mode      = data.get("mode", "liquidate")   # "liquidate" | "hold"
    client    = UpbitClient()
    now_str   = _dt.now().strftime("%H:%M:%S")

    # 먼저 매매를 비활성화하고 epoch을 올려 진행 중인 사이클의 추가 주문을 차단
    with _trading_lock:
        _trading_state["enabled"]  = False
        _trading_state["epoch"]   += 1
        positions_snap = dict(_trading_state["positions"])
        live_mode      = _trading_state["live"]

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
                        _sell_cooldown[ticker] = _dt.now()   # 청산 후 즉시 재매수 방지
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
    # 매매 중지 시 AI 캐시 초기화
    with _ai_lock:
        _ai_cache.clear()
    from core.notifier import notify_event
    notify_event("⏹️ 자동 매매 중지", stop_msg)
    return jsonify({"ok": True, "enabled": False, "sell_failed": sell_failed})


@app.route('/api/trading/status')
def api_trading_status():
    with _market_lock:
        market_map = {c["ticker"]: c["current"] for c in _market_cache.get("coins", [])}
    # 보유 코인은 market_cache에서 제외되므로 holdings 캐시에서 보완
    with _cache_lock:
        for h in _cache.get("holdings", []):
            if h["ticker"] not in market_map:
                market_map[h["ticker"]] = h.get("current_price")

    with _trading_lock:
        positions_raw = dict(_trading_state["positions"])
        state_snap = {
            "enabled":    _trading_state["enabled"],
            "live":       _trading_state["live"],
            "sim_krw":    _trading_state["sim_krw"],
            "sim_initial_total": _trading_state["sim_initial_total"],
            "last_check": _trading_state["last_check"],
            "status_msg": _trading_state["status_msg"],
            "log":        list(_trading_state["log"]),
        }

    live_mode = state_snap["live"]
    positions_out = {}
    for ticker, pos in positions_raw.items():
        current = market_map.get(ticker)
        profit_pct = None
        if current and pos["entry_price"] > 0:
            # 실거래: 매도 수수료(0.05%) 차감 후 실수령 기준 수익률
            # 시뮬: 매도 수수료는 sim_krw 차감으로 처리되므로 현재가 그대로 사용
            net_current = current * 0.9995 if live_mode else current
            profit_pct = round((net_current - pos["entry_price"]) / pos["entry_price"] * 100, 2)
        positions_out[ticker] = {**pos, "current_price": current, "profit_pct": profit_pct}

    used_budget = sum(p["amount_krw"] for p in positions_raw.values())

    return jsonify({
        **state_snap,
        "positions":     positions_out,
        "used_budget":   used_budget,
        "max_positions": MAX_POSITIONS,
        "trade_amount":  config.TRADE_AMOUNT_KRW,
        "max_loss":      config.MAX_LOSS_PERCENT,
        "take_profit":   config.TAKE_PROFIT_PERCENT,
        "risk":          get_risk_snapshot(),
    })


if __name__ == '__main__':
    start_workers()
    # 대시보드에 인증이 없으므로 공인 IP(0.0.0.0)에 직접 바인딩하지 말 것.
    # VPS에서는 DASHBOARD_HOST에 Tailscale IP(100.x.x.x)를 지정해 tailnet에서만 접속.
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT,
            debug=False, threaded=True)
