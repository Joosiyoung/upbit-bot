# ─────────────────────────────────────────────
# 자동 매매 — 상태, 로직, 워커
# ─────────────────────────────────────────────

import html
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from core.upbit_client import UpbitClient
from core import config
from core import notifier
from core import sheets_client as _sheets_mod

_KST = config.KST

def _as_kst(dt: datetime) -> datetime:
    """naive datetime을 KST(+09:00)로 간주. already-aware는 그대로."""
    return dt if dt.tzinfo else dt.replace(tzinfo=_KST)
from core.data_builder import (
    _cache, _cache_lock,
    _market_cache, _market_lock,
    build_market_data,
)

# 런타임 데이터는 프로젝트 루트의 data/ 폴더에 저장
_ROOT_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR      = os.path.join(_ROOT_DIR, "data")
os.makedirs(_DATA_DIR, exist_ok=True)
STATE_FILE     = os.path.join(_DATA_DIR, "bot_state.json")
TRADE_LOG_FILE = os.path.join(_DATA_DIR, "trade_history.jsonl")

FEE_RATE = 0.0005   # 업비트 수수료 0.05%

# ─────────────────────────────────────────────
# 자동 매매 상태 및 상수
# ─────────────────────────────────────────────

_trading_lock = threading.Lock()
_trading_state = {
    "enabled":    False,
    "live":       config.LIVE_TRADING,
    "sim_krw":    0.0,      # 시뮬레이션 가상 KRW 잔고
    "sim_initial_total": 0.0,  # 시뮬 시작 시점 총자산 (KRW + 초기 포지션 평가액) — 성과 기준점
    "log":        [],       # 최근 거래 이력 (최대 50건, 전체 이력은 trade_history.jsonl)
    "last_check": None,
    "status_msg": "대기 중",
    "positions":  {},       # ticker → {entry_price, amount_krw, quantity, entry_time, ...}
    "epoch":      0,        # start/stop 시 증가 — 진행 중 사이클의 낡은 쓰기 방지
}

# 전역 리스크 상태 (일일 손실 한도, 연속 손절 보호)
_risk_state = {
    "date":               None,   # "YYYY-MM-DD"
    "day_start_total":    0.0,    # 당일 시작 시점 총자산 (KRW + 포지션 투자금)
    "daily_realized_krw": 0.0,    # 당일 실현 손익 (원)
    "daily_blocked":      False,  # 일일 손실 한도 도달 → 당일 신규 매수 차단
    "consec_stoploss":    0,      # 연속 손절 횟수
    "buy_block_until":    None,   # 연속 손절 전역 쿨다운 해제 시각 (ISO str)
}

MAX_POSITIONS      = config.MAX_POSITIONS      # 최대 동시 보유 종목 수 (.env MAX_POSITIONS)
MIN_ORDER_KRW      = config.MIN_ORDER_KRW      # 업비트 최소 주문 금액 (.env MIN_ORDER_KRW)
SELL_COOLDOWN_MIN  = config.SELL_COOLDOWN_MIN  # 매도 후 재매수 금지 시간 (.env SELL_COOLDOWN_MIN)

_sell_cooldown: dict[str, datetime] = {}   # ticker → 매도 시각
_buy_fail: dict[str, dict] = {}            # ticker → {"count": int, "until": datetime|None}
_buy_confirm: dict[str, int] = {}          # ticker → 연속 score≥임계치 사이클 수 (진입 디바운스용)

# 자동 매매 절대 제외 코인 (보유 중이므로 매수·매도 금지)
TRADING_BLACKLIST = {"KRW-XRP", "KRW-CRO", "KRW-RVN"}

_MAX_LOG = 50

# 시장 레짐 캐시 (BTC 일봉 추세) — 매 사이클 재조회 대신 일정 시간 캐싱
_regime_cache = {"ts": None, "ok": True, "reason": ""}
_REGIME_TTL_SEC = 600   # 10분


def _market_regime_ok(client: UpbitClient, now_dt: datetime) -> tuple[bool, str]:
    """시장 프록시(BTC) 일봉 추세가 매수에 우호적인지 판정.

    ema_short >= ema_mid 이면 우호(상승/중립), 미만이면 하락 레짐 → 신규 매수 차단.
    하락장 롱 진입을 막아 거래당 기대값을 양으로 전환하는 핵심 필터(backtest 검증:
    365일 -39% 하락장에서 재설계 룰+이 필터 = 기대값 +0.15%). 실패 시 보수적으로 허용.
    """
    if not config.MARKET_REGIME_FILTER:
        return True, ""
    cached_ts = _regime_cache["ts"]
    if cached_ts and (now_dt - cached_ts).total_seconds() < _REGIME_TTL_SEC:
        return _regime_cache["ok"], _regime_cache["reason"]
    ok, reason = True, ""
    try:
        from core.indicators import add_all_indicators, get_latest_indicators
        df = client.get_ohlcv(config.MARKET_REGIME_PROXY, "day", 200)
        if df is not None and not df.empty:
            df = add_all_indicators(df)
            # 레짐 판정도 완성봉 기준 — 당일 봉이 미완성일 때 신호가 바뀌는 리페인팅 방지
            ind = get_latest_indicators(df, completed=True)
            es, em = ind.get("ema_short", 0), ind.get("ema_mid", 0)
            if es < em:
                ok = False
                reason = f"시장 하락 레짐 ({config.MARKET_REGIME_PROXY} 단기<중기 EMA) — 신규 매수 차단"
    except Exception as e:
        logging.warning(f"시장 레짐 판정 실패(보수적 허용): {e}")
    _regime_cache.update({"ts": now_dt, "ok": ok, "reason": reason})
    return ok, reason


# ─────────────────────────────────────────────
# 상태 영속화 (서버 재시작 대비)
# ─────────────────────────────────────────────

