"""서버측 알림 감시 — 브라우저 없이도(폰 꺼도) 알림을 발송한다.

폴러(RealtimePoller) 스냅샷을 주기적으로 보고, 서버에 저장된 알림 설정(alert_config)에
따라 증감(±%)·목표금액 도달을 판정해 텔레그램+카카오로 발송한다. 종목·조건별 거래일 1회
중복방지(메모리, 날짜 바뀌면 리셋). 한국장 시간(평일 08~20시) 밖·휴장(stale) 종목은 건너뛴다.

더듬이1(피보나치)·더듬이4(ETF 수급)는 2단계에서 채운다(설정 토글은 받되 아직 미발송).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import alert_config
from app.core.scheduler import is_market_open
from app.core.threshold_engine import crossed_thresholds
from app.notify.dispatch import notify_all

_KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


def _fmt(price, currency: str) -> str:
    if price is None:
        return "-"
    return f"{int(round(price)):,}원" if currency == "KRW" else f"{price:,.2f}"


class AlertWatcher:
    def __init__(self, poller, interval: float = 30.0) -> None:
        self._poller = poller
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sent: dict[str, set] = {}  # {거래일: {(symbol, key)}}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alert-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _today(self) -> str:
        return datetime.now(_KST).strftime("%Y-%m-%d")

    def _mark(self, symbol: str, key: str) -> bool:
        """거래일 1회 중복방지. 새로 발송할 차례면 True."""
        today = self._today()
        for d in list(self._sent):
            if d != today:
                self._sent.pop(d, None)  # 지난 날짜 정리(재무장)
        s = self._sent.setdefault(today, set())
        if (symbol, key) in s:
            return False
        s.add((symbol, key))
        return True

    def _loop(self) -> None:
        self._stop.wait(20)  # 폴러 시드 여유
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                logger.exception("alert-watch 틱 실패")
            self._stop.wait(self._interval)

    def _check(self) -> None:
        if not is_market_open(datetime.now(_KST)):
            return  # 장 시간 밖/주말 — 발송 안 함
        cfg = alert_config.load()
        types = cfg.get("types") or []
        if not types:
            return
        pct_th = cfg.get("pct_thresholds") or []
        targets = cfg.get("targets") or {}
        for q in self._poller.quotes():
            if q.get("stale"):
                continue
            symbol = q.get("symbol")
            currency = q.get("currency", "")
            # ① 증감(±%)
            if "pct" in types and q.get("change_pct") is not None:
                for t in crossed_thresholds(q.get("change_pct"), pct_th):
                    if self._mark(symbol, f"pct{t:+g}"):
                        notify_all("🔔 등락 알림", self._msg_pct(q, t))
            # ② 목표금액
            if "target" in types and q.get("price") is not None:
                tg = targets.get(symbol)
                if tg:
                    up = tg.get("dir") == "up"
                    reached = q["price"] >= tg["price"] if up else q["price"] <= tg["price"]
                    if reached and self._mark(symbol, f"tgt{tg['price']:g}"):
                        notify_all("🎯 목표가 도달", self._msg_tgt(q, tg))
            # ③④ 더듬이1/4 — 2단계 구현 예정(설정에 있어도 아직 미발송)

    def _msg_pct(self, q: dict, t: float) -> str:
        pct = q.get("change_pct")
        arrow = "▲ 상승" if pct >= 0 else "▼ 하락"
        cur = q.get("currency", "")
        return (f"{q.get('name')}({q.get('symbol')}) {arrow} {pct:+.2f}% · 임계값 {t:+g}% 도달\n"
                f"현재가 {_fmt(q.get('price'), cur)} (전일 {_fmt(q.get('prev_close'), cur)})")

    def _msg_tgt(self, q: dict, tg: dict) -> str:
        cur = q.get("currency", "")
        side = "▲ 이상" if tg.get("dir") == "up" else "▼ 이하"
        return (f"{q.get('name')}({q.get('symbol')}) 목표가 {side} {_fmt(tg['price'], cur)} 도달\n"
                f"현재가 {_fmt(q.get('price'), cur)} ({q.get('change_pct'):+.2f}%)")
