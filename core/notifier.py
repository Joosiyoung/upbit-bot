# -*- coding: utf-8 -*-
"""Telegram 알림 모듈

.env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 설정된 경우에만 동작.
미설정 시 모든 함수가 조용히 no-op이 되므로 호출부에서 분기할 필요 없음.

전송은 백그라운드 큐 워커 스레드에서 처리 → 매매 사이클을 블로킹하지 않고,
Telegram API 장애가 봇 동작에 영향을 주지 않음.
"""

import html
import logging
import queue
import threading

import requests

from core import config

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_QUEUE_MAX = 100          # 큐 포화 시 오래된 알림은 버림 (봇 동작 보호 우선)
_SEND_TIMEOUT = 10        # 초

_queue: "queue.Queue[str]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker_started = False
_worker_lock = threading.Lock()


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _worker():
    url = _API_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    while True:
        text = _queue.get()
        try:
            resp = requests.post(url, json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=_SEND_TIMEOUT)
            if resp.status_code != 200:
                logging.warning("Telegram 전송 실패 (%d): %s",
                                resp.status_code, resp.text[:200])
        except Exception as e:
            logging.warning("Telegram 전송 오류: %s", e)
        finally:
            _queue.task_done()


def _ensure_worker():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            t = threading.Thread(target=_worker, daemon=True, name="telegram-notifier")
            t.start()
            _worker_started = True


def send(text: str):
    """알림 전송 (비동기). 미설정 시 no-op."""
    if not is_configured():
        return
    _ensure_worker()
    try:
        _queue.put_nowait(text)
    except queue.Full:
        logging.warning("Telegram 알림 큐 포화 → 메시지 폐기")


def send_sync(text: str) -> tuple[bool, str]:
    """동기 전송 (연결 테스트용). (성공 여부, 메시지) 반환."""
    if not is_configured():
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 .env에 없습니다."
    try:
        url = _API_URL.format(token=config.TELEGRAM_BOT_TOKEN)
        resp = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=_SEND_TIMEOUT)
        if resp.status_code == 200:
            return True, "전송 성공"
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────
# 메시지 포맷터
# ─────────────────────────────────────────────

_TYPE_LABEL = {
    "buy":       "🟢 매수",
    "sell":      "🔴 매도",
    "buy_fail":  "⚠️ 매수 실패",
    "sell_fail": "🚨 매도 실패",
}


def notify_trade(entry: dict):
    """_log_trade 엔트리를 Telegram 메시지로 변환해 전송. hold는 무시."""
    if not is_configured():
        return
    etype = entry.get("type")
    label = _TYPE_LABEL.get(etype)
    if label is None:
        return

    mode = "실거래" if entry.get("live") else "시뮬레이션"
    ticker_safe = html.escape(str(entry.get("ticker", "-")))
    lines = [f"<b>{label}</b> · {ticker_safe} <i>({mode})</i>"]

    profit_pct = entry.get("profit_pct")
    if etype == "sell" and profit_pct is not None:
        sign = "📈" if profit_pct >= 0 else "📉"
        lines.append(f"{sign} 수익률: <b>{profit_pct:+.2f}%</b>")

    price = entry.get("price") or 0
    amount = entry.get("amount") or 0
    if price:
        lines.append(f"체결가: {price:,.0f}원")
    if amount:
        lines.append(f"금액: {amount:,.0f}원")

    reason = entry.get("reason")
    if reason:
        lines.append(f"사유: {html.escape(str(reason))}")

    lines.append(f"⏰ {entry.get('date', '')} {entry.get('time', '')}")
    send("\n".join(lines))


def notify_event(title: str, body: str = ""):
    """매매 시작/중지, 리스크 차단 등 시스템 이벤트 알림."""
    if not is_configured():
        return
    msg = f"<b>{html.escape(title)}</b>"
    if body:
        msg += f"\n{html.escape(body)}"
    send(msg)


# ─────────────────────────────────────────────
# 주식 봇 전용 알림 (TELEGRAM_STOCK_BOT_TOKEN)
# ─────────────────────────────────────────────

_stock_queue: "queue.Queue[str]" = queue.Queue(maxsize=_QUEUE_MAX)
_stock_worker_started = False
_stock_worker_lock = threading.Lock()


def is_stock_configured() -> bool:
    return bool(config.TELEGRAM_STOCK_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _stock_notifier_worker():
    url = _API_URL.format(token=config.TELEGRAM_STOCK_BOT_TOKEN)
    while True:
        text = _stock_queue.get()
        try:
            resp = requests.post(url, json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=_SEND_TIMEOUT)
            if resp.status_code != 200:
                logging.warning("Telegram(주식) 전송 실패 (%d): %s",
                                resp.status_code, resp.text[:200])
        except Exception as e:
            logging.warning("Telegram(주식) 전송 오류: %s", e)
        finally:
            _stock_queue.task_done()


def _ensure_stock_worker():
    global _stock_worker_started
    with _stock_worker_lock:
        if not _stock_worker_started:
            t = threading.Thread(target=_stock_notifier_worker, daemon=True,
                                 name="telegram-stock-notifier")
            t.start()
            _stock_worker_started = True


def send_stock(text: str):
    """주식 봇 알림 전송 (비동기). 미설정 시 코인 봇으로 fallback."""
    if not is_stock_configured():
        send(text)   # fallback: 주식 토큰 없으면 코인 봇으로
        return
    _ensure_stock_worker()
    try:
        _stock_queue.put_nowait(text)
    except queue.Full:
        logging.warning("Telegram(주식) 알림 큐 포화 → 메시지 폐기")
