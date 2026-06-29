import functools
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime
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
    auto_trade_worker, get_risk_snapshot, compute_performance,
)
from core import trading_control
from core.ai_analysis import ai_worker
from core import config

# ─────────────────────────────────────────────
# 로깅: 파일(일 단위 회전, logs/) + 콘솔
# ─────────────────────────────────────────────
# 자정마다 회전하고 최근 config.LOG_RETENTION_DAYS일치를 보관(기본 35일). 크기 기반과 달리
# 로그량과 무관하게 보존 기간이 일정 → log-analyzer를 주/월 단위로 돌려도 그 기간 로그가 남음.

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR  = os.path.join(_BASE_DIR, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True,   # 라이브러리가 먼저 로깅을 설정해도 우리 설정으로 덮어씀
    handlers=[
        logging.handlers.TimedRotatingFileHandler(
            os.path.join(_LOG_DIR, "bot.log"),
            when="midnight", interval=1,
            backupCount=config.LOG_RETENTION_DAYS, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
)


# ─────────────────────────────────────────────
# 인증 데코레이터 (상태 변경 엔드포인트 전용)
# ─────────────────────────────────────────────

def _require_auth(f):
    """X-Dashboard-Token 헤더 또는 ?token= 쿼리파라미터로 DASHBOARD_TOKEN 검증.
    토큰 미설정 시(빈 문자열) 경고만 출력하고 통과 — 로컬 개발 편의.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = config.DASHBOARD_TOKEN
        if not token:
            logging.warning("DASHBOARD_TOKEN 미설정 — 인증 없이 %s 허용 (로컬 개발 모드)", request.path)
        else:
            provided = (
                request.headers.get("X-Dashboard-Token")
                or request.args.get("token", "")
            )
            if provided != token:
                return jsonify({"ok": False, "error": "Unauthorized"}), 401
        # CSRF 완화: 커스텀 헤더 필요 엔드포인트이므로 Content-Type도 검증
        if not request.is_json:
            return jsonify({"ok": False, "error": "Content-Type must be application/json"}), 415
        return f(*args, **kwargs)
    return wrapper

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

    # 주식 상태 복원 (재시작 시 이전 포지션 유지)
    from core.stock.trader import _load_state as _load_stock_state, _stock_trading_state, _stock_positions
    _load_stock_state()
    if _stock_trading_state.get("enabled"):
        if _stock_positions:
            from core.stock.trader import _stock_worker
            t = threading.Thread(target=_stock_worker, daemon=True, name="stock-worker")
            t.start()
        else:
            _stock_trading_state["enabled"] = False
            from core.stock.trader import _save_state as _save_stock_state
            _save_stock_state()

    # 국내주식 장 시작(09:00) 알림 데몬 — 시뮬 상태와 무관하게 항상 동작
    from core.stock.trader import _stock_market_notifier
    threading.Thread(target=_stock_market_notifier, daemon=True, name="stock-market-notifier").start()

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
    return jsonify(data)

@app.route('/api/refresh', methods=['POST'])
@_require_auth
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
@_require_auth
def api_trading_start():
    data      = request.get_json(silent=True) or {}
    live_mode = data.get("live", False)

    # 시뮬레이션 가상 예산(선택). 비어 있으면 None → 업비트 잔고 사용.
    sim_budget = None
    raw = data.get("sim_budget")
    if raw is not None and str(raw).strip() != "":
        try:
            sim_budget = float(str(raw).replace(",", "").strip())
        except ValueError:
            return jsonify({"ok": False, "error": "가상 자산 금액이 올바르지 않습니다."}), 400
        if sim_budget <= 0:
            return jsonify({"ok": False, "error": "가상 자산은 0보다 커야 합니다."}), 400

    result = trading_control.start_trading(live_mode, sim_budget)
    return jsonify(result), (200 if result.get("ok") else 409)


@app.route('/api/trading/stop', methods=['POST'])
@_require_auth
def api_trading_stop():
    """자동 매매 중지
    mode=liquidate: 봇 매수 코인 시장가 청산 후 중지 (기본)
    mode=hold:      매도 없이 추적만 종료 후 중지
    """
    data   = request.get_json(silent=True) or {}
    mode   = data.get("mode", "liquidate")   # "liquidate" | "hold"
    result = trading_control.stop_trading(mode)
    return jsonify(result)


@app.route('/api/performance')
def api_performance():
    """trade_history.jsonl 누적 실현 성과 (시뮬/실거래 분리)"""
    return jsonify(compute_performance())


# ─────────────────────────────────────────────
# 주식 봇 라우트
# ─────────────────────────────────────────────

@app.route('/api/stock/status')
def api_stock_status():
    from core.stock.trader import _stock_trading_state, _stock_positions, _stock_lock
    with _stock_lock:
        enabled  = _stock_trading_state["enabled"]
        sim_krw  = _stock_trading_state["sim_krw"]
        positions_raw = dict(_stock_positions)

    from core.stock.kis_client import KisClient
    client = KisClient()

    positions_out = {}
    for code, pos in positions_raw.items():
        try:
            current_price = client.get_current_price(code)
        except Exception:
            current_price = pos.get("entry_price")

        entry_price = pos.get("entry_price", 0)
        profit_pct = None
        if current_price and entry_price > 0:
            net_sell = current_price * (1 - config.STOCK_FEE_RATE)
            cost     = entry_price  * (1 + config.STOCK_FEE_RATE)
            profit_pct = round((net_sell - cost) / cost * 100, 2)

        entry_time = pos.get("entry_time")
        positions_out[code] = {
            "name":          pos.get("name", code),
            "entry_price":   entry_price,
            "quantity":      pos.get("quantity", 0),
            "entry_time":    entry_time.isoformat() if hasattr(entry_time, "isoformat") else entry_time,
            "peak":          pos.get("peak"),
            "current_price": current_price,
            "profit_pct":    profit_pct,
        }

    return jsonify({
        "enabled":       enabled,
        "sim_krw":       sim_krw,
        "positions":     positions_out,
        "max_positions": config.STOCK_MAX_POSITIONS,
        "slot_used":     len(positions_out),
    })


@app.route('/api/stock/start', methods=['POST'])
@_require_auth
def api_stock_start():
    from core.stock.trader import start_stock_trading
    data   = request.get_json(silent=True) or {}
    budget = data.get("budget", 0.0)
    try:
        budget = float(budget) if budget else 0.0
    except (ValueError, TypeError):
        budget = 0.0
    result_msg = start_stock_trading(budget_krw=budget)
    return jsonify({"ok": True, "msg": result_msg})


@app.route('/api/stock/stop', methods=['POST'])
@_require_auth
def api_stock_stop():
    from core.stock.trader import stop_stock_trading
    result_msg = stop_stock_trading()
    return jsonify({"ok": True, "msg": result_msg})


@app.route('/api/stock/performance')
def api_stock_performance():
    import json as _json
    log_file = os.path.join("data", "stock_trade_history.jsonl")
    today = datetime.now(config.KST).date()

    total_trades  = 0
    wins          = 0
    rets          = []
    today_sells   = 0
    today_ret_sum = 0.0

    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                if rec.get("side") != "sell":
                    continue
                r = rec.get("ret_pct")
                if r is None:
                    continue
                total_trades += 1
                rets.append(r)
                if r > 0:
                    wins += 1
                try:
                    ts = datetime.fromisoformat(rec["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=config.KST)
                    if ts.date() == today:
                        today_sells   += 1
                        today_ret_sum += r
                except Exception:
                    pass

    return jsonify({
        "total_trades":  total_trades,
        "win_rate":      wins / total_trades * 100 if total_trades else 0,
        "avg_ret":       sum(rets) / len(rets) if rets else 0,
        "today_sells":   today_sells,
        "today_ret_sum": today_ret_sum,
    })


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

    # market_map에 없는 포지션 티커(시뮬 전용 보유 등)는 직접 현재가 조회
    missing = [t for t in positions_raw if t not in market_map]
    if missing:
        from core.upbit_client import UpbitClient
        _uc = UpbitClient()
        for t in missing:
            p = _uc.get_current_price(t, warn=False)
            if p:
                market_map[t] = p

    positions_out = {}
    for ticker, pos in positions_raw.items():
        current = market_map.get(ticker)
        profit_pct = None
        if current and pos["entry_price"] > 0:
            # 시뮬·실거래 동일 회계: 매도 수수료(0.05%) 차감 실수령 기준 수익률.
            # trader.py Phase 1 실현 수익률(net_sell_price 기준)과 표시 기준을 일치시킴.
            net_current = current * (1 - 0.0005)
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
    if config.DASHBOARD_HOST == "0.0.0.0":
        print("[ERROR] DASHBOARD_HOST=0.0.0.0 은 보안상 금지됩니다. "
              "Tailscale IP(100.x.x.x) 또는 127.0.0.1 로 설정하세요.", file=sys.stderr)
        sys.exit(1)
    start_workers()
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
