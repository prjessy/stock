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
        self._sym_refresh_interval = 30.0  # 사용자 종목 합집합을 DB 에서 다시 읽는 주기(초)
        self._transient: dict[str, float] = {}  # 비로그인(로컬저장) 사용자가 요청한 심볼 {sym: last_seen}

    def note_symbols(self, symbols: list[str]) -> None:
        """비로그인 사용자가 /api/quotes 로 요청한 심볼을 임시 등록(다음 갱신부터 미리 받아둠)."""
        now = time.time()
        with self._lock:
            for s in symbols:
                if s:
                    self._transient[s] = now

    def refresh_symbols(self) -> None:
        """폴링 대상 = .env 기본종목 ∪ 모든 사용자의 관심종목. 런타임 추가분도 주기적으로 반영.

        한 명이 종목을 추가하면 그 종목 시세도 미리 받아둬야(=스냅샷) /api/quotes 가
        즉시 응답한다. 사용자별 필터링은 API 층에서 한다(여기선 합집합만 받아둠).
        """
        kr = list(settings.kr_symbols)
        us = list(settings.us_symbols)
        try:
            from app.storage.db import Repository
            repo = Repository()
            try:
                for sym, market in repo.all_watchlist_symbols():
                    if market == "US":
                        if sym not in us:
                            us.append(sym)
                    elif sym not in kr:
                        kr.append(sym)
            finally:
                repo.close()
        except Exception:
            pass
        # 비로그인 임시 심볼(최근 10분 내 요청분)도 폴링 대상에 포함, 오래된 건 정리.
        cutoff = time.time() - 600
        with self._lock:
            self._transient = {s: t for s, t in self._transient.items() if t >= cutoff}
            trans = list(self._transient.keys())
        for s in trans:
            if s.isdigit():
                if s not in kr:
                    kr.append(s)
            elif s not in us:
                us.append(s)
        with self._lock:
            self._kr = kr
            self._us = us

    def _fetch(self, symbol: str) -> tuple[str, dict | None]:
        try:
            # registry.quote: 라이브 실패 시 일봉 마지막 종가로 폴백(휴장에도 종가 표시).
            return symbol, self._registry.quote(symbol)
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
        # 폴링 대상에 사용자 관심종목 합집합을 먼저 반영한 뒤 1회 동기 시드(한 번만 ~수 초).
        self.refresh_symbols()
        self._refresh(self._kr + self._us)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rt-poller")
        self._thread.start()

    def _loop(self) -> None:
        now = time.time()
        last_us = now          # 시드에서 막 받았으므로 기준 시각으로
        last_sym = now         # 종목 합집합 갱신 기준 시각
        while not self._stop.is_set():
            cycle = time.time()
            if cycle - last_sym >= self._sym_refresh_interval:
                self.refresh_symbols()
                last_sym = cycle
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
        """워치리스트(기본) 순서대로 최신 스냅샷. 아직 못 받은 심볼은 생략."""
        with self._lock:
            return [self._snapshot[s] for s in self._registry.watchlist()
                    if s in self._snapshot]

    def quotes_for(self, symbols: list[str]) -> list[dict]:
        """주어진 심볼 순서대로 최신 스냅샷(사용자별 시세). 아직 못 받은 심볼은 생략."""
        with self._lock:
            return [self._snapshot[s] for s in symbols if s in self._snapshot]
