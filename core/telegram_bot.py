# -*- coding: utf-8 -*-
"""Telegram 명령 봇 — 폰에서 매매 원격 제어

지원 명령:
  /status        현재 상태·잔고·포지션 요약
  /perf          누적 성과 (승률·손익)
  /positions     보유 포지션 상세
  /risk          리스크 상태 확인
  /history [n]   최근 거래 이력 (기본 10, 최대 50)
  /params        현재 파라미터 조회
  /start_sim [금액]  시뮬레이션 매매 시작 (금액 생략 시 입력 유도, '잔고'면 업비트 잔고 사용)
  /start_live    실거래 매매 시작 (→ /confirm 2단계 확인)
  /stop        매매 중지 (포지션 보유 유지)
  /liquidate   매매 중지 + 전액 청산 (→ /confirm 2단계 확인)
  /confirm     위 위험 명령 확인 (60초 이내)
  /help        명령어 목록

보안:
  - .env의 TELEGRAM_CHAT_ID에서 온 메시지만 처리. 다른 사용자는 무시 + 경고 로그.
  - 실거래 시작/청산은 /confirm 2단계 확인 필수 (오터치 방지).
  - 재시작 시 쌓여 있던 이전 명령은 모두 폐기 (오래된 /liquidate 재실행 방지).

동작:
  - getUpdates long polling (30초) 데몬 스레드. 매매 사이클과 완전 분리.
  - 같은 토큰으로 두 곳(로컬 PC + VPS)에서 동시에 돌리면 Telegram이 409를
    반환하므로 한쪽만 켜야 함. 409 감지 시 60초 백오프 후 재시도.
"""

import html
import logging
import threading
import time
from datetime import datetime, timedelta

import requests

from core import config
from core import notifier
from core import trading_control

_KST = config.KST

_API_BASE     = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30                     # long polling 대기 (초)
_REQ_TIMEOUT  = _POLL_TIMEOUT + 10     # HTTP 타임아웃은 폴링 대기보다 길게
_CONFIRM_TTL  = 60                     # /confirm 유효시간 (초)
_CONFLICT_BACKOFF = 60                 # 409 (다른 폴러 존재) 시 대기 (초)

# 위험 명령 2단계 확인 상태 (단일 사용자 전제이므로 모듈 전역으로 충분)
_pending = {"action": None, "expires": None}

# 시뮬 가상자산 입력 대기 상태 (/start_sim 을 인자 없이 보냈을 때)
_pending_budget = {"active": False, "expires": None}

_HELP_TEXT = (
    "<b>명령어 목록</b>\n"
    "/status — 상태·잔고·포지션 요약\n"
    "/perf — 누적 성과 (승률·손익)\n"
    "/positions — 보유 포지션 상세\n"
    "/risk — 리스크 상태 확인\n"
    "/history [n] — 최근 거래 이력 (기본 10, 최대 50)\n"
    "/params — 현재 파라미터 조회\n"
    "/start_sim [금액] — 시뮬레이션 시작 (금액 생략 시 입력 유도, '잔고'면 업비트 잔고)\n"
    "/start_live — 실거래 시작 (확인 필요)\n"
    "/stop — 매매 중지 (보유 유지)\n"
    "/liquidate — 중지 + 전액 청산 (확인 필요)\n"
    "/confirm — 위험 명령 확인 (60초 이내)\n"
    "/universe — 코인 유니버스 (스캔 목록·점수)\n"
    "/help — 이 도움말"
)

# Reply Keyboard 버튼 텍스트 → 명령어 매핑
_BUTTON_MAP = {
    "📊 상태":   "/status",
    "💰 성과":   "/perf",
    "📋 포지션": "/positions",
    "⚠️ 리스크": "/risk",
    "▶ 시뮬":   "/start_sim",
    "⏹ 중지":   "/stop",
    "📜 이력":   "/history",
    "🔍 유니버스": "/universe",
    "❓ 도움말": "/help",
}

_REPLY_KEYBOARD = {
    "keyboard": [
        ["📊 상태", "💰 성과", "📋 포지션", "⚠️ 리스크"],
        ["▶ 시뮬", "⏹ 중지", "📜 이력", "❓ 도움말"],
        ["🔍 유니버스"],
    ],
    "resize_keyboard": True,
    "persistent": True,
}

# setMyCommands 등록 목록
_BOT_COMMANDS = [
    {"command": "status",     "description": "상태·잔고·포지션 요약"},
    {"command": "perf",       "description": "누적 성과 (승률·손익)"},
    {"command": "positions",  "description": "보유 포지션 상세"},
    {"command": "risk",       "description": "리스크 상태 확인"},
    {"command": "history",    "description": "최근 거래 이력"},
    {"command": "params",     "description": "현재 파라미터 조회"},
    {"command": "start_sim",  "description": "시뮬레이션 시작"},
    {"command": "start_live", "description": "실거래 시작 (확인 필요)"},
    {"command": "stop",       "description": "매매 중지 (보유 유지)"},
    {"command": "liquidate",  "description": "전액 청산 (확인 필요)"},
    {"command": "universe",    "description": "코인 유니버스 (스캔 목록·점수)"},
    {"command": "help",            "description": "도움말"},
]


def _parse_budget(text: str):
    """문자열을 시뮬 가상예산으로 파싱.
    반환: (ok: bool, budget: float|None, err: str|None)
    빈값·'0'·'잔고'·'balance'·'기본' → budget=None (업비트 잔고 사용)."""
    t = (text or "").strip().lower()
    for token in (",", "원", "krw", "kr", " "):
        t = t.replace(token, "")
    if t in ("", "0", "잔고", "balance", "기본", "default"):
        return True, None, None
    try:
        v = float(t)
    except ValueError:
        return False, None, "금액이 올바르지 않습니다. 예: /start_sim 500000  (또는 '잔고')"
    if v <= 0:
        return False, None, "가상 자산은 0보다 커야 합니다."
    return True, v, None


def _start_sim(budget):
    """시뮬레이션 시작 + 실패 시 안내. 성공 알림은 start_trading 내부 notify_event가 처리."""
    result = trading_control.start_trading(live_mode=False, sim_budget=budget)
    if not result.get("ok"):
        notifier.send(f"⚠️ {html.escape(result.get('error', '시작 실패'))}")


