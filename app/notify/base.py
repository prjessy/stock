"""Notifier 인터페이스.

Core 는 "보낸다"만 알면 되고, 실제 채널(Hermes 등)은 구현체로 교체 가능하다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    """알림/리포트 전송 추상 인터페이스."""

    @abstractmethod
    def send_message(self, text: str) -> bool:
        """단순 텍스트 알림 전송. 성공 여부 반환."""
        raise NotImplementedError

    @abstractmethod
    def send_report(self, payload: dict) -> bool:
        """구조화된 리포트(브리핑/분석 등) 전송. 성공 여부 반환."""
        raise NotImplementedError
