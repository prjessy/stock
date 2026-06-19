"""콘솔(로그) Notifier.

Hermes 가 미가동/미도달일 때도 알림 동작을 눈으로 검증할 수 있게 한다.
--dry-run 테스트나 로컬 개발에서 사용한다. 항상 성공(True)을 반환한다.
"""
from __future__ import annotations

from app.notify.base import Notifier


class ConsoleNotifier(Notifier):
    """작성된 메시지를 표준출력으로 찍는 Notifier."""

    def send_message(self, text: str) -> bool:
        print("\n----- [알림 메시지] -----")
        print(text)
        print("-------------------------")
        return True

    def send_report(self, payload: dict) -> bool:
        print("\n----- [리포트] -----")
        print(payload)
        print("--------------------")
        return True
