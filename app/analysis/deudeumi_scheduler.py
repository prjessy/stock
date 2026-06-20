"""더듬이 2·3 자동 감시 — 본장 중 주기 실행, 트리거 시 Claude 판단 → 사이렌(텔레그램).

비용 절감: 매 주기 규칙 트리거(RSI 극단·핵심가 근접·급변)를 먼저 보고, 트리거가 있을 때만
Claude(더듬이2·3)를 호출한다. 실행 가능 신호(buy/sell, 신뢰도 medium↑)가 직전과 바뀌면
hermes send 로 텔레그램 사이렌을 보낸다. 신호는 deudeumi_ai.analyze 가 jsonl 에 기록(진화).
DEUDEUMI_INTERVAL_MIN=0 이면 비활성. 본장(09:00~15:30 KST 평일)에만 동작.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import threading
from zoneinfo import ZoneInfo

from app.analysis.deudeumi_ai import analyze, recent_signals
from app.analysis.feed import compute_feed

_KST = ZoneInfo("Asia/Seoul")
_HERMES = "/usr/local/bin/hermes"


class DeudeumiScheduler:
    def __init__(self, registry, poller, interval_min: int = 0) -> None:
        self._registry = registry
        self._poller = poller
        self._interval = max(0, interval_min) * 60
        self._stop = threading.Event()
        self._thread = None
        self._last: dict[str, str] = {}

    def start(self) -> None:
        if self._interval <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="deudeumi")
        self._thread.start()

    def _market_open(self) -> bool:
        now = _dt.datetime.now(_KST)
        if now.weekday() >= 5:
            return False
        t = now.time()
        return _dt.time(9, 0) <= t <= _dt.time(15, 30)

    def _loop(self) -> None:
        # 기동 직후 한 박자 쉬고 시작(시드 폴링 여유)
        self._stop.wait(30)
        while not self._stop.is_set():
            try:
                if self._market_open():
                    for sym in self._registry.watchlist():
                        try:
                            self._check(sym)
                        except Exception:
                            pass
            except Exception:
                pass
            self._stop.wait(self._interval)

    def _trigger(self, feed: dict) -> bool:
        ind = feed.get("indicators", {})
        lv = feed.get("levels", {})
        P = feed.get("price")
        rsi = ind.get("rsi14")
        st = ind.get("stochastic_k")
        if rsi is not None and (rsi <= 35 or rsi >= 68):
            return True
        if st is not None and (st <= 20 or st >= 85):
            return True
        cp = feed.get("change_pct")
        if cp is not None and abs(cp) >= 3:
            return True
        if P:
            for k in ("recent_high", "recent_low"):
                v = lv.get(k)
                if v and abs(P - v) / P <= 0.015:
                    return True
        return False

    def _check(self, sym: str) -> None:
        rows = self._registry.history(sym, "1y")
        quote = next((x for x in self._poller.quotes() if x.get("symbol") == sym), None)
        if quote is None:
            quote = self._registry.source_for(sym).get_quote(sym)
        fund = self._registry.fundamentals(sym)
        feed = compute_feed(sym, rows, quote, fund)
        if feed.get("error") or not self._trigger(feed):
            return  # 트리거 없으면 Claude 호출 안 함(비용 절감)
        res = analyze(sym, feed, recent_signals(sym))
        sig = res.get("signal")
        conf = res.get("confidence")
        if sig in ("buy", "strong_buy", "sell", "strong_sell") and conf in ("medium", "high"):
            if self._last.get(sym) != sig:
                self._last[sym] = sig
                self._siren(sym, res)

    def _siren(self, sym: str, res: dict) -> None:
        emoji = "🟢" if "buy" in (res.get("signal") or "") else "🔴"
        msg = (
            f"{emoji} {res.get('name')}({sym}) · {res.get('signal')} ({res.get('confidence')})\n"
            f"{res.get('summary')}\n"
            f"매수 {res.get('buy_zone')}\n매도 {res.get('sell_zone')}\n손절 {res.get('stop')}\n"
            f"현재가 {res.get('price')}"
        )
        try:
            subprocess.run(
                [_HERMES, "send", "--to", "telegram", "--subject", "🚨 더듬이 신호 (바로 지금이야!)", msg],
                env={**os.environ, "HERMES_HOME": "/root/.hermes"},
                timeout=30, capture_output=True,
            )
        except Exception:
            pass
