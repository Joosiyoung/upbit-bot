# -*- coding: utf-8 -*-
"""Telegram 알림 연결 테스트

사용법: python scripts/test_telegram.py
.env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 설정 후 실행하면 테스트 메시지를 전송한다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import notifier  # noqa: E402


def main():
    if not notifier.is_configured():
        print("[실패] .env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 없습니다.")
        print()
        print("설정 방법:")
        print("  1. Telegram에서 @BotFather 검색 → /newbot → 봇 이름 입력 → 토큰 발급")
        print("  2. 생성된 봇과 대화 시작 (아무 메시지 1회 전송 — 필수)")
        print("  3. @userinfobot 에게 아무 메시지 전송 → 내 chat id 확인")
        print("  4. .env에 추가:")
        print("       TELEGRAM_BOT_TOKEN=123456:ABC-...")
        print("       TELEGRAM_CHAT_ID=123456789")
        sys.exit(1)

    ok, msg = notifier.send_sync(
        "✅ <b>Upbit 자동매매 봇 — Telegram 연결 테스트</b>\n"
        "이 메시지가 보이면 알림 설정이 완료된 것입니다.")
    if ok:
        print("[성공] 테스트 메시지를 전송했습니다. Telegram을 확인하세요.")
    else:
        print(f"[실패] {msg}")
        print()
        print("점검 사항:")
        print("  - 토큰 오타 여부 (BotFather가 준 전체 문자열)")
        print("  - 봇에게 먼저 메시지를 1회 보냈는지 (안 보내면 chat not found)")
        print("  - chat id가 숫자인지")
        sys.exit(1)


if __name__ == "__main__":
    main()