def _save_state_locked():
    """_trading_lock 보유 상태에서 호출. 핵심 상태를 JSON 파일로 저장."""
    try:
        snap = {
            "enabled":           _trading_state["enabled"],
            "live":              _trading_state["live"],
            "sim_krw":           _trading_state["sim_krw"],
            "sim_initial_total": _trading_state["sim_initial_total"],
            "positions":         _trading_state["positions"],
            "log":               _trading_state["log"],
            "sell_cooldown":     {t: dt.isoformat() for t, dt in _sell_cooldown.items()},
            "risk":              dict(_risk_state),
            "saved_at":          datetime.now(_KST).isoformat(),
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logging.warning("상태 저장 실패: %s", e)


def save_state():
    with _trading_lock:
        _save_state_locked()


def _load_state():
    """기동 시 이전 상태 복원. 매매가 켜진 채 재시작됐으면 자동 재개."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        logging.warning("상태 파일 로드 실패: %s", e)
        return

    with _trading_lock:
        _trading_state["enabled"]           = bool(snap.get("enabled", False))
        _trading_state["live"]              = bool(snap.get("live", config.LIVE_TRADING))
        _trading_state["sim_krw"]           = float(snap.get("sim_krw", 0.0))
        _trading_state["sim_initial_total"] = float(snap.get("sim_initial_total", 0.0))
        raw_positions = snap.get("positions", {}) or {}
        valid_positions = {}
        for ticker, pos in raw_positions.items():
            entry_price = float(pos.get("entry_price", 0))
            quantity    = float(pos.get("quantity", 0))
            slot_count  = int(pos.get("slot_count", 1))
            if entry_price <= 0 or quantity <= 0:
                logging.warning("포지션 스키마 검증 실패 (폐기) [%s]: entry_price=%s quantity=%s",
                                ticker, entry_price, quantity)
                continue
            if not (1 <= slot_count <= 10):
                logging.warning("포지션 slot_count 범위 오류 (1로 보정) [%s]: slot_count=%d",
                                ticker, slot_count)
                pos = {**pos, "slot_count": 1}
            valid_positions[ticker] = pos
        _trading_state["positions"] = valid_positions
        _trading_state["log"]               = (snap.get("log", []) or [])[:_MAX_LOG]
        for t, iso in (snap.get("sell_cooldown", {}) or {}).items():
            try:
                _sell_cooldown[t] = _as_kst(datetime.fromisoformat(iso))
            except Exception:
                pass
        risk = snap.get("risk") or {}
        for k in _risk_state:
            if k in risk:
                _risk_state[k] = risk[k]
        # 날짜가 바뀐 채로 재시작된 경우 일일 카운터 리셋
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        if _risk_state["date"] != today:
            _risk_state["date"]               = today
            _risk_state["daily_realized_krw"] = 0.0
            _risk_state["daily_blocked"]      = False
            _risk_state["consec_stoploss"]    = 0
        if _trading_state["enabled"]:
            mode = "실거래" if _trading_state["live"] else "시뮬레이션"
            _trading_state["status_msg"] = f"재시작 — 이전 {mode} 상태 복원, 자동 재개"
            logging.info("재시작 복원: %s 모드, 포지션 %d개 자동 재개",
                         mode, len(_trading_state["positions"]))


_last_prune_date: str | None = None


def _prune_trade_history():
    """`TRADE_HISTORY_RETENTION_DAYS`보다 오래된 거래 이력을 정리(파일 원자적 재작성).
    레코드의 'date'(YYYY-MM-DD) 기준. 날짜가 없거나 파싱 불가한 라인은 안전하게 보존한다.
    0이면 영구 보존(정리 안 함)."""
    days = config.TRADE_HISTORY_RETENTION_DAYS
    if days <= 0 or not os.path.exists(TRADE_LOG_FILE):
        return
    cutoff = (datetime.now(_KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        kept, dropped = [], 0
        with open(TRADE_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line).get("date")
                except Exception:
                    d = None
                if d is None or d >= cutoff:   # 날짜 미상은 보존
                    kept.append(line.rstrip("\n"))
                else:
                    dropped += 1
        if dropped == 0:
            return
        tmp = TRADE_LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")
        os.replace(tmp, TRADE_LOG_FILE)
        logging.info("거래 이력 정리: %d일 경과 %d건 삭제 (보존 %d건)", days, dropped, len(kept))
    except Exception as e:
        logging.warning("거래 이력 정리 실패: %s", e)


def _maybe_prune_trade_history():
    """하루 1회만 실제 정리하도록 일자 게이트. 워커 매 사이클에서 호출(대부분 즉시 반환)."""
    global _last_prune_date
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    if _last_prune_date == today:
        return
    _last_prune_date = today
    _prune_trade_history()


def _log_trade(entry: dict):
    entry.setdefault("date", datetime.now(_KST).strftime("%Y-%m-%d"))
    with _trading_lock:
        _trading_state["log"].insert(0, entry)
        _trading_state["log"] = _trading_state["log"][:_MAX_LOG]
    # 관망(hold) 로그는 30초마다 발생하므로 파일에는 기록하지 않음
    if entry.get("type") == "hold":
        return
    try:
        with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.warning("거래 이력 파일 기록 실패: %s", e)
    notifier.notify_trade(entry)
    if entry.get("type") in ("buy", "sell"):
        try:
            sc = _sheets_mod.get_client()
            if sc is not None:
                row = [
                    entry.get("date", ""),
                    entry.get("time", ""),
                    entry.get("type", ""),
                    entry.get("ticker", ""),
                    entry.get("reason", ""),
                    entry.get("price", ""),
                    entry.get("amount", ""),
                    entry.get("profit_pct", ""),
                    entry.get("live", ""),
                    entry.get("threshold", ""),
                    entry.get("tp_pct", ""),
                    entry.get("sl_pct", ""),
                    entry.get("trailing_start_pct", ""),
                    entry.get("trailing_stop_pct", ""),
                    entry.get("max_hold_hours", ""),
                ]
                sc.append("코인", row)
        except Exception as e:
            logging.warning("Sheets 코인 전송 실패: %s", e)


# ─────────────────────────────────────────────
# 리스크 관리 (일일 한도 · 연속 손절 보호)
# ─────────────────────────────────────────────

def _roll_risk_day_locked(total_assets: float):
    """날짜가 바뀌면 일일 리스크 카운터 리셋. _trading_lock 보유 상태에서 호출."""
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    if _risk_state["date"] != today:
        _risk_state["date"]               = today
        _risk_state["day_start_total"]    = total_assets
        _risk_state["daily_realized_krw"] = 0.0
        _risk_state["daily_blocked"]      = False
        _risk_state["consec_stoploss"]    = 0


def _record_sell_locked(profit_pct: float, amount_krw: float, is_stoploss: bool):
    """매도 확정 후 리스크 카운터 갱신. _trading_lock 보유 상태에서 호출."""
    _risk_state["daily_realized_krw"] += amount_krw * profit_pct / 100.0

    if is_stoploss:
        _risk_state["consec_stoploss"] += 1
        if _risk_state["consec_stoploss"] >= config.MAX_CONSECUTIVE_STOPLOSS:
            until = datetime.now(_KST) + timedelta(minutes=config.GLOBAL_BUY_COOLDOWN_MIN)
            _risk_state["buy_block_until"] = until.isoformat()
            logging.warning("연속 손절 %d회 → %d분간 전역 매수 차단",
                            _risk_state["consec_stoploss"], config.GLOBAL_BUY_COOLDOWN_MIN)
            notifier.notify_event(
                "🛑 연속 손절 보호 발동",
                f"연속 손절 {_risk_state['consec_stoploss']}회 → "
                f"{config.GLOBAL_BUY_COOLDOWN_MIN}분간 신규 매수 차단 (~{until.strftime('%H:%M')})")
    elif profit_pct > 0:
        _risk_state["consec_stoploss"] = 0

    base = _risk_state["day_start_total"]
    if base > 0 and _risk_state["daily_realized_krw"] <= -base * config.DAILY_LOSS_LIMIT_PCT / 100.0:
        if not _risk_state["daily_blocked"]:
            logging.warning("일일 손실 한도 도달 (실현 %.0f원) → 당일 신규 매수 차단",
                            _risk_state["daily_realized_krw"])
            notifier.notify_event(
                "🛑 일일 손실 한도 도달",
                f"당일 실현손익 {_risk_state['daily_realized_krw']:,.0f}원 "
                f"(한도 -{config.DAILY_LOSS_LIMIT_PCT}%) → 오늘 신규 매수 차단")
        _risk_state["daily_blocked"] = True


def _buy_block_reason_locked() -> str | None:
    """전역 매수 차단 사유. 없으면 None. _trading_lock 보유 상태에서 호출."""
    if _risk_state["daily_blocked"]:
        return f"일일 손실 한도 -{config.DAILY_LOSS_LIMIT_PCT}% 도달"
    bbu = _risk_state["buy_block_until"]
    if bbu:
        try:
            if datetime.now(_KST) < _as_kst(datetime.fromisoformat(bbu)):
                return f"연속 손절 보호 쿨다운 (~{bbu[11:16]})"
            # 쿨다운 소진 → 연속 손절 카운터도 리셋(스트릭 종료).
            # 그러지 않으면 카운터가 ≥MAX로 남아, 쿨다운 직후 첫 손절이 즉시
            # 또 다른 쿨다운을 발동시켜 보호가 '120분 반복 멈춤'으로 변질된다.
            _risk_state["buy_block_until"] = None
            _risk_state["consec_stoploss"] = 0
        except Exception:
            _risk_state["buy_block_until"] = None
            _risk_state["consec_stoploss"] = 0
    return None


def _market_age_sec() -> float | None:
    """마켓 캐시의 나이(초). 알 수 없으면 None."""
    with _market_lock:
        upd = _market_cache.get("updated_at")
    if not upd:
        return None
    try:
        dt = datetime.fromisoformat(upd)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def get_risk_snapshot() -> dict:
    """상태 API용 리스크 정보 스냅샷"""
    with _trading_lock:
        snap = dict(_risk_state)
        snap["buy_block_reason"] = _buy_block_reason_locked()
    snap["market_age_sec"] = _market_age_sec()
    return snap


def _perf_bucket(sells: list) -> dict:
    """매도 체결 목록 → 성과 지표 집계."""
    n = len(sells)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "realized_krw": 0.0,
                "avg_profit_pct": 0.0, "best_pct": 0.0, "worst_pct": 0.0}
    pcts = [float(s.get("profit_pct") or 0) for s in sells]
    realized = sum(float(s.get("amount") or 0) * p / 100.0 for s, p in zip(sells, pcts))
    wins = sum(1 for p in pcts if p > 0)
    return {
        "n":              n,
        "win_rate":       round(wins / n * 100, 1),
        "realized_krw":   round(realized),
        "avg_profit_pct": round(sum(pcts) / n, 2),
        "best_pct":       round(max(pcts), 2),
        "worst_pct":      round(min(pcts), 2),
    }


def compute_performance() -> dict:
    """trade_history.jsonl 전체를 읽어 누적 실현 성과를 집계 (시뮬/실거래 분리)."""
    sells_live, sells_sim = [], []
    try:
        if os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("type") != "sell":
                        continue
                    (sells_live if e.get("live") else sells_sim).append(e)
    except Exception as ex:
        logging.warning("성과 집계 실패: %s", ex)

    return {
        "all":  _perf_bucket(sells_live + sells_sim),
        "live": _perf_bucket(sells_live),
        "sim":  _perf_bucket(sells_sim),
    }


def _cycle_active(epoch: int) -> bool:
    """진행 중 사이클이 여전히 유효한지 (stop/start로 무효화되지 않았는지)"""
    with _trading_lock:
        return _trading_state["enabled"] and _trading_state["epoch"] == epoch


def _buy_banned(ticker: str, now_dt: datetime) -> bool:
    """매수 실패 백오프 중인 종목인지"""
    info = _buy_fail.get(ticker)
    return bool(info and info.get("until") and now_dt < info["until"])


def _record_buy_fail(ticker: str):
    info = _buy_fail.setdefault(ticker, {"count": 0, "until": None})
    info["count"] += 1
    if info["count"] >= config.BUY_FAIL_LIMIT:
        info["until"] = datetime.now(_KST) + timedelta(minutes=config.BUY_FAIL_COOLDOWN_MIN)
        info["count"] = 0
        logging.warning("[%s] 매수 연속 실패 %d회 → %d분 매수 금지",
                        ticker, config.BUY_FAIL_LIMIT, config.BUY_FAIL_COOLDOWN_MIN)


# ─────────────────────────────────────────────
# 주문 실행 헬퍼
# ─────────────────────────────────────────────

def _execute_live_sell(client: UpbitClient, ticker: str, tracked_qty: float):
    """실잔고 조회 후 시장가 매도. 반환: (성공여부, 매도수량, 오류메시지)"""
    qty = client.get_coin_balance(ticker)
    if qty is None:
        qty = tracked_qty   # 잔고 조회 실패 → 추적 수량으로 폴백
    if qty <= 0:
        return False, 0.0, "잔고 없음"
    order = client.sell_market_order_direct(ticker, qty)
    if order.get("uuid"):
        return True, qty, ""
    err = order.get("error", {})
    return False, qty, f"{err.get('name', '?')} {str(err.get('message', ''))[:50]}"


def _execute_live_buy(client: UpbitClient, ticker: str, amount: float, est_price: float):
    """시장가 매수 + uuid 체결 조회로 실제 체결가/수량 확정.
    반환: (성공여부, 체결수량, 체결단가, 오류메시지)"""
    order = client.buy_market_order_direct(ticker, amount)
    if not order.get("uuid"):
        if order.get("error"):
            err = order["error"]
            msg = f"API오류 {order.get('_http_status', '?')}: {err.get('name', '?')} {str(err.get('message', ''))[:50]}"
        else:
            msg = f"응답이상: {str(order)[:80]}"
        return False, 0.0, 0.0, msg

    # 체결 조회 (시장가 매수는 보통 1초 내 체결)
    fill = client.get_order_result(order["uuid"])
    if fill and fill["executed_volume"] > 0:
        qty = fill["executed_volume"]
        avg = fill["avg_price"] or (amount / qty)
        return True, qty, avg, ""
    # 체결 조회 실패 → 추정치 폴백 (현재가 기준)
    qty = amount / est_price if est_price > 0 else 0.0
    return True, qty, est_price, "[체결조회실패-추정치]"


# ─────────────────────────────────────────────
# 실거래 포지션 동기화
# ─────────────────────────────────────────────

def _sync_live_positions(client, now_str: str):
    """실거래 모드: 실제 업비트 잔고와 _trading_state["positions"] 동기화"""
    try:
        balances = client.upbit.get_balances()
    except Exception:
        return   # 잔고 조회 실패 → 이번 사이클 동기화 스킵 (포지션 보존)

    actual  = {}
    unknown = set()   # 현재가 조회 실패로 보유 여부 판단 불가한 코인 (제거 금지)
    for b in balances:
        currency = b['currency']
        if currency == 'KRW':
            continue
        ticker = f"KRW-{currency}"
        if ticker in TRADING_BLACKLIST:
            continue
        vol = float(b.get('balance', 0))
        if vol <= 0:
            continue
        avg = float(b.get('avg_buy_price', 0))
        # avg_buy_price가 0이면 현재가 사용 (에어드롭·이벤트 지급 코인)
        if avg <= 0:
            try:
                avg = client.get_current_price(ticker) or 0
            except Exception:
                pass
        if avg > 0:
            actual[ticker] = (vol, avg)
        else:
            # 가격 조회 실패 — "팔렸다"고 단정하면 안 됨
            unknown.add(ticker)

    with _trading_lock:
        positions = _trading_state["positions"]
        changed = False
        # 실제로 팔린 코인은 포지션에서 제거 (가격 조회 실패 코인은 보존)
        for ticker in list(positions.keys()):
            if ticker not in actual and ticker not in unknown:
                positions.pop(ticker)
                changed = True
        # 실제 보유 중이지만 포지션에 없는 코인 추가 (슬롯 여유분 내, dust 제외)
        for ticker, (vol, avg) in actual.items():
            if ticker in positions:
                continue
            if vol * avg < MIN_ORDER_KRW:
                continue   # 최소주문금액 미만 dust는 슬롯을 점유시키지 않음
            used = sum(p.get("slot_count", 1) for p in positions.values())
            if used < MAX_POSITIONS:
                positions[ticker] = {
                    "entry_price": avg,
                    "amount_krw":  round(vol * avg),
                    "quantity":    vol,
                    "entry_time":  now_str,
                    "entry_ts":    datetime.now(_KST).isoformat(),
                    "bot_bought":  False,
                    "slot_count":  1,
                    "peak_price":  avg,
                }
                changed = True
        if changed:
            _save_state_locked()


# ─────────────────────────────────────────────
# 자동 매매 사이클
# ─────────────────────────────────────────────

def run_auto_trade():
    """자동 매매 1사이클: 분산 매수 + 손절/익절/트레일링 관리"""
    client  = UpbitClient()
    now_dt  = datetime.now(_KST)
    now_str = now_dt.strftime("%H:%M:%S")

    with _trading_lock:
        if not _trading_state["enabled"]:
            return
        epoch     = _trading_state["epoch"]
        live_mode = _trading_state["live"]
        _trading_state["last_check"] = now_str
        _trading_state["status_msg"] = f"분석 중... ({now_str})"

    # 실거래 모드: 사이클 시작 시 실제 잔고와 포지션 동기화
    if live_mode and client.upbit:
        _sync_live_positions(client, now_str)

    # 마켓 캐시 스냅샷 + 신선도 확인
    with _market_lock:
        market_coins = list(_market_cache.get("coins", []))
    market_map = {c["ticker"]: c for c in market_coins}
    # 보유 코인이 거래량 톱20에서 밀리거나 수집 실패 시 holdings 캐시로 보완
    # holdings의 action_class(일봉×2+4h×1.5+1h×1 가중)는 market 체계보다 보수적이라
    # 매도 보호(sell-strong) 용도로 안전 — None으로 두면 지표 매도가 조용히 비활성화됨
    with _cache_lock:
        for h in _cache.get("holdings", []):
            if h["ticker"] not in market_map:
                market_map[h["ticker"]] = {
                    "current":      h.get("current_price"),
                    "action_class": h.get("action_class"),
                    "action_text":  h.get("action_text", ""),
                }

    market_age   = _market_age_sec()
    market_stale = market_age is None or market_age > config.MARKET_STALE_SEC

    stop_loss_pct   = config.MAX_LOSS_PERCENT     # 기본 3.0%
    take_profit_pct = config.TAKE_PROFIT_PERCENT  # 기본 5.0%

    # KRW 잔고 (일일 리스크 기준점 + Phase 2 매수 예산)
    if live_mode:
        try:
            krw_balance = client.get_balance_krw()
        except Exception:
            krw_balance = 0.0
    else:
        with _trading_lock:
            krw_balance = _trading_state["sim_krw"]

    # 일일 리스크 카운터 날짜 갱신
    with _trading_lock:
        invested = sum(p.get("amount_krw", 0) for p in _trading_state["positions"].values())
        _roll_risk_day_locked(krw_balance + invested)

    # ─── Phase 1: 기존 포지션 손절/익절/트레일링/매도신호 체크 ───
    with _trading_lock:
        positions_snap = dict(_trading_state["positions"])

    # 업비트 거래지원 종료 감지 (lazy import: 순환 import 방지)
    from core.ai_analysis import get_active_markets
    active_mkts = get_active_markets()

    sold_any = False

    for ticker, pos in list(positions_snap.items()):
        if ticker in TRADING_BLACKLIST:
            continue

        # 손절/익절 판정은 캐시 대신 실시간 가격 우선 (보유 종목 ≤ 5개라 부담 적음)
        current_price = None
        try:
            current_price = client.get_current_price(ticker) or None
        except Exception:
            pass
        coin_data = market_map.get(ticker)
        if current_price is None and coin_data:
            current_price = coin_data["current"]
        if not current_price:
            continue

        entry_price = pos["entry_price"]
        if entry_price <= 0:
            continue

        # 고점 추적 (트레일링 스탑용)
        peak_price = max(pos.get("peak_price", entry_price), current_price)
        with _trading_lock:
            if ticker in _trading_state["positions"]:
                _trading_state["positions"][ticker]["peak_price"] = peak_price

        # dust 포지션: 평가액이 최소주문금액 미만이면 매도 주문 불가 → 스킵 (1회만 로그)
        if live_mode and current_price * pos["quantity"] < MIN_ORDER_KRW:
            if not pos.get("dust_logged"):
                with _trading_lock:
                    if ticker in _trading_state["positions"]:
                        _trading_state["positions"][ticker]["dust_logged"] = True
                logging.info("[%s] 평가액 %.0f원 < 최소주문금액 — dust 포지션, 매도 스킵",
                             ticker, current_price * pos["quantity"])
            continue

        # 수수료 반영 실수령 기준 수익률 (시뮬·실거래 동일 회계)
        net_sell_price  = current_price * (1 - FEE_RATE)
        effective_entry = entry_price * (1 + FEE_RATE)
        profit_pct      = (net_sell_price - effective_entry) / effective_entry * 100
        peak_profit_pct = (peak_price * (1 - FEE_RATE) - effective_entry) / effective_entry * 100

        # 보유 경과 시간 (time-stop용)
        held_hours = None
        entry_ts = pos.get("entry_ts")
        if entry_ts:
            try:
                held_hours = (now_dt - _as_kst(datetime.fromisoformat(entry_ts))).total_seconds() / 3600
            except Exception:
                pass

        should_sell  = False
        sell_reason  = ""
        is_stoploss  = False

        if active_mkts and ticker not in active_mkts:
            should_sell = True
            sell_reason = f"업비트 거래지원 종료 감지 ({profit_pct:+.1f}%)"
        elif profit_pct >= take_profit_pct:
            should_sell = True
            sell_reason = f"익절 {profit_pct:+.1f}% (목표 +{take_profit_pct}%)"
        elif profit_pct <= -stop_loss_pct:
            should_sell = True
            is_stoploss = True
            sell_reason = f"손절 {profit_pct:+.1f}% (한도 -{stop_loss_pct}%)"
        elif (peak_profit_pct >= config.TRAILING_START_PCT
              and current_price <= peak_price * (1 - config.TRAILING_STOP_PCT / 100)):
            should_sell = True
            sell_reason = (f"트레일링 스탑 {profit_pct:+.1f}% "
                           f"(고점 {peak_profit_pct:+.1f}% 대비 -{config.TRAILING_STOP_PCT}%)")
        elif coin_data and coin_data.get("action_class") == "sell-strong":
            should_sell = True
            sell_reason = f"매도 신호: {coin_data.get('action_text', '매도')} ({profit_pct:+.1f}%)"
        elif held_hours is not None and held_hours >= config.MAX_HOLD_HOURS:
            should_sell = True
            sell_reason = f"보유시간 초과 {held_hours:.0f}h ≥ {config.MAX_HOLD_HOURS:.0f}h ({profit_pct:+.1f}%)"

        if not should_sell:
            continue
        if not _cycle_active(epoch):
            return   # 사이클 도중 stop/start 발생 → 즉시 중단

        sold_qty = pos["quantity"]
        if live_mode and client.upbit:
            ok, sold_qty, err = _execute_live_sell(client, ticker, pos["quantity"])
            if not ok:
                # 매도 실패 → 포지션 유지, 다음 사이클 재시도 (로그는 압축)
                fail_n = pos.get("sell_fail_count", 0) + 1
                with _trading_lock:
                    if ticker in _trading_state["positions"]:
                        _trading_state["positions"][ticker]["sell_fail_count"] = fail_n
                if fail_n == 1 or fail_n % 10 == 0:
                    _log_trade({
                        "time": now_str, "type": "sell_fail", "ticker": ticker,
                        "reason": f"매도 실패({fail_n}회): {sell_reason} [{err}]",
                        "price": current_price, "amount": pos["amount_krw"], "live": live_mode,
                    })
                    logging.warning("[%s] 매도 주문 실패 %d회: %s", ticker, fail_n, err)
                continue

        sell_proceeds = sold_qty * current_price * (1 - FEE_RATE)

        _log_trade({
            "time":               now_str,
            "type":               "sell",
            "ticker":             ticker,
            "reason":             sell_reason,
            "price":              current_price,
            "amount":             pos["amount_krw"],
            "profit_pct":         round(profit_pct, 2),
            "live":               live_mode,
            "threshold":          config.BUY_SCORE_THRESHOLD,
            "tp_pct":             config.TAKE_PROFIT_PERCENT,
            "sl_pct":             config.MAX_LOSS_PERCENT,
            "trailing_start_pct": config.TRAILING_START_PCT,
            "trailing_stop_pct":  config.TRAILING_STOP_PCT,
            "max_hold_hours":     config.MAX_HOLD_HOURS,
        })
        with _trading_lock:
            _trading_state["positions"].pop(ticker, None)
            if not live_mode:
                _trading_state["sim_krw"] += sell_proceeds
            _sell_cooldown[ticker] = datetime.now(_KST)
            _record_sell_locked(profit_pct, pos["amount_krw"], is_stoploss)
            _save_state_locked()
        sold_any = True

    # ─── Phase 2: 신규 매수 (빈 슬롯 전체를 한 번에 채우기) ───
    with _trading_lock:
        current_positions = dict(_trading_state["positions"])
        cooldown_snap     = dict(_sell_cooldown)
        buy_block_reason  = _buy_block_reason_locked()

    # KRW 잔고 재조회 (Phase 1 매도 반영)
    if live_mode:
        try:
            krw_balance = client.get_balance_krw()
        except Exception:
            krw_balance = 0.0
    else:
        with _trading_lock:
            krw_balance = _trading_state["sim_krw"]

    used_slots      = sum(p.get("slot_count", 1) for p in current_positions.values())
    available_slots = MAX_POSITIONS - used_slots
    # 포지션 사이징: 기본은 잔고/빈슬롯 균등. EQUAL_WEIGHT_SIZING이면 코인당 금액을
    # (총자산/MAX_POSITIONS)로 캡 — 빈 슬롯 1개에 잔고 전액이 몰리는 집중 리스크 방지.
    if available_slots > 0:
        per_coin = krw_balance / available_slots
        if config.EQUAL_WEIGHT_SIZING:
            invested_now = sum(p.get("amount_krw", 0) for p in current_positions.values())
            equity_target = (krw_balance + invested_now) / MAX_POSITIONS
            per_coin = min(per_coin, equity_target)
    else:
        per_coin = 0

    # Fear & Greed 양극단 모두 신규 매수 차단 (시뮬에서는 데이터 축적을 위해 건너뜀)
    from core.ai_analysis import get_fear_greed
    fg = get_fear_greed()
    fg_block = None
    if live_mode and fg is not None:
        if fg["value"] >= config.FEAR_GREED_GREED_MAX:
            fg_block = f"F&G {fg['value']} 극단탐욕"
        elif fg["value"] <= config.FEAR_GREED_FEAR_MIN:
            fg_block = f"F&G {fg['value']} 극단공포"

    # 시장 레짐 필터 (BTC 일봉 하락 추세면 전 종목 신규 매수 차단)
    # 시뮬 모드에서는 레짐 필터를 건너뜀 — 실돈 위험 없으므로 데이터 축적 우선
    if live_mode:
        regime_ok, regime_reason = _market_regime_ok(client, now_dt)
    else:
        regime_ok, regime_reason = True, ""

    # 신규 매수 차단 사유 종합
    skip_buy_reason = None
    if buy_block_reason:
        skip_buy_reason = buy_block_reason
    elif not regime_ok:
        skip_buy_reason = regime_reason
    elif fg_block:
        skip_buy_reason = f"{fg_block} — 매수 차단"
    elif market_stale:
        age_txt = f"{market_age:.0f}초" if market_age is not None else "알 수 없음"
        skip_buy_reason = f"시장 데이터 노후 ({age_txt}) — 매수 보류"

    # 진입 디바운스 카운터 갱신: 강한 매수 신호(score≥임계치)를 연속 유지한 사이클 수를 센다.
    # BUY_CONFIRM_TICKS사이클 이상 유지된 후보만 진입 → 미완성 캔들의 순간 스파이크 진입 방지.
    signal_set = {c["ticker"] for c in market_coins
                  if c.get("action_class") == "buy-strong"
                  and c.get("total_score", 0) >= config.BUY_SCORE_THRESHOLD}
    for tk in list(_buy_confirm.keys()):
        if tk not in signal_set:
            del _buy_confirm[tk]
    for tk in signal_set:
        _buy_confirm[tk] = _buy_confirm.get(tk, 0) + 1

    candidates = []
    if skip_buy_reason is None:
        candidates = sorted(
            [c for c in market_coins
             if c.get("action_class") == "buy-strong"
             and c.get("total_score", 0) >= config.BUY_SCORE_THRESHOLD
             and _buy_confirm.get(c["ticker"], 0) >= config.BUY_CONFIRM_TICKS
             and c["ticker"] not in current_positions
             and c["ticker"] not in TRADING_BLACKLIST
             and not _buy_banned(c["ticker"], now_dt)
             and (c["ticker"] not in cooldown_snap
                  or (now_dt - cooldown_snap[c["ticker"]]) >= timedelta(minutes=SELL_COOLDOWN_MIN))],
            key=lambda x: x.get("total_score", 0),
            reverse=True,
        )

    bought_any = False
    phase2_spent = 0.0   # 이번 사이클 Phase 2 매수 총액 (Phase 2.5 잔고 추정용)

    # 조건을 넘는 후보를 빈 슬롯만큼 한 번에 매수 (총 보유 5개 상한)
    if available_slots > 0 and candidates and per_coin >= MIN_ORDER_KRW:
        for coin in candidates[:available_slots]:
            amount      = round(per_coin * 0.999)  # 수수료(0.05%) + 잔고 변동 버퍼
            ticker      = coin["ticker"]
            entry_price = coin.get("current", 0)
            if entry_price <= 0:
                continue
            if not _cycle_active(epoch):
                return
            buy_reason = f"{coin.get('action_text', '강한 매수')} (점수 {coin['total_score']:.1f})"

            if live_mode and client.upbit:
                time.sleep(1)  # 직전 API 호출 레이트 리밋 방지
                ok, actual_qty, stored_entry, err = _execute_live_buy(client, ticker, amount, entry_price)
                if not ok:
                    _record_buy_fail(ticker)
                    _log_trade({
                        "time": now_str, "type": "buy_fail", "ticker": ticker,
                        "reason": f"{buy_reason} [{err}]",
                        "price": entry_price, "amount": amount, "live": live_mode,
                    })
                    continue
                if err:
                    buy_reason += f" {err}"
                _buy_fail.pop(ticker, None)
            else:
                # 시뮬 수수료: 매수 시 수수료(FEE_RATE)가 차감된 수량으로 진입
                # (sim_krw에서 amount*(1+FEE_RATE)를 뺐으므로 수량도 동일 기준으로 계산)
                actual_qty   = amount * (1 - FEE_RATE) / entry_price
                stored_entry = entry_price

            _log_trade({
                "time":      now_str,
                "type":      "buy",
                "ticker":    ticker,
                "reason":    buy_reason,
                "price":     stored_entry,
                "amount":    amount,
                "live":      live_mode,
                "threshold": config.BUY_SCORE_THRESHOLD,
            })
            with _trading_lock:
                if _trading_state["epoch"] != epoch:
                    return
                _trading_state["positions"][ticker] = {
                    "entry_price": stored_entry,
                    "amount_krw":  amount,
                    "quantity":    actual_qty,
                    "entry_time":  now_str,
                    "entry_ts":    now_dt.isoformat(),
                    "bot_bought":  True,
                    "slot_count":  1,
                    "peak_price":  stored_entry,
                }
                if not live_mode:
                    _trading_state["sim_krw"] = max(0.0, _trading_state["sim_krw"] - amount * (1 + FEE_RATE))
                _save_state_locked()
            _buy_confirm.pop(ticker, None)   # 진입 완료 → 디바운스 카운터 리셋
            phase2_spent += amount
            bought_any = True

    # ─── Phase 2.5: 추가매수 (마지막 매수 1시간 경과 + 강한 매수 신호 + 수익 구간) ───
    with _trading_lock:
        positions_for_add = dict(_trading_state["positions"])

    used_slots2     = sum(p.get("slot_count", 1) for p in positions_for_add.values())
    remaining_slots = MAX_POSITIONS - used_slots2

    if config.ADD_BUY_ENABLED and remaining_slots > 0 and skip_buy_reason is None:
        if live_mode:
            # Phase 2 직후 잔고 = Phase 2 재조회분 - 이번 사이클 매수액 (API 재호출 절약)
            krw2 = max(0.0, krw_balance - phase2_spent)
        else:
            with _trading_lock:
                krw2 = _trading_state["sim_krw"]

        per_coin2 = krw2 / remaining_slots if remaining_slots > 0 else 0
        # 사이징 캡: 추가매수도 코인당 (총자산/MAX_POSITIONS) 상한 적용
        if config.EQUAL_WEIGHT_SIZING:
            invested2 = sum(p.get("amount_krw", 0) for p in positions_for_add.values())
            per_coin2 = min(per_coin2, (krw2 + invested2) / MAX_POSITIONS)

        for ticker, pos in list(positions_for_add.items()):
            if remaining_slots <= 0:
                break
            if ticker in TRADING_BLACKLIST:
                continue
            # 종목당 슬롯 상한 — 한 종목에 자본이 과도하게 쏠리는 것 방지
            if pos.get("slot_count", 1) >= config.MAX_SLOTS_PER_TICKER:
                continue

            # 마지막 매수(최초 진입 또는 직전 추가매수) 후 1시간 경과 확인
            last_ts = pos.get("last_add_ts") or pos.get("entry_ts")
            if not last_ts:
                continue
            try:
                last_dt = _as_kst(datetime.fromisoformat(last_ts))
            except Exception:
                continue
            if (now_dt - last_dt) < timedelta(hours=1):
                continue

            # 현재가 및 수익률 확인
            coin_data = market_map.get(ticker)
            current_price = coin_data.get("current") if coin_data else None
            if not current_price:
                try:
                    current_price = client.get_current_price(ticker) or None
                except Exception:
                    pass
            if not current_price:
                continue

            entry_price = pos["entry_price"]
            if entry_price <= 0:
                continue
            net_price       = current_price * (1 - FEE_RATE)
            effective_entry = entry_price * (1 + FEE_RATE)
            profit_pct      = (net_price - effective_entry) / effective_entry * 100

            # 수익 구간에서만 추가매수 (손실 중 물타기 방지)
            if profit_pct < config.ADD_BUY_MIN_PROFIT or profit_pct > config.ADD_BUY_MAX_PROFIT:
                continue

            # 강한 매수 신호 확인
            if not coin_data or coin_data.get("total_score", 0) < config.BUY_SCORE_THRESHOLD:
                continue

            amount2 = round(per_coin2 * 0.999)
            if amount2 < MIN_ORDER_KRW:
                continue
            if not _cycle_active(epoch):
                return

            add_reason = (f"추가매수: {coin_data.get('action_text', '강한 매수')} "
                          f"(점수 {coin_data['total_score']:.1f}, 수익 {profit_pct:+.1f}%)")

            if live_mode and client.upbit:
                time.sleep(1)
                ok, actual_qty2, fill_price2, err = _execute_live_buy(client, ticker, amount2, current_price)
                if not ok:
                    _record_buy_fail(ticker)
                    _log_trade({
                        "time": now_str, "type": "buy_fail", "ticker": ticker,
                        "reason": f"{add_reason} [{err}]",
                        "price": current_price, "amount": amount2, "live": live_mode,
                    })
                    continue
                if err:
                    add_reason += f" {err}"
                _buy_fail.pop(ticker, None)
            else:
                actual_qty2 = amount2 * (1 - FEE_RATE) / current_price
                fill_price2 = current_price

            _log_trade({
                "time":      now_str,
                "type":      "buy",
                "ticker":    ticker,
                "reason":    add_reason,
                "price":     fill_price2,
                "amount":    amount2,
                "live":      live_mode,
                "threshold": config.BUY_SCORE_THRESHOLD,
            })
            with _trading_lock:
                if _trading_state["epoch"] != epoch:
                    return
                p = _trading_state["positions"].get(ticker)
                if p is None:
                    continue
                new_qty    = p["quantity"] + actual_qty2
                new_amount = p["amount_krw"] + amount2
                p["entry_price"] = new_amount / new_qty   # 가중평균 단가 갱신
                p["quantity"]    = new_qty
                p["amount_krw"]  = new_amount
                p["slot_count"]  = p.get("slot_count", 1) + 1
                p["last_add_ts"] = now_dt.isoformat()     # 추가매수 쿨다운 기준 갱신
                p["peak_price"]  = max(p.get("peak_price", 0), fill_price2)
                if not live_mode:
                    _trading_state["sim_krw"] = max(0.0, _trading_state["sim_krw"] - amount2 * (1 + FEE_RATE))
                _save_state_locked()
            bought_any = True
            remaining_slots -= 1

    # 매매가 전혀 없었고 포지션도 없으면 관망 로그 (매도 직후 사이클엔 생략)
    if not bought_any and not sold_any and not current_positions:
        if skip_buy_reason:
            extra = f", {skip_buy_reason}"
        else:
            extra = f", F&G {fg['value']}" if fg else ""
        _log_trade({
            "time":   now_str,
            "type":   "hold",
            "ticker": "-",
            "reason": f"매수 신호 없음 (후보 {len(candidates)}개, 잔고 {krw_balance:,.0f}원, "
                      f"슬롯 {available_slots}/{MAX_POSITIONS}{extra})",
            "price":  0,
            "amount": 0,
            "live":   live_mode,
        })

    with _trading_lock:
        suffix = f" · {skip_buy_reason}" if skip_buy_reason else ""
        _trading_state["status_msg"] = f"마지막 체크: {now_str}{suffix}"


_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def build_daily_report(period_start: datetime, period_end: datetime) -> str:
    """period_start ~ period_end 기간의 거래 통계를 HTML 문자열로 반환."""
    records = []
    try:
        if os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("type") in ("hold",):
                        continue
                    try:
                        ts = datetime.fromisoformat(
                            f"{r['date']}T{r['time']}"
                        ).replace(tzinfo=_KST)
                    except Exception:
                        continue
                    if period_start <= ts <= period_end:
                        records.append(r)
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning("일일 보고서 파일 읽기 실패: %s", e)
        return "⚠️ 거래 기록을 읽는 중 오류가 발생했습니다."

    live_recs = [r for r in records if r.get("live")]
    sim_recs  = [r for r in records if not r.get("live")]

    def _bucket_html(recs: list) -> str:
        buys      = [r for r in recs if r.get("type") == "buy"]
        sells     = [r for r in recs if r.get("type") == "sell"]
        buy_fails = [r for r in recs if r.get("type") == "buy_fail"]
        sel_fails = [r for r in recs if r.get("type") == "sell_fail"]

        if not buys and not sells and not buy_fails and not sel_fails:
            return "거래 없음"

        lines = []

        fail_suffix = f"(실패 {len(sel_fails)}건)" if sel_fails else ""
        sell_warn   = "⚠️ " if sel_fails else ""
        sell_str    = f"매수 {len(buys)}건 · {sell_warn}매도 {len(sells)}건"
        if fail_suffix:
            sell_str += f" {fail_suffix}"
        if buy_fails:
            sell_str += f" · 매수실패 {len(buy_fails)}건"
        lines.append(sell_str)

        if sells:
            pcts  = [float(s.get("profit_pct") or 0) for s in sells]
            wins  = sum(1 for p in pcts if p > 0)
            losses = len(pcts) - wins
            win_rate = wins / len(pcts) * 100
            lines.append(f"승률: {wins}승 {losses}패 ({win_rate:.1f}%)")

            realized = sum(
                float(s.get("amount") or 0) * float(s.get("profit_pct") or 0) / 100
                for s in sells
            )
            sign = "+" if realized >= 0 else ""
            lines.append(f"실현손익: {sign}{realized:,.0f}원")

            avg_pct  = sum(pcts) / len(pcts)
            best_pct = max(pcts)
            worst_pct = min(pcts)
            lines.append(
                f"평균수익률: {avg_pct:+.2f}% | 최고 {best_pct:+.2f}% | 최저 {worst_pct:+.2f}%"
            )

        lines.append("\n📥 매수 내역")
        buy_lines = []
        for r in buys:
            ticker = html.escape(r.get("ticker", "-")).replace("KRW-", "")
            amt = float(r.get("amount") or 0)
            price = float(r.get("price") or 0)
            buy_lines.append(f"  · {ticker}  {amt:,.0f}원 @ {price:,.0f}")
        if len(buy_lines) <= 5:
            lines.extend(buy_lines)
        else:
            lines.extend(buy_lines[:5])
            lines.append(f"  (외 {len(buy_lines)-5}건)")

        if sells:
            lines.append("\n📤 매도 내역")
            show_sells = sells[:5]
            for s in show_sells:
                ticker = html.escape(s.get("ticker", "-")).replace("KRW-", "")
                pct = float(s.get("profit_pct") or 0)
                amt = float(s.get("amount") or 0)
                reason = html.escape(str(s.get("reason") or "-"))
                sign = "+" if pct >= 0 else ""
                lines.append(f"  · {ticker}  {sign}{pct:.2f}%  {reason}  {amt:,.0f}원")
            if len(sells) > 5:
                lines.append(f"  (외 {len(sells)-5}건)")

        return "\n".join(lines)

    # 보유 포지션 및 잔고 (락 보호)
    with _trading_lock:
        positions = dict(_trading_state["positions"])
        is_sim    = not _trading_state["live"]
        sim_krw   = _trading_state["sim_krw"] if is_sim else None

    pos_count = len(positions)

    # 헤더
    report_dt  = period_end + timedelta(seconds=1)   # 발송 시각 (= period_end + 1s = 당일 09:00)
    weekday_ko = _WEEKDAY_KO[report_dt.weekday()]
    header = (
        f"📋 <b>일일 보고서</b> · {report_dt.month}월 {report_dt.day}일({weekday_ko})\n"
        f"📅 기간: {period_start.month}/{period_start.day} 09:00"
        f" ~ {period_end.month}/{period_end.day} 08:59"
    )

    sep = "━━━━━━━━━━━━━━━"
    sim_html  = _bucket_html(sim_recs)
    live_html = _bucket_html(live_recs)

    pos_section = f"📌 보유 포지션 ({pos_count}종목)"
    if positions:
        now_dt = datetime.now(_KST)
        pos_lines = []
        for ticker, pos in positions.items():
            short = html.escape(ticker).replace("KRW-", "")
            entry_price = float(pos.get("entry_price") or 0)
            held_str = ""
            try:
                entry_dt = _as_kst(datetime.fromisoformat(pos["entry_ts"]))
                delta = now_dt - entry_dt
                h, m = divmod(int(delta.total_seconds()) // 60, 60)
                held_str = f" · {h}h{m:02d}m"
            except Exception:
                pass
            pos_lines.append(f"  · {short}  진입가 {entry_price:,.0f}{held_str}")
        pos_section += "\n" + "\n".join(pos_lines)

    if sim_krw is not None:
        pos_section += f"\n\n💰 가상잔고: {sim_krw:,.0f}원"

    parts = [
        header,
        sep,
        f"🎮 <b>시뮬레이션</b>\n{sim_html}",
        sep,
        f"💰 <b>실거래</b>\n{live_html}",
        sep,
        pos_section,
    ]
    return "\n".join(parts)


def auto_trade_worker():
    while True:
        # 거래 이력 보존 한도 정리 (하루 1회만 실제 수행)
        _maybe_prune_trade_history()

        # 매매 여부와 무관하게 항상 market 데이터 갱신
        try:
            data = build_market_data()
            with _market_lock:
                _market_cache.update(data)
        except Exception as e:
            with _market_lock:
                _market_cache["status"]    = "error"
                _market_cache["error_msg"] = str(e)
            logging.exception("마켓 데이터 갱신 실패")

        with _trading_lock:
            enabled = _trading_state["enabled"]

        if enabled:
            try:
                run_auto_trade()
            except Exception as e:
                with _trading_lock:
                    _trading_state["status_msg"] = f"오류: {str(e)[:80]}"
                logging.exception("자동매매 사이클 오류")

        time.sleep(30)


# 모듈 로드 시 이전 상태 복원 (재시작 시 자동 재개)
_load_state()
# 기동 시 1회 거래 이력 보존 한도 정리 (일자 게이트 설정 겸용)
_maybe_prune_trade_history()
