# -*- coding: utf-8 -*-
"""Telegram 명령 봇 — 폰에서 매매 원격 제어

지원 명령:
  /status      현재 상태·잔고·포지션 요약
  /start_sim   시뮬레이션 매매 시작
  /start_live  실거래 매매 시작 (→ /confirm 2단계 확인)
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

import logging
import threading
import time
from datetime import datetime, timedelta

import requests

from core import config
from core import notifier
from core import trading_control

_API_BASE     = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30                     # long polling 대기 (초)
_REQ_TIMEOUT  = _POLL_TIMEOUT + 10     # HTTP 타임아웃은 폴링 대기보다 길게
_CONFIRM_TTL  = 60                     # /confirm 유효시간 (초)
_CONFLICT_BACKOFF = 60                 # 409 (다른 폴러 존재) 시 대기 (초)

# 위험 명령 2단계 확인 상태 (단일 사용자 전제이므로 모듈 전역으로 충분)
_pending = {"action": None, "expires": None}

_HELP_TEXT = (
    "<b>명령어 목록</b>\n"
    "/status — 상태·잔고·포지션 요약\n"
    "/start_sim — 시뮬레이션 시작\n"
    "/start_live — 실거래 시작 (확인 필요)\n"
    "/stop — 매매 중지 (보유 유지)\n"
    "/liquidate — 중지 + 전액 청산 (확인 필요)\n"
    "/confirm — 위험 명령 확인 (60초 이내)\n"
    "/help — 이 도움말"
)


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
    _pending["expires"] = datetime.now() + timedelta(seconds=_CONFIRM_TTL)


def _take_pending() -> str | None:
    action, expires = _pending["action"], _pending["expires"]
    _pending["action"] = _pending["expires"] = None
    if action and expires and datetime.now() <= expires:
        return action
    return None


def _handle_command(cmd: str):
    """명령 처리. 응답은 notifier 큐로 전송 (HTML 모드)."""
    reply = notifier.send

    if cmd in ("/start", "/help"):
        reply(_HELP_TEXT)

    elif cmd == "/status":
        try:
            reply(trading_control.status_summary())
        except Exception as e:
            logging.exception("Telegram /status 처리 오류")
            reply(f"⚠️ 상태 조회 오류: {str(e)[:100]}")

    elif cmd == "/start_sim":
        result = trading_control.start_trading(live_mode=False)
        if not result.get("ok"):
            reply(f"⚠️ {result.get('error', '시작 실패')}")
        # 성공 알림은 start_trading 내부의 notify_event가 전송

    elif cmd == "/start_live":
        _set_pending("start_live")
        reply("⚠️ <b>실거래 시작 확인</b>\n실제 계좌에서 매수·매도가 실행됩니다.\n"
              f"계속하려면 {_CONFIRM_TTL}초 내에 /confirm 을 입력하세요.")

    elif cmd == "/stop":
        result = trading_control.stop_trading(mode="hold")
        if not result.get("ok"):
            reply(f"⚠️ {result.get('error', '중지 실패')}")

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
                reply(f"⚠️ {result.get('error', '시작 실패')}")
        elif action == "liquidate":
            result = trading_control.stop_trading(mode="liquidate")
            if not result.get("ok"):
                reply(f"⚠️ {result.get('error', '청산 실패')}")

    else:
        reply(f"알 수 없는 명령: {cmd}\n/help 로 명령어를 확인하세요.")


def telegram_worker():
    """Telegram 명령 폴링 워커 (데몬 스레드). 미설정 시 즉시 종료."""
    if not notifier.is_configured():
        logging.info("Telegram 미설정 — 명령 봇 비활성")
        return

    offset = _drain_old_updates()
    logging.info("Telegram 명령 봇 시작 (chat_id=%s)", config.TELEGRAM_CHAT_ID)

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
                cmd = text.split()[0].split("@")[0].lower()  # "/status@MyBot arg" → "/status"
                logging.info("Telegram 명령 수신: %s", cmd)
                _handle_command(cmd)
        except requests.exceptions.Timeout:
            continue   # long polling 타임아웃은 정상
        except Exception as e:
            logging.warning("Telegram 폴링 오류: %s — 10초 후 재시도", e)
            time.sleep(10)


def start_telegram_bot():
    """명령 봇 스레드 기동 (앱 시작 시 1회 호출)"""
    t = threading.Thread(target=telegram_worker, daemon=True, name="telegram-command-bot")
    t.start()