def _send_message(text: str, reply_markup=None):
    """sendMessage 직접 호출 (reply_markup 지원용)."""
    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            logging.warning("Telegram sendMessage 실패 (%d): %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logging.warning("Telegram sendMessage 오류: %s", e)


def _send_with_keyboard(text: str):
    """Reply Keyboard 포함 메시지 전송."""
    _send_message(text, reply_markup=_REPLY_KEYBOARD)


def _register_commands():
    """봇 기동 시 1회 setMyCommands 호출로 BotFather 명령어 목록 자동 등록."""
    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method="setMyCommands")
    try:
        resp = requests.post(url, json={"commands": _BOT_COMMANDS}, timeout=10)
        if resp.status_code != 200:
            logging.warning("Telegram setMyCommands 실패 (%d): %s", resp.status_code, resp.text[:200])
        else:
            logging.info("Telegram setMyCommands 등록 완료 (%d개)", len(_BOT_COMMANDS))
    except Exception as e:
        logging.warning("Telegram setMyCommands 오류: %s", e)


def _get_updates(offset):
    """getUpdates long polling. 반환: (updates 리스트 | None, 새 offset)"""
    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates")
    params = {"timeout": _POLL_TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    resp = requests.post(url, json=params, timeout=_REQ_TIMEOUT)
    if resp.status_code == 409:
        # 같은 토큰으로 다른 곳에서 getUpdates 중 (로컬 PC + VPS 동시 구동)
        logging.warning("Telegram getUpdates 409 충돌 — 다른 인스턴스가 폴링 중. %d초 대기", _CONFLICT_BACKOFF)
        time.sleep(_CONFLICT_BACKOFF)
        return None, offset
    if resp.status_code != 200:
        logging.warning("Telegram getUpdates 실패 (%d): %s", resp.status_code, resp.text[:200])
        time.sleep(5)
        return None, offset
    updates = resp.json().get("result", [])
    if updates:
        offset = updates[-1]["update_id"] + 1
    return updates, offset


def _drain_old_updates():
    """기동 시 쌓여 있던 이전 업데이트 전부 폐기.
    재시작 직후 오래된 /liquidate 등이 재실행되는 사고 방지."""
    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates")
    try:
        resp = requests.post(url, json={"timeout": 0, "offset": -1}, timeout=10)
        if resp.status_code == 200:
            updates = resp.json().get("result", [])
            if updates:
                return updates[-1]["update_id"] + 1
    except Exception as e:
        logging.warning("Telegram 이전 업데이트 폐기 실패: %s", e)
    return None


def _set_pending(action: str):
    _pending["action"]  = action
    _pending["expires"] = datetime.now(_KST) + timedelta(seconds=_CONFIRM_TTL)


def _take_pending() -> str | None:
    action, expires = _pending["action"], _pending["expires"]
    _pending["action"] = _pending["expires"] = None
    if action and expires and datetime.now(_KST) <= expires:
        return action
    return None


# ─────────────────────────────────────────────
# 신규 조회 명령 구현
# ─────────────────────────────────────────────

def _cmd_perf() -> str:
    from core.trader import compute_performance
    p = compute_performance()

    def _fmt(label: str, b: dict) -> str:
        if b["n"] == 0:
            return f"<b>{label}</b>: 거래 없음"
        sign = "+" if b["realized_krw"] >= 0 else ""
        return (
            f"<b>{label}</b>\n"
            f"  거래: {b['n']}건 · 승률: {b['win_rate']}%\n"
            f"  실현손익: {sign}{b['realized_krw']:,.0f}원\n"
            f"  평균수익률: {b['avg_profit_pct']:+.2f}%\n"
            f"  최고: {b['best_pct']:+.2f}% · 최악: {b['worst_pct']:+.2f}%"
        )

    parts = []
    if p["live"]["n"] == 0 and p["sim"]["n"] == 0:
        return "아직 거래 이력 없음"
    if p["live"]["n"] > 0:
        parts.append(_fmt("실거래", p["live"]))
    if p["sim"]["n"] > 0:
        parts.append(_fmt("시뮬레이션", p["sim"]))
    return "\n\n".join(parts)


def _cmd_positions() -> str:
    from core.trader import _trading_state, _trading_lock, FEE_RATE
    from core.upbit_client import UpbitClient

    with _trading_lock:
        positions = dict(_trading_state["positions"])

    if not positions:
        return "보유 포지션 없음"

    client = UpbitClient()
    now_dt = datetime.now(_KST)
    lines = [f"<b>보유 포지션 ({len(positions)}개)</b>"]

    for ticker, pos in positions.items():
        entry_price = pos.get("entry_price", 0)
        peak_price  = pos.get("peak_price", entry_price)

        # 현재가 API 조회 (포지션별 1회)
        try:
            current_price = client.get_current_price(ticker, warn=False) or 0
        except Exception:
            current_price = 0

        if current_price > 0 and entry_price > 0:
            pct = (current_price - entry_price) / entry_price * 100
            emoji = "📈" if pct >= 0 else "📉"
            profit_str = f"{pct:+.2f}%"
        else:
            emoji = "•"
            profit_str = "조회 불가"

        # 보유 시간
        held_str = ""
        entry_ts = pos.get("entry_ts")
        if entry_ts:
            try:
                from core.trader import _as_kst
                entry_dt = _as_kst(datetime.fromisoformat(entry_ts))
                delta = now_dt - entry_dt
                h, m = divmod(int(delta.total_seconds() // 60), 60)
                held_str = f" · {h}h{m:02d}m"
            except Exception:
                pass

        ticker_safe = html.escape(ticker)
        lines.append(
            f"{emoji} <b>{ticker_safe}</b> {profit_str}{held_str}\n"
            f"   진입 {entry_price:,.0f} / 현재 {current_price:,.0f} / 고점 {peak_price:,.0f}"
        )

    return "\n".join(lines)


def _cmd_risk() -> str:
    from core.trader import get_risk_snapshot
    r = get_risk_snapshot()

    lines = ["<b>리스크 상태</b>"]

    daily_krw = r.get("daily_realized_krw", 0)
    sign = "+" if daily_krw >= 0 else ""
    lines.append(f"당일 실현손익: {sign}{daily_krw:,.0f}원")

    blocked = r.get("daily_blocked", False)
    lines.append(f"일일 차단: {'🛑 차단 중' if blocked else '정상'}")

    consec = r.get("consec_stoploss", 0)
    lines.append(f"연속 손절: {consec}회")

    bbu = r.get("buy_block_until")
    if bbu:
        lines.append(f"쿨다운 해제: {bbu[11:16]}")

    reason = r.get("buy_block_reason")
    if reason:
        lines.append(f"매수 차단 사유: {html.escape(str(reason))}")

    age = r.get("market_age_sec")
    if age is None:
        lines.append("마켓 데이터: 미확인")
    elif age > config.MARKET_STALE_SEC:
        lines.append(f"마켓 데이터: ⚠️ 노후 ({age:.0f}초)")
    else:
        lines.append(f"마켓 데이터: 신선 ({age:.0f}초)")

    return "\n".join(lines)


def _cmd_history(n: int) -> str:
    from core.trader import _trading_state, _trading_lock

    with _trading_lock:
        log = list(_trading_state["log"])

    if not log:
        return "거래 이력 없음"

    entries = log[:n]
    lines = [f"<b>최근 거래 이력 ({len(entries)}건)</b>"]
    _TYPE_KO = {
        "buy":       "매수",
        "sell":      "매도",
        "buy_fail":  "매수실패",
        "sell_fail": "매도실패",
        "hold":      "관망",
    }
    for e in entries:
        date    = e.get("date", "")
        t       = e.get("time", "")
        etype   = _TYPE_KO.get(e.get("type", ""), e.get("type", ""))
        ticker  = html.escape(str(e.get("ticker", "-")))
        reason  = html.escape(str(e.get("reason", "")))
        pct     = e.get("profit_pct")
        pct_str = f" {pct:+.2f}%" if pct is not None else ""
        lines.append(f"{date} {t} [{etype}] {ticker}{pct_str} {reason}")

    return "\n".join(lines)


def _cmd_params() -> str:
    lines = ["<b>현재 파라미터</b>"]
    lines.append(f"BUY_SCORE_THRESHOLD: {config.BUY_SCORE_THRESHOLD}")
    lines.append(f"MAX_LOSS_PERCENT: {config.MAX_LOSS_PERCENT}%")
    lines.append(f"TAKE_PROFIT_PERCENT: {config.TAKE_PROFIT_PERCENT}%")
    lines.append(f"TRAILING_START_PCT: {config.TRAILING_START_PCT}%")
    lines.append(f"TRAILING_STOP_PCT: {config.TRAILING_STOP_PCT}%")
    lines.append(f"MAX_HOLD_HOURS: {config.MAX_HOLD_HOURS}h")
    lines.append(f"DAILY_LOSS_LIMIT_PCT: {config.DAILY_LOSS_LIMIT_PCT}%")
    lines.append(f"MAX_POSITIONS: {config.MAX_POSITIONS}")
    lines.append(f"MARKET_REGIME_FILTER: {config.MARKET_REGIME_FILTER}")
    return "\n".join(lines)


def _cmd_universe() -> str:
    from core.data_builder import _market_cache, _market_lock
    with _market_lock:
        coins = list(_market_cache.get("coins", []))
        updated_at = _market_cache.get("updated_at")

    if not coins:
        return "마켓 데이터 아직 없음 (초기화 중)"

    threshold = config.BUY_SCORE_THRESHOLD
    hdr = f"<b>🔍 코인 유니버스</b> ({len(coins)}개 스캔)"
    if updated_at:
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).astimezone(_KST)
            hdr += f" · {dt.strftime('%H:%M')} 갱신"
        except Exception:
            pass

    above = [c for c in coins if c["total_score"] >= threshold]
    below = [c for c in coins if c["total_score"] < threshold]

    lines = [hdr]
    if above:
        lines.append(f"\n<b>✅ 진입 가능 ({len(above)}개)</b>")
        for c in above[:15]:
            lines.append(f"  {html.escape(c['coin'])} {c['total_score']:.1f}점 — {html.escape(c['action_text'])}")
    lines.append(f"\n<b>⬇ 기준 미달 상위 10개 (임계치 {threshold}점)</b>")
    for c in below[:10]:
        lines.append(f"  {html.escape(c['coin'])} {c['total_score']:.1f}점")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 명령 디스패치
# ─────────────────────────────────────────────

def _handle_command(cmd: str, arg: str | None = None):
    """명령 처리. 응답은 notifier 큐 또는 직접 sendMessage로 전송 (HTML 모드)."""
    reply = notifier.send

    if cmd in ("/start", "/help"):
        _send_with_keyboard(_HELP_TEXT)

    elif cmd == "/status":
        try:
            reply(trading_control.status_summary())
        except Exception as e:
            logging.exception("Telegram /status 처리 오류")
            reply(f"⚠️ 상태 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/perf":
        try:
            reply(_cmd_perf())
        except Exception as e:
            logging.exception("Telegram /perf 처리 오류")
            reply(f"⚠️ 성과 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/positions":
        try:
            reply(_cmd_positions())
        except Exception as e:
            logging.exception("Telegram /positions 처리 오류")
            reply(f"⚠️ 포지션 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/risk":
        try:
            reply(_cmd_risk())
        except Exception as e:
            logging.exception("Telegram /risk 처리 오류")
            reply(f"⚠️ 리스크 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/history":
        try:
            n = 10
            if arg:
                try:
                    n = max(1, min(50, int(arg.strip())))
                except ValueError:
                    pass
            reply(_cmd_history(n))
        except Exception as e:
            logging.exception("Telegram /history 처리 오류")
            reply(f"⚠️ 이력 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/params":
        try:
            reply(_cmd_params())
        except Exception as e:
            logging.exception("Telegram /params 처리 오류")
            reply(f"⚠️ 파라미터 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/start_sim":
        from core.trader import _trading_state, _trading_lock
        with _trading_lock:
            _enabled = _trading_state["enabled"]
            _cur_mode = "실거래" if _trading_state["live"] else "시뮬레이션"
        if _enabled:
            reply(f"▶ 현재 <b>{_cur_mode}</b> 매매가 실행 중입니다.\n"
                  "중지하려면 /stop 을 누르세요.")
        elif arg:
            ok, budget, err = _parse_budget(arg)
            if not ok:
                reply(f"⚠️ {err}")
            else:
                _start_sim(budget)
        else:
            _pending_budget["active"]  = True
            _pending_budget["expires"] = datetime.now(_KST) + timedelta(seconds=_CONFIRM_TTL)
            reply("💰 <b>시뮬레이션 가상 자산 입력</b>\n"
                  "예산(원)을 숫자로 보내주세요. 예: <code>500000</code>\n"
                  "내 업비트 잔고로 시작하려면 <code>잔고</code> 라고 입력하세요. "
                  f"({_CONFIRM_TTL}초 이내)")

    elif cmd == "/start_live":
        from core.trader import _trading_state, _trading_lock
        with _trading_lock:
            _enabled = _trading_state["enabled"]
            _cur_mode = "실거래" if _trading_state["live"] else "시뮬레이션"
        if _enabled:
            reply(f"▶ 현재 <b>{_cur_mode}</b> 매매가 실행 중입니다.\n"
                  "중지하려면 /stop 을 누르세요.")
        else:
            _set_pending("start_live")
            reply("⚠️ <b>실거래 시작 확인</b>\n실제 계좌에서 매수·매도가 실행됩니다.\n"
                  f"계속하려면 {_CONFIRM_TTL}초 내에 /confirm 을 입력하세요.")

    elif cmd == "/stop":
        result = trading_control.stop_trading(mode="hold")
        if not result.get("ok"):
            reply(f"⚠️ {html.escape(result.get('error', '중지 실패'))}")

    elif cmd == "/liquidate":
        _set_pending("liquidate")
        reply("⚠️ <b>전액 청산 확인</b>\n모든 보유 포지션을 시장가로 즉시 매도합니다.\n"
              f"계속하려면 {_CONFIRM_TTL}초 내에 /confirm 을 입력하세요.")

    elif cmd == "/confirm":
        action = _take_pending()
        if action is None:
            reply("확인 대기 중인 명령이 없거나 시간이 만료됐습니다.")
        elif action == "start_live":
            result = trading_control.start_trading(live_mode=True)
            if not result.get("ok"):
                reply(f"⚠️ {html.escape(result.get('error', '시작 실패'))}")
        elif action == "liquidate":
            result = trading_control.stop_trading(mode="liquidate")
            if not result.get("ok"):
                reply(f"⚠️ {html.escape(result.get('error', '청산 실패'))}")

    elif cmd == "/universe":
        try:
            reply(_cmd_universe())
        except Exception as e:
            logging.exception("Telegram /universe 처리 오류")
            reply(f"⚠️ 유니버스 조회 오류: {html.escape(str(e)[:100])}")

    else:
        reply(f"알 수 없는 명령: {html.escape(cmd)}\n/help 로 명령어를 확인하세요.")


def telegram_worker():
    """Telegram 명령 폴링 워커 (데몬 스레드). 미설정 시 즉시 종료."""
    if not notifier.is_configured():
        logging.info("Telegram 미설정 — 명령 봇 비활성")
        return

    _register_commands()
    offset = _drain_old_updates()
    logging.info("Telegram 명령 봇 시작 (chat_id=%s)", config.TELEGRAM_CHAT_ID)

    # 봇 기동 시 Reply Keyboard 노출
    _send_with_keyboard("🤖 봇이 시작됐습니다. /help 로 명령어를 확인하세요.")

    while True:
        try:
            updates, offset = _get_updates(offset)
            if not updates:
                continue
            for upd in updates:
                msg = upd.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text    = (msg.get("text") or "").strip()
                if not text:
                    continue
                # 핵심 보안: 등록된 chat_id 외의 명령은 전부 무시
                if chat_id != str(config.TELEGRAM_CHAT_ID):
                    logging.warning("Telegram 미등록 chat_id(%s)의 명령 무시: %s", chat_id, text[:50])
                    continue

                # Reply Keyboard 버튼 텍스트 → 명령어로 변환
                if text in _BUTTON_MAP:
                    text = _BUTTON_MAP[text]

                # 가상자산 입력 대기 중 + 명령이 아닌 메시지 → 예산 입력으로 처리
                if _pending_budget["active"] and not text.startswith("/"):
                    _pending_budget["active"] = False
                    if _pending_budget["expires"] and datetime.now(_KST) > _pending_budget["expires"]:
                        notifier.send("⏰ 가상 자산 입력 시간이 만료됐습니다. /start_sim 으로 다시 시작하세요.")
                        continue
                    ok, budget, err = _parse_budget(text)
                    if not ok:
                        notifier.send(f"⚠️ {err}")
                    else:
                        _start_sim(budget)
                    continue

                parts = text.split(maxsplit=1)
                cmd   = parts[0].split("@")[0].lower()        # "/status@MyBot arg" → "/status"
                arg   = parts[1].strip() if len(parts) > 1 else None
                # 새 명령이 들어오면 대기 중이던 가상자산 입력은 취소
                _pending_budget["active"] = False
                logging.info("Telegram 명령 수신: %s", cmd)
                _handle_command(cmd, arg)
        except requests.exceptions.Timeout:
            continue   # long polling 타임아웃은 정상
        except Exception as e:
            logging.warning("Telegram 폴링 오류: %s — 10초 후 재시도", e)
            time.sleep(10)


def _daily_report_worker():
    """매일 09:00 KST 일일 보고서 전송 데몬."""
    while True:
        now = datetime.now(_KST)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        time.sleep(max(0, (target - now).total_seconds()))

        # 기간: 전일 09:00 ~ 당일 08:59:59
        period_end   = target - timedelta(seconds=1)
        period_start = target - timedelta(days=1)

        try:
            from core.trader import build_daily_report
            msg = build_daily_report(period_start, period_end)
            _send_message(msg)
        except Exception:
            logging.exception("일일 보고서 생성/전송 오류")


# ─────────────────────────────────────────────
# 주식 봇 전용 Telegram 워커
# ─────────────────────────────────────────────

_STOCK_HELP_TEXT = (
    "<b>[미국주식 봇] 명령어</b>\n\n"
    "/us_start_sim [금액] — 미국주식 시뮬 시작 (원화, 생략 시 기본값 100만원)\n"
    "/us_stop — 미국주식 시뮬 중지\n"
    "/us_status — 미국주식 현황·잔고·포지션\n"
    "/us_perf — 누적 성과 (승률·평균수익률)\n"
    "/us_history [n] — 최근 거래 이력 (기본 10, 최대 50)\n"
    "/us_params — 현재 파라미터 조회\n"
    "/us_universe — 현재 유니버스 종목 리스트\n"
    "/us_market — 미국장 시간 상태\n"
    "/help — 이 도움말"
)

_STOCK_BOT_COMMANDS = [
    # ── 국내주식 명령 비활성 ──
    # {"command": "status",       "description": "시뮬 상태·잔고·포지션 요약"},
    # {"command": "perf",         "description": "누적 성과 (승률·손익)"},
    # {"command": "positions",    "description": "보유 포지션 상세"},
    # {"command": "history",      "description": "최근 거래 이력 [n]"},
    # {"command": "params",       "description": "현재 파라미터 조회"},
    # {"command": "market",       "description": "장중/장외 상태 확인"},
    # {"command": "start_sim",    "description": "주식 시뮬 시작 [예산]"},
    # {"command": "start_live",   "description": "실거래 시작 (미지원)"},
    # {"command": "stop",         "description": "주식 시뮬 중지"},
    {"command": "us_start_sim", "description": "미국주식 시뮬 시작 [원화예산]"},
    {"command": "us_stop",      "description": "미국주식 시뮬 중지"},
    {"command": "us_status",    "description": "미국주식 현황·잔고·포지션"},
    {"command": "us_perf",      "description": "누적 성과 (승률·평균수익률)"},
    {"command": "us_history",   "description": "최근 거래 이력 [n]"},
    {"command": "us_params",    "description": "현재 파라미터 조회"},
    {"command": "us_universe",  "description": "유니버스 종목 리스트"},
    {"command": "us_market",    "description": "미국장 시간 상태"},
    {"command": "help",         "description": "도움말"},
]

_STOCK_REPLY_KEYBOARD = {
    "keyboard": [
        ["🇺🇸 시작", "⏹ 중지", "📊 현황"],
        ["📈 성과", "📋 이력", "⚙️ 파라미터"],
        ["🌐 유니버스", "🕐 장 상태", "❓ 도움말"],
    ],
    "resize_keyboard": True,
    "persistent": True,
}

_STOCK_BUTTON_MAP = {
    "🇺🇸 시작":    "/us_start_sim",
    "⏹ 중지":      "/us_stop",
    "📊 현황":      "/us_status",
    "📈 성과":      "/us_perf",
    "📋 이력":      "/us_history",
    "⚙️ 파라미터":  "/us_params",
    "🌐 유니버스":  "/us_universe",
    "🕐 장 상태":   "/us_market",
    "❓ 도움말":    "/help",
}


def _send_stock_message(text: str):
    """주식 봇 토큰으로 직접 sendMessage."""
    url = _API_BASE.format(token=config.TELEGRAM_STOCK_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            logging.warning("Telegram(주식) sendMessage 실패 (%d): %s",
                            resp.status_code, resp.text[:200])
    except Exception as e:
        logging.warning("Telegram(주식) sendMessage 오류: %s", e)


def _send_stock_with_keyboard(text: str):
    """Reply Keyboard 포함 주식 봇 메시지 전송."""
    url = _API_BASE.format(token=config.TELEGRAM_STOCK_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _STOCK_REPLY_KEYBOARD,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            logging.warning("Telegram(주식) sendMessage 실패 (%d): %s",
                            resp.status_code, resp.text[:200])
    except Exception as e:
        logging.warning("Telegram(주식) sendMessage 오류: %s", e)


def _register_stock_commands():
    url = _API_BASE.format(token=config.TELEGRAM_STOCK_BOT_TOKEN, method="setMyCommands")
    try:
        resp = requests.post(url, json={"commands": _STOCK_BOT_COMMANDS}, timeout=10)
        if resp.status_code != 200:
            logging.warning("Telegram(주식) setMyCommands 실패 (%d): %s",
                            resp.status_code, resp.text[:200])
        else:
            logging.info("Telegram(주식) setMyCommands 등록 완료 (%d개)", len(_STOCK_BOT_COMMANDS))
    except Exception as e:
        logging.warning("Telegram(주식) setMyCommands 오류: %s", e)


def _get_stock_updates(offset, token: str):
    """주식 봇 전용 getUpdates."""
    url = _API_BASE.format(token=token, method="getUpdates")
    params = {"timeout": _POLL_TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.post(url, json=params, timeout=_REQ_TIMEOUT)
    except requests.exceptions.Timeout:
        return None, offset
    if resp.status_code == 409:
        logging.warning("Telegram(주식) getUpdates 409 충돌 — %d초 대기", _CONFLICT_BACKOFF)
        time.sleep(_CONFLICT_BACKOFF)
        return None, offset
    if resp.status_code != 200:
        logging.warning("Telegram(주식) getUpdates 실패 (%d): %s",
                        resp.status_code, resp.text[:200])
        time.sleep(5)
        return None, offset
    updates = resp.json().get("result", [])
    if updates:
        offset = updates[-1]["update_id"] + 1
    return updates, offset


def _drain_stock_updates(token: str):
    url = _API_BASE.format(token=token, method="getUpdates")
    try:
        resp = requests.post(url, json={"timeout": 0, "offset": -1}, timeout=10)
        if resp.status_code == 200:
            updates = resp.json().get("result", [])
            if updates:
                return updates[-1]["update_id"] + 1
    except Exception as e:
        logging.warning("Telegram(주식) 이전 업데이트 폐기 실패: %s", e)
    return None


def _stock_cmd_perf() -> str:
    import json as _json
    import os as _os
    log_file = _os.path.join("data", "stock_trade_history.jsonl")
    total, wins, rets = 0, 0, []
    if _os.path.exists(log_file):
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
                total += 1
                rets.append(r)
                if r > 0:
                    wins += 1
    if total == 0:
        return "아직 청산 거래 이력이 없습니다."
    win_rate = wins / total * 100
    avg_ret  = sum(rets) / len(rets)
    best     = max(rets)
    worst    = min(rets)
    lines = [
        "<b>주식 시뮬 누적 성과</b>",
        f"거래: {total}건 · 승률: {win_rate:.0f}%",
        f"평균수익률: {avg_ret:+.2f}%",
        f"최고: {best:+.2f}% · 최악: {worst:+.2f}%",
    ]
    return "\n".join(lines)


def _stock_cmd_positions() -> str:
    from core.stock.trader import _stock_positions, _stock_lock, _trading_days_held
    from core.stock.kis_client import KisClient
    from core import config as _cfg
    with _stock_lock:
        positions = dict(_stock_positions)
    if not positions:
        return "보유 포지션 없음"
    client = KisClient()
    lines = [f"<b>보유 포지션 ({len(positions)}종목)</b>"]
    for code, pos in positions.items():
        name = html.escape(pos.get("name", code))
        entry_price = pos.get("entry_price", 0)
        entry_time  = pos.get("entry_time")
        try:
            current = client.get_current_price(code)
        except Exception:
            current = entry_price
        if current and entry_price:
            fee = _cfg.STOCK_FEE_RATE
            pct = (current * (1 - fee) - entry_price * (1 + fee)) / (entry_price * (1 + fee)) * 100
            emoji = "📈" if pct >= 0 else "📉"
            pct_str = f"{pct:+.2f}%"
        else:
            emoji = "•"
            pct_str = "조회 불가"
        held = _trading_days_held(entry_time) if entry_time else 0
        lines.append(
            f"{emoji} <b>{name}</b>({html.escape(code)}) {pct_str} · {held}일차\n"
            f"   진입 {entry_price:,.0f}원 / 현재 {current:,.0f}원 / {pos.get('quantity', 0)}주"
        )
    return "\n".join(lines)


def _stock_cmd_history(n: int) -> str:
    import json as _json
    import os as _os
    log_file = _os.path.join("data", "stock_trade_history.jsonl")
    if not _os.path.exists(log_file):
        return "거래 이력 없음"
    records = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(_json.loads(line))
                except Exception:
                    pass
    if not records:
        return "거래 이력 없음"
    recent = records[-n:][::-1]
    lines = [f"<b>최근 거래 이력 ({len(recent)}건)</b>"]
    for rec in recent:
        side = "매수" if rec.get("side") == "buy" else "매도"
        name = html.escape(rec.get("name", rec.get("code", "?")))
        ts   = rec.get("ts", "")[:16].replace("T", " ")
        r    = rec.get("ret_pct")
        pct_str = f" {r:+.2f}%" if r is not None else ""
        reason = html.escape(rec.get("reason", ""))
        lines.append(f"{ts} [{side}] {name}{pct_str} {reason}")
    return "\n".join(lines)


def _stock_cmd_params() -> str:
    from core import config as _cfg
    lines = [
        "<b>주식 봇 파라미터</b>",
        f"STOCK_BUY_SCORE_THRESHOLD: {_cfg.STOCK_BUY_SCORE_THRESHOLD}",
        f"STOCK_MAX_LOSS_PERCENT: {_cfg.STOCK_MAX_LOSS_PERCENT}%",
        f"STOCK_TAKE_PROFIT_PERCENT: {_cfg.STOCK_TAKE_PROFIT_PERCENT}%",
        f"STOCK_TRAILING_START_PCT: {_cfg.STOCK_TRAILING_START_PCT}%",
        f"STOCK_TRAILING_STOP_PCT: {_cfg.STOCK_TRAILING_STOP_PCT}%",
        f"STOCK_MAX_HOLD_DAYS: {_cfg.STOCK_MAX_HOLD_DAYS}일",
        f"STOCK_MAX_POSITIONS: {_cfg.STOCK_MAX_POSITIONS}",
        f"STOCK_MAX_PRICE: {_cfg.STOCK_MAX_PRICE:,}원",
        f"STOCK_BUY_CLOSE_TIME: {_cfg.STOCK_BUY_CLOSE_TIME}",
    ]
    return "\n".join(lines)


def _stock_cmd_market() -> str:
    from core.stock.trader import is_market_hours
    now = datetime.now(_KST)
    weekday_ko = ["월", "화", "수", "목", "금", "토", "일"]
    dow = weekday_ko[now.weekday()]
    time_str = now.strftime("%H:%M")
    if is_market_hours():
        close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        remain = close - now
        h, m = divmod(int(remain.total_seconds() // 60), 60)
        return (
            f"<b>🟢 장중</b> — {now.strftime('%m/%d')}({dow}) {time_str}\n"
            f"마감까지 {h}시간 {m}분"
        )
    else:
        nxt = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= nxt:
            nxt += timedelta(days=1)
        while nxt.weekday() > 4:
            nxt += timedelta(days=1)
        remain = nxt - now
        h, m = divmod(int(remain.total_seconds() // 60), 60)
        nxt_dow = weekday_ko[nxt.weekday()]
        return (
            f"<b>🔴 장 외</b> — {now.strftime('%m/%d')}({dow}) {time_str}\n"
            f"다음 개장: {nxt.strftime('%m/%d')}({nxt_dow}) 09:00 "
            f"({h}시간 {m}분 후)"
        )


def _us_cmd_perf() -> str:
    import json as _json
    import os as _os
    log_file = _os.path.join("data", "us_stock_trade_history.jsonl")
    total, wins, rets = 0, 0, []
    if _os.path.exists(log_file):
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
                total += 1
                rets.append(r)
                if r > 0:
                    wins += 1
    if total == 0:
        return "아직 청산 거래 이력이 없습니다."
    win_rate = wins / total * 100
    avg_ret  = sum(rets) / len(rets)
    return "\n".join([
        "<b>🇺🇸 미국주식 시뮬 누적 성과</b>",
        f"거래: {total}건 · 승률: {win_rate:.0f}%",
        f"평균수익률: {avg_ret:+.2f}%",
        f"최고: {max(rets):+.2f}% · 최악: {min(rets):+.2f}%",
    ])


def _us_cmd_history(n: int) -> str:
    import json as _json
    import os as _os
    log_file = _os.path.join("data", "us_stock_trade_history.jsonl")
    if not _os.path.exists(log_file):
        return "거래 이력 없음"
    records = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(_json.loads(line))
                except Exception:
                    pass
    if not records:
        return "거래 이력 없음"
    recent = records[-n:][::-1]
    lines = [f"<b>🇺🇸 최근 거래 이력 ({len(recent)}건)</b>"]
    for rec in recent:
        side   = "매수" if rec.get("side") == "buy" else "매도"
        name   = html.escape(rec.get("name", rec.get("symbol", "?")))
        symbol = html.escape(rec.get("symbol", ""))
        ts     = rec.get("ts", "")[:16].replace("T", " ")
        r      = rec.get("ret_pct")
        pct_str = f" {r:+.2f}%" if r is not None else ""
        price  = rec.get("price", "")
        p_str  = f" ${price}" if price else ""
        lines.append(f"{ts} [{side}] {name}({symbol}){pct_str}{p_str}")
    return "\n".join(lines)


def _us_cmd_params() -> str:
    from core import config as _cfg
    return "\n".join([
        "<b>🇺🇸 미국주식 파라미터</b>",
        f"US_BUY_SCORE_THRESHOLD: {_cfg.US_BUY_SCORE_THRESHOLD}",
        f"US_MAX_POSITIONS: {_cfg.US_MAX_POSITIONS}",
        f"US_SIM_BUDGET: ₩{_cfg.US_SIM_BUDGET:,.0f}",
        f"US_MAX_LOSS_PERCENT: {_cfg.US_MAX_LOSS_PERCENT}%",
        f"US_TAKE_PROFIT_PERCENT: {_cfg.US_TAKE_PROFIT_PERCENT}%",
        f"US_TRAILING_START_PCT: {_cfg.US_TRAILING_START_PCT}%",
        f"US_TRAILING_STOP_PCT: {_cfg.US_TRAILING_STOP_PCT}%",
        f"US_MAX_HOLD_DAYS: {_cfg.US_MAX_HOLD_DAYS}일",
        f"US_FEE_RATE: {_cfg.US_FEE_RATE * 100:.2f}%",
        f"진입등락폭: {_cfg.US_ENTRY_CHANGE_MIN}% ~ {_cfg.US_ENTRY_CHANGE_MAX}%",
    ])


def _us_cmd_universe() -> str:
    import core.stock.us_universe as _usu
    with _usu._lock:
        universe  = list(_usu._cached_universe) if _usu._cached_universe else list(_usu._FALLBACK_UNIVERSE)
        cache_date = _usu._cache_date

    if not universe:
        return "유니버스 데이터 없음"

    if cache_date:
        d = cache_date
        source = f"📅 {d[:4]}-{d[4:6]}-{d[6:]} KIS 거래량순위 기준"
    else:
        source = "⚠️ fallback 고정 리스트 (아직 갱신 전)"

    nas = [(sym, name) for sym, name, excd in universe if excd == "NAS"]
    nys = [(sym, name) for sym, name, excd in universe if excd == "NYS"]
    lines = [f"<b>🌐 미국주식 유니버스</b> ({len(universe)}종목)\n{source}"]
    if nas:
        lines.append(f"\n<b>NASDAQ ({len(nas)})</b>")
        for sym, name in nas[:25]:
            lines.append(f"  {html.escape(sym)} — {html.escape(name)}")
    if nys:
        lines.append(f"\n<b>NYSE ({len(nys)})</b>")
        for sym, name in nys[:25]:
            lines.append(f"  {html.escape(sym)} — {html.escape(name)}")
    return "\n".join(lines)


def _us_cmd_market() -> str:
    from core.stock.us_trader import is_us_market_hours
    now = datetime.now(_KST)
    weekday_ko = ["월", "화", "수", "목", "금", "토", "일"]
    dow  = weekday_ko[now.weekday()]
    ts   = now.strftime("%H:%M")
    if is_us_market_hours():
        # 마감 KST 05:00 계산
        close = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if now.hour >= 5:
            close += timedelta(days=1)
        remain = close - now
        h, m = divmod(int(remain.total_seconds() // 60), 60)
        return (
            f"<b>🟢 미국장 중</b> — {now.strftime('%m/%d')}({dow}) {ts}\n"
            f"마감(KST 05:00)까지 {h}시간 {m}분"
        )
    # 다음 개장 KST 22:30 계산
    nxt = now.replace(hour=22, minute=30, second=0, microsecond=0)
    if now >= nxt:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    remain = nxt - now
    h, m = divmod(int(remain.total_seconds() // 60), 60)
    nxt_dow = weekday_ko[nxt.weekday()]
    return (
        f"<b>🔴 미국장 외</b> — {now.strftime('%m/%d')}({dow}) {ts}\n"
        f"다음 개장: {nxt.strftime('%m/%d')}({nxt_dow}) 22:30 ({h}시간 {m}분 후)"
    )


def _handle_stock_command(cmd: str, arg: str | None = None):
    """주식 봇 명령 처리."""
    reply = _send_stock_message

    if cmd in ("/start", "/help"):
        _send_stock_with_keyboard(_STOCK_HELP_TEXT)

    # ── 국내주식 명령 비활성 (미국주식 전용 운영) ──
    # elif cmd == "/status":
    #     try:
    #         from core.stock.trader import build_stock_status_msg
    #         reply(build_stock_status_msg())
    #     except Exception as e:
    #         logging.exception("Telegram(주식) /status 처리 오류")
    #         reply(f"⚠️ 상태 조회 오류: {html.escape(str(e)[:100])}")

    # elif cmd == "/perf":
    #     try:
    #         reply(_stock_cmd_perf())
    #     except Exception as e:
    #         logging.exception("Telegram(주식) /perf 처리 오류")
    #         reply(f"⚠️ 성과 조회 오류: {html.escape(str(e)[:100])}")

    # elif cmd == "/positions":
    #     try:
    #         reply(_stock_cmd_positions())
    #     except Exception as e:
    #         logging.exception("Telegram(주식) /positions 처리 오류")
    #         reply(f"⚠️ 포지션 조회 오류: {html.escape(str(e)[:100])}")

    # elif cmd == "/history":
    #     try:
    #         n = 10
    #         if arg:
    #             try:
    #                 n = max(1, min(50, int(arg.strip())))
    #             except ValueError:
    #                 pass
    #         reply(_stock_cmd_history(n))
    #     except Exception as e:
    #         logging.exception("Telegram(주식) /history 처리 오류")
    #         reply(f"⚠️ 이력 조회 오류: {html.escape(str(e)[:100])}")

    # elif cmd == "/params":
    #     try:
    #         reply(_stock_cmd_params())
    #     except Exception as e:
    #         logging.exception("Telegram(주식) /params 처리 오류")
    #         reply(f"⚠️ 파라미터 조회 오류: {html.escape(str(e)[:100])}")

    # elif cmd == "/market":
    #     try:
    #         reply(_stock_cmd_market())
    #     except Exception as e:
    #         logging.exception("Telegram(주식) /market 처리 오류")
    #         reply(f"⚠️ 장중 상태 조회 오류: {html.escape(str(e)[:100])}")

    # elif cmd == "/start_sim":
    #     from core.stock.trader import _stock_trading_state, _stock_lock, start_stock_trading
    #     with _stock_lock:
    #         _enabled = _stock_trading_state["enabled"]
    #     if _enabled:
    #         reply("이미 주식 시뮬 매매가 실행 중입니다. /stop 으로 중지하세요.")
    #     elif arg:
    #         raw = arg.strip().replace(",", "").replace("원", "")
    #         try:
    #             budget = float(raw)
    #         except ValueError:
    #             reply("⚠️ 금액이 올바르지 않습니다. 예: /start_sim 1000000")
    #             return
    #         if budget <= 0:
    #             reply("⚠️ 예산은 0보다 커야 합니다.")
    #             return
    #         result = start_stock_trading(budget)
    #         reply(result)
    #     else:
    #         result = start_stock_trading()
    #         reply(result)

    # elif cmd == "/start_live":
    #     reply(
    #         "🚧 <b>주식 실거래는 아직 지원하지 않습니다.</b>\n"
    #         "현재는 시뮬레이션 모드만 사용할 수 있습니다.\n"
    #         "/start_sim 으로 시뮬을 시작하세요."
    #     )

    # elif cmd == "/risk":
    #     reply(
    #         "🚧 <b>주식 봇 리스크 트래킹은 아직 구현 중입니다.</b>\n"
    #         "일일 손실 한도·연속 손절 추적 기능이 추가될 예정입니다."
    #     )

    # elif cmd == "/stop":
    #     from core.stock.trader import stop_stock_trading
    #     result = stop_stock_trading()
    #     reply(result)

    elif cmd == "/us_perf":
        try:
            reply(_us_cmd_perf())
        except Exception as e:
            logging.exception("Telegram(주식) /us_perf 처리 오류")
            reply(f"⚠️ 성과 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/us_history":
        try:
            n = 10
            if arg:
                try:
                    n = max(1, min(50, int(arg.strip())))
                except ValueError:
                    pass
            reply(_us_cmd_history(n))
        except Exception as e:
            logging.exception("Telegram(주식) /us_history 처리 오류")
            reply(f"⚠️ 이력 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/us_params":
        try:
            reply(_us_cmd_params())
        except Exception as e:
            logging.exception("Telegram(주식) /us_params 처리 오류")
            reply(f"⚠️ 파라미터 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/us_universe":
        try:
            reply(_us_cmd_universe())
        except Exception as e:
            logging.exception("Telegram(주식) /us_universe 처리 오류")
            reply(f"⚠️ 유니버스 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/us_market":
        try:
            reply(_us_cmd_market())
        except Exception as e:
            logging.exception("Telegram(주식) /us_market 처리 오류")
            reply(f"⚠️ 장 상태 조회 오류: {html.escape(str(e)[:100])}")

    elif cmd == "/us_start_sim":
        import core.stock.us_trader as _us_mod
        from core.stock.us_trader import start_us_sim
        with _us_mod._us_lock:
            running = _us_mod._us_running
        if running:
            reply("이미 미국주식 시뮬이 실행 중입니다. /us_stop 으로 중지하세요.")
        else:
            budget = None
            if arg:
                raw = arg.strip().lstrip("$").replace(",", "")
                try:
                    budget = float(raw)
                except ValueError:
                    reply("⚠️ 금액이 올바르지 않습니다. 예: /us_start_sim 1000")
                    return
                if budget <= 0:
                    reply("⚠️ 예산은 0보다 커야 합니다.")
                    return
            result = start_us_sim(budget)
            msg = result.get("msg", "시작 실패")
            if result.get("ok") and not _us_mod.is_us_market_hours():
                msg += "\n\n⚠️ 현재 장 외 시간입니다. 개장 시 자동으로 매매를 시작합니다.\n" + _us_cmd_market()
            reply(msg)

    elif cmd == "/us_stop":
        from core.stock.us_trader import stop_us
        result = stop_us()
        reply(result.get("msg", "중지됨"))

    elif cmd == "/us_status":
        try:
            from core.stock.us_trader import build_us_status_msg
            reply(build_us_status_msg())
        except Exception as e:
            logging.exception("Telegram(주식) /us_status 처리 오류")
            reply(f"⚠️ 미국주식 상태 조회 오류: {html.escape(str(e)[:100])}")

    else:
        reply(f"알 수 없는 명령: {html.escape(cmd)}\n/help 로 명령어를 확인하세요.")


def stock_telegram_worker():
    """주식 봇 전용 Telegram 명령 폴링 워커."""
    if not notifier.is_stock_configured():
        logging.info("TELEGRAM_STOCK_BOT_TOKEN 미설정 — 주식 봇 비활성")
        return

    token = config.TELEGRAM_STOCK_BOT_TOKEN
    _register_stock_commands()
    offset = _drain_stock_updates(token)
    logging.info("Telegram 주식 명령 봇 시작 (chat_id=%s)", config.TELEGRAM_CHAT_ID)
    _send_stock_with_keyboard("🏦 주식 봇이 시작됐습니다. /help 로 명령어를 확인하세요.")

    while True:
        try:
            updates, offset = _get_stock_updates(offset, token)
            if not updates:
                continue
            for upd in updates:
                msg = upd.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text    = (msg.get("text") or "").strip()
                if not text:
                    continue
                if chat_id != str(config.TELEGRAM_CHAT_ID):
                    logging.warning("Telegram(주식) 미등록 chat_id(%s)의 명령 무시: %s",
                                    chat_id, text[:50])
                    continue
                # Reply Keyboard 버튼 텍스트 → 명령어로 변환
                if text in _STOCK_BUTTON_MAP:
                    text = _STOCK_BUTTON_MAP[text]
                parts = text.split(maxsplit=1)
                cmd   = parts[0].split("@")[0].lower()
                arg   = parts[1].strip() if len(parts) > 1 else None
                logging.info("Telegram(주식) 명령 수신: %s", cmd)
                _handle_stock_command(cmd, arg)
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logging.warning("Telegram(주식) 폴링 오류: %s — 10초 후 재시도", e)
            time.sleep(10)


def start_telegram_bot():
    """명령 봇 스레드 기동 (앱 시작 시 1회 호출)"""
    t = threading.Thread(target=telegram_worker, daemon=True, name="telegram-command-bot")
    t.start()
    r = threading.Thread(target=_daily_report_worker, daemon=True, name="telegram-daily-report")
    r.start()
    s = threading.Thread(target=stock_telegram_worker, daemon=True, name="telegram-stock-bot")
    s.start()
