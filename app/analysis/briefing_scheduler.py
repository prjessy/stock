"""미국 브리핑 자동 생성 — 하루 1회 새벽(기본 6시 KST), 한국장 개장 전 준비.

미국장 마감(대략 05~06시 KST) 직후 생성하여 프리장(08:00) 전 7시까지 준비되게 한다.
시작 시 자료 없으면 1회 생성. 실패해도 직전 파일 유지(graceful).
"""
from __future__ import annotations

import threading
from datetime import datetime

from app.analysis.briefing import generate, load
from app.core.market import KST


class BriefingScheduler:
    def __init__(self, registry, hour: int = 6) -> None:
        self._registry = registry
        self._hour = hour
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_date: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="brief-sched")
        self._thread.start()

    def _loop(self) -> None:
        if not load().get("available"):
            try:
                generate(self._registry)
            except Exception:
                pass
        while not self._stop.is_set():
            now = datetime.now(KST)
            today = now.strftime("%Y-%m-%d")
            if now.hour >= self._hour and self._last_date != today:
                self._last_date = today
                try:
                    generate(self._registry)
                except Exception:
                    pass
            self._stop.wait(600)

    def stop(self) -> None:
        self._stop.set()
