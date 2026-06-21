"""자동 매도 감시 — 손절(stop-loss) + 스케줄 매도. 브라우저 없이 서버가 장중 자동 실행.

안전 설계:
- 마스터 스위치(autosell_config.enabled) OFF면 아무것도 안 함(기본 OFF).
- 매도 전용 — 보유분(잔고)만 판다. 자동 매수 절대 없음.
- 한국장(평일 09:00~15:30)에만 동작.
- 종목당 거래일 1회만 발동(스팸·중복매도 방지, 날짜 바뀌면 리셋).
- 규칙별 max_qty 상한 + 실제 보유수량 둘 중 작은 값만 매도.
- 지정가 = 현재가로 매도(삼성전자 등 유동성 큰 종목은 즉시 체결). 결과는 카톡·텔레그램 통지.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.scheduler import is_market_open
from app.notify.dispatch import notify_all
from app.trading import autosell_config
from app.trading.kis_order import OrderClient

_KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


class AutoSellWatcher:
    def __init__(self, registry, interval: float = 15.0) -> None:
        self._registry = registry
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._done: dict[str, set] = {}   # {거래일: {종목코드}} — 발동 완료

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="autosell-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _today(self) -> str:
        return datetime.now(_KST).strftime("%Y-%m-%d")

    def _mark_done(self, symbol: str) -> None:
        today = self._today()
        for d in list(self._done):
            if d != today:
                self._done.pop(d, None)
        self._done.setdefault(today, set()).add(symbol)

    def _already_done(self, symbol: str) -> bool:
        return symbol in self._done.get(self._today(), set())

    def _loop(self) -> None:
        self._stop.wait(25)  # 기동 여유
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                logger.exception("autosell 틱 실패")
            self._stop.wait(self._interval)

    def _check(self) -> None:
        now = datetime.now(_KST)
        if not is_market_open(now):
            return
        cfg = autosell_config.load()
        if not cfg.get("enabled"):
            return
        rules = cfg.get("rules") or {}
        if not rules:
            return
        src = self._registry.kr_source()
        if not hasattr(src, "_ensure_token"):
            return
        client = OrderClient(src)
        bal = client.get_balance()
        if not bal.get("ok"):
            logger.info("autosell: 잔고 조회 실패 — %s", bal.get("error"))
            return
        held = {h["symbol"]: h for h in (bal.get("holdings") or [])}
        hhmm = now.strftime("%H:%M")
        for sym, rule in rules.items():
            if self._already_done(sym):
                continue
            h = held.get(sym)
            if not h or h["qty"] <= 0:
                continue
            cur = h.get("cur_price") or 0
            avg = h.get("avg_price") or 0
            if cur <= 0:
                continue
            reason = self._trigger_reason(rule, cur, avg, hhmm)
            if not reason:
                continue
            self._mark_done(sym)  # 먼저 마킹(같은 틱 재시도 방지)
            self._sell(client, sym, h, rule, cur, reason)

    def _trigger_reason(self, rule: dict, cur: float, avg: float, hhmm: str) -> str | None:
        """발동 사유 문자열 반환(없으면 None)."""
        sp = rule.get("stop_price")
        if sp is not None and cur <= sp:
            return f"손절가 도달(현재 {int(cur):,} ≤ {int(sp):,}원)"
        spct = rule.get("stop_pct")
        if spct is not None and avg > 0:
            pnl = (cur - avg) / avg * 100
            if pnl <= spct:
                return f"손절 {spct:g}% 도달(평단 대비 {pnl:+.2f}%)"
        st = rule.get("sell_time")
        if st is not None and hhmm >= st:
            return f"예약 매도 시각 도달({st})"
        return None

    def _sell(self, client: OrderClient, sym: str, h: dict, rule: dict, cur: float, reason: str) -> None:
        qty = int(h["qty"])
        cap = int(rule.get("max_qty") or 1)   # 규칙 안전 상한(기본 1)
        name = h.get("name") or sym
        res = client.place_order(sym, "sell", qty, price=int(cur), cap=cap)
        sold = min(qty, cap)
        if res.get("ok"):
            notify_all("🤖 자동 매도 실행",
                       f"{name}({sym}) {sold}주 매도 접수\n사유: {reason}\n"
                       f"지정가 {int(cur):,}원 · 주문번호 {res.get('order_no','-')}")
        else:
            notify_all("⚠️ 자동 매도 실패",
                       f"{name}({sym}) 매도 시도 실패\n사유: {reason}\n오류: {res.get('error','')}")
