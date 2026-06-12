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
    MAX_POSITIONS,
    auto_trade_worker, get_risk_snapshot,
)
from core import trading_control
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
    force=True,   # 라이브러리가 먼저 로깅을 설정해도 우리 설정으로 덮어씀
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
    # Telegram 명령 봇 (미설정 시 내부에서 즉시 종료)
    from core.telegram_bot import start_telegram_bot
    start_telegram_bot()

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
    live_mode = request.json.get("live", False) if request.is_json else False
    result = trading_control.start_trading(live_mode)
    return jsonify(result), (200 if result.get("ok") else 409)


@app.route('/api/trading/stop', methods=['POST'])
def api_trading_stop():
    """자동 매매 중지
    mode=liquidate: 봇 매수 코인 시장가 청산 후 중지 (기본)
    mode=hold:      매도 없이 추적만 종료 후 중지
    """
    data   = request.get_json(silent=True) or {}
    mode   = data.get("mode", "liquidate")   # "liquidate" | "hold"
    result = trading_control.stop_trading(mode)
    return jsonify(result)


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
    try:
        # waitress: 24시간 운영용 프로덕션 WSGI 서버 (Flask 개발 서버 대체)
        from waitress import serve
        logging.info("대시보드 시작 (waitress): http://%s:%d",
                     config.DASHBOARD_HOST, config.DASHBOARD_PORT)
        serve(app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, threads=8)
    except ImportError:
        logging.warning("waitress 미설치 — Flask 개발 서버로 실행 (pip install waitress 권장)")
        app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT,
                debug=False, threaded=True)
