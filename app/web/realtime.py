"""실시간 시세 백그라운드 폴러.

브라우저가 매초 /api/quotes 를 호출할 때마다 출처(KIS/yfinance)로 동기 네트워크
호출을 하면, 느린 소스(yfinance ~5s, KIS ~수백ms×N)가 요청을 정체시켜 다른 탭
(history/알람)까지 굶긴다. 그래서 백그라운드 스레드가 주기적으로 시세를 받아 메모리
스냅샷에 저장하고, API 는 그 스냅샷을 '즉시' 반환한다(네트워크 대기 0).

- 국내(KIS)는 빠르므로 짧은 주기(기본 1s)로, 미국(yfinance)은 느리고 지연 데이터라
  긴 주기(기본 10s)로 갱신한다.
- 한 사이클의 심볼들은 스레드풀로 동시에 조회해 벽시계 시간을 단축한다.
- 개별 심볼 실패는 직전 스냅샷 값을 유지한다(부분 실패 격리).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.datasources.registry import SourceRegistry


class RealtimePoller:
    def __init__(self, registry: SourceRegistry, kr_interval: float = 1.0,
                 us_interval: float = 10.0) -> None:
        self._registry = registry
        self._kr_interval = kr_interval
        self._us_interval = us_interval
        self._snapshot: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rt")
        self._kr = list(settings.kr_symbols)
        self._us = list(settings.us_symbols)

    def _fetch(self, symbol: str) -> tuple[str, dict | None]:
        try:
            return symbol, self._registry.source_for(symbol).get_quote(symbol)
        except Exception:
            return symbol, None

    def _refresh(self, symbols: list[str]) -> None:
        for symbol, quote in self._pool.map(self._fetch, symbols):
            if quote is not None:
                with self._lock:
                    self._snapshot[symbol] = quote

    def start(self) -> None:
        if self._thread is not None:
            return
        # 첫 페이지 로드가 빈 화면이 되지 않도록 1회 동기 시드(한 번만 ~수 초).
        self._refresh(self._kr + self._us)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rt-poller")
        self._thread.start()

    def _loop(self) -> None:
        last_us = time.time()  # 시드에서 막 받았으므로 기준 시각으로
        while not self._stop.is_set():
            cycle = time.time()
            symbols = list(self._kr)
            if cycle - last_us >= self._us_interval:
                symbols += self._us
                last_us = cycle
            self._refresh(symbols)
            elapsed = time.time() - cycle
            self._stop.wait(max(0.0, self._kr_interval - elapsed))

    def stop(self) -> None:
        self._stop.set()

    def quotes(self) -> list[dict]:
        """워치리스트 순서대로 최신 스냅샷. 아직 못 받은 심볼은 생략."""
        with self._lock:
            return [self._snapshot[s] for s in self._registry.watchlist()
                    if s in self._snapshot]
