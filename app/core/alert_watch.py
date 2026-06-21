"""서버측 알림 감시 — 브라우저 없이도(폰 꺼도) 알림을 발송한다.

폴러(RealtimePoller) 스냅샷을 주기적으로 보고, 서버에 저장된 알림 설정(alert_config)에
따라 증감(±%)·목표금액 도달을 판정해 텔레그램+카카오로 발송한다. 종목·조건별 거래일 1회
중복방지(메모리, 날짜 바뀌면 리셋). 한국장 시간(평일 08~20시) 밖·휴장(stale) 종목은 건너뛴다.

더듬이1(피보나치)·더듬이4(ETF 수급)는 2단계에서 채운다(설정 토글은 받되 아직 미발송).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.core import alert_config
from app.core.scheduler import is_market_open
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
        self._daily_done: str | None = None  # 더듬이1·4 오늘(9시) 실행한 날짜

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
            try:
                self._maybe_daily()  # 더듬이1·4 — 오전 9시 1회
            except Exception:
                logger.exception("더듬이 일일 작업 실패")
            self._stop.wait(self._interval)

    def _check(self) -> None:
        if not is_market_open(datetime.now(_KST)):
            return  # 장 시간 밖/주말 — 발송 안 함
        cfg = alert_config.load()
        types = cfg.get("types") or []
        if not types:
            return
        step = cfg.get("pct_step") or 0
        count = int(cfg.get("pct_count") or 3)
        targets = cfg.get("targets") or {}
        for q in self._poller.quotes():
            if q.get("stale"):
                continue
            symbol = q.get("symbol")
            currency = q.get("currency", "")
            # ① 증감(±%) — step 배수마다(±step·±2step·±3step…) 도달 시 단계별 1회씩.
            cp = q.get("change_pct")
            if "pct" in types and cp is not None and step > 0:
                n = min(int(abs(cp) / step), count)  # 도달한 배수 개수(설정 횟수까지만)
                sign = 1 if cp >= 0 else -1
                for k in range(1, n + 1):
                    level = round(sign * step * k, 4)
                    if self._mark(symbol, f"pct{level:+g}"):
                        notify_all("🔔 등락 알림", self._msg_pct(q, level))
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
        return (f"{q.get('name')}({q.get('symbol')}) {arrow} {pct:+.2f}% · {t:+g}% 단계 돌파\n"
                f"현재가 {_fmt(q.get('price'), cur)} (전일 {_fmt(q.get('prev_close'), cur)})")

    def _msg_tgt(self, q: dict, tg: dict) -> str:
        cur = q.get("currency", "")
        side = "▲ 이상" if tg.get("dir") == "up" else "▼ 이하"
        return (f"{q.get('name')}({q.get('symbol')}) 목표가 {side} {_fmt(tg['price'], cur)} 도달\n"
                f"현재가 {_fmt(q.get('price'), cur)} ({q.get('change_pct'):+.2f}%)")

    # ---------------- 더듬이1·4 (오전 9시 1회) ----------------
    def _maybe_daily(self) -> None:
        now = datetime.now(_KST)
        if now.weekday() >= 5 or now.hour < 9:   # 평일 09시 이후에 1회
            return
        today = now.strftime("%Y-%m-%d")
        if self._daily_done == today:
            return
        self._daily_done = today  # 오늘 9시 창 처리 표시(중복 방지)
        types = alert_config.load().get("types") or []
        if "deudeumi1" in types:
            try:
                self._run_deudeumi1()
            except Exception:
                logger.exception("더듬이1 실패")
        if "deudeumi4" in types:
            try:
                self._run_deudeumi4()
            except Exception:
                logger.exception("더듬이4 실패")

    def _run_deudeumi1(self) -> None:
        """워치리스트 종목 중 현재가가 피보나치 레벨에 ±1% 근접한 종목 → 알림."""
        from app.analysis.feed import compute_feed
        hits = []
        for sym in settings.kr_symbols:
            try:
                rows = self._registry.history(sym, "1y")
                quote = next((x for x in self._poller.quotes() if x.get("symbol") == sym), None) \
                    or self._registry.quote(sym)
                feed = compute_feed(sym, rows, quote, self._registry.fundamentals(sym),
                                    self._registry.investor_flow(sym))
                P = feed.get("price")
                fib = (feed.get("levels") or {}).get("fib_retracement") or {}
                if not P or not fib:
                    continue
                for label, lv in fib.items():
                    if lv and abs(P - lv) / P <= 0.01:  # 1% 근접
                        hits.append(f"{feed.get('name') or sym}({sym}) 현재 {_fmt(P,'KRW')} ≈ 피보 {label} {_fmt(lv,'KRW')}")
                        break
            except Exception:
                continue
        if hits:
            notify_all("🟣 더듬이1 · 피보나치 근접",
                       "오늘 09시 기준 피보나치 지지/저항 근접:\n" + "\n".join(hits))

    def _run_deudeumi4(self) -> None:
        """KODEX 200 구성종목 중 기관·외국인 매수세가 새로(신규) 들어온 종목 → 알림.

        신규 유입 기준: 당일 순매수>0 인데 직전(5일합−당일)≤0 (최근엔 안 사다 오늘 매수 전환).
        """
        constituents = self._registry.etf_constituents("069500")  # KODEX 200
        if not constituents:
            logger.info("더듬이4: KODEX200 구성종목 비어있음(휴장/필드 미상) — 스킵")
            return
        hits = []
        for code, name in constituents:
            try:
                flow = self._registry.investor_flow(code)
            except Exception:
                flow = None
            time.sleep(0.15)  # KIS 초당 호출 제한 회피(폴러와 겹쳐도 여유 — 약 6.6건/초)
            if not flow:
                continue
            for who, day_key, sum_key in (("외국인", "frgn_ntby_qty", "frgn_ntby_sum"),
                                          ("기관", "orgn_ntby_qty", "orgn_ntby_sum")):
                day = flow.get(day_key)
                tot = flow.get(sum_key)
                if day and day > 0 and tot is not None and (tot - day) <= 0:
                    hits.append(f"{name}({code}) {who} 신규 순매수 +{int(day):,}주")
                    break
        if hits:
            notify_all("🟢 더듬이4 · ETF 매수세 신규 유입",
                       f"KODEX 200 구성종목 중 매수세 신규 유입 {len(hits)}종목 (09시):\n"
                       + "\n".join(hits[:30]))
        else:
            logger.info("더듬이4: 신규 매수 유입 종목 없음")
