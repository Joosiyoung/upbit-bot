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
    "❓ 도움말": "/help",
}

_REPLY_KEYBOARD = {
    "keyboard": [
        ["📊 상태", "💰 성과", "📋 포지션", "⚠️ 리스크"],
        ["▶ 시뮬", "⏹ 중지", "📜 이력", "❓ 도움말"],
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
    {"command": "help",       "description": "도움말"},
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


def start_telegram_bot():
    """명령 봇 스레드 기동 (앱 시작 시 1회 호출)"""
    t = threading.Thread(target=telegram_worker, daemon=True, name="telegram-command-bot")
    t.start()
    r = threading.Thread(target=_daily_report_worker, daemon=True, name="telegram-daily-report")
    r.start()
