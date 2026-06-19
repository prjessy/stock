"""Hermes 게이트웨이 Notifier 구현체.

같은 VPS 의 Hermes(AI 에이전트)에 localhost HTTP POST 로 데이터를 넘기면
Hermes 가 Telegram 으로 중계한다.

부분 실패 격리(AC-6/AC-9): Hermes 가 응답하지 않아도 예외를 던지지 않고
False 를 반환 + 로그만 남긴다. 호출자(Core)는 멈추지 않는다.

TODO: confirm Hermes endpoint/auth contract
  - 실제 엔드포인트 경로(/notify, /send 등)와 인증(토큰 헤더 여부)이 미확정.
  - 현재는 합리적 기본값(POST {base}/notify, JSON body)을 사용한다.
  - Hermes 측 명세 확정 후 _ENDPOINT / 헤더 / body 스키마를 맞춘다.
"""
from __future__ import annotations

import logging

import requests

from app.config import settings
from app.notify.base import Notifier

logger = logging.getLogger(__name__)

# TODO: confirm Hermes endpoint/auth contract — 아래 경로/타임아웃은 잠정값.
_ENDPOINT = "/notify"
_TIMEOUT_SECONDS = 5


class HermesNotifier(Notifier):
    """localhost Hermes HTTP 클라이언트."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.hermes_base_url).rstrip("/")
        self.url = f"{self.base_url}{_ENDPOINT}"

    def send_message(self, text: str) -> bool:
        """텍스트 알림을 Hermes 로 전송."""
        return self._post({"type": "message", "text": text})

    def send_report(self, payload: dict) -> bool:
        """구조화 리포트를 Hermes 로 전송."""
        return self._post({"type": "report", "payload": payload})

    def _post(self, body: dict) -> bool:
        """공통 POST 헬퍼. 실패해도 절대 예외를 올리지 않는다."""
        try:
            resp = requests.post(self.url, json=body, timeout=_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            # 네트워크 오류/타임아웃/HTTP 에러 전부 여기서 흡수.
            logger.warning("Hermes 전송 실패 (%s): %s", self.url, exc)
            return False
