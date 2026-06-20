"""마케팅 자료 자동 생성 스케줄러 — 하루 1회(장마감 후, 기본 16시 KST).

가벼운 백그라운드 스레드. 시작 시 자료가 없으면 1회 생성, 이후 매일 지정 시각 이후 1회 갱신.
생성 실패해도 직전 파일을 유지한다(graceful). interval 개념 없이 '하루 1회'만 보장.
"""
from __future__ import annotations

import threading
from datetime import datetime

from app.analysis.marketing import generate, load
from app.core.market import KST


class MarketingScheduler:
    def __init__(self, hour: int = 16) -> None:
        self._hour = hour
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_date: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mkt-sched")
        self._thread.start()

    def _loop(self) -> None:
        # 자료가 아예 없으면 첫 구동 시 1회 생성(빈 탭 방지).
        if not load().get("available"):
            try:
                generate()
            except Exception:
                pass
        while not self._stop.is_set():
            now = datetime.now(KST)
            today = now.strftime("%Y-%m-%d")
            if now.hour >= self._hour and self._last_date != today:
                self._last_date = today
                try:
                    generate()
                except Exception:
                    pass
            self._stop.wait(600)  # 10분마다 시각 체크

    def stop(self) -> None:
        self._stop.set()
