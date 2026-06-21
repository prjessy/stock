"""자동매매 감시 — 등락률 밴드 매수/매도 + 손절 + 예약 매도. 서버가 장중 자동 실행.

안전 설계:
- 마스터 스위치(autotrade_config.enabled) OFF면 아무것도 안 함(기본 OFF).
- 한국장(평일 09:00~15:30)에만 동작.
- 종목·행위(매수/매도)당 거래일 1회만 발동(스팸·연속거래 방지, 날짜 바뀌면 리셋).
- 규칙별 qty(수량 상한)만큼만 거래. 매수는 cash 부족 시 KIS가 거부.
- 지정가 = 현재가로 주문(유동성 큰 종목은 즉시 체결). 결과는 카톡·텔레그램 통지.

판정 기준:
- 매수: 등락률(전일대비 %) ≤ buy_pct  (예 -2% 이하면 매수)
- 매도: 등락률 ≥ sell_pct  /  현재가 ≤ stop_price  /  평단대비 ≤ stop_pct  /  현재시각 ≥ sell_time
  (매도 조건 중 하나라도 충족 시 보유분 매도)
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.scheduler import is_market_open
from app.notify.dispatch import notify_all
from app.trading import autotrade_config
from app.trading.kis_order import OrderClient

_KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


class AutoTradeWatcher:
    def __init__(self, registry, poller, interval: float = 15.0) -> None:
        self._registry = registry
        self._poller = poller          # 등락률(change_pct)·현재가 스냅샷
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._done: dict[str, set] = {}   # {거래일: {"종목:행위"}} — 발동 완료

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="autotrade-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _today(self) -> str:
        return datetime.now(_KST).strftime("%Y-%m-%d")

    def _done_action(self, symbol: str, action: str) -> bool:
        return f"{symbol}:{action}" in self._done.get(self._today(), set())

    def _mark(self, symbol: str, action: str) -> None:
        today = self._today()
        for d in list(self._done):
            if d != today:
                self._done.pop(d, None)
        self._done.setdefault(today, set()).add(f"{symbol}:{action}")

    def _loop(self) -> None:
        self._stop.wait(25)  # 기동 여유
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                logger.exception("autotrade 틱 실패")
            self._stop.wait(self._interval)

    def _check(self) -> None:
        now = datetime.now(_KST)
        if not is_market_open(now):
            return
        cfg = autotrade_config.load()
        if not cfg.get("enabled"):
            return
        rules = cfg.get("rules") or {}
        if not rules:
            return
        src = self._registry.kr_source()
        if not hasattr(src, "_ensure_token"):
            return
        client = OrderClient(src)
        quotes = {q.get("symbol"): q for q in self._poller.quotes() if not q.get("stale")}
        bal = client.get_balance()
        held = {h["symbol"]: h for h in (bal.get("holdings") or [])} if bal.get("ok") else {}
        hhmm = now.strftime("%H:%M")
        for sym, rule in rules.items():
            q = quotes.get(sym)
            if not q:
                continue
            cp = q.get("change_pct")
            price = q.get("price") or 0
            name = q.get("name") or sym
            # ① 매수 (등락률 밴드) — 보유 여부와 무관
            bp = rule.get("buy_pct")
            if (bp is not None and cp is not None and price > 0
                    and not self._done_action(sym, "buy") and cp <= bp):
                self._mark(sym, "buy")
                self._buy(client, sym, name, rule, int(price), cp)
                continue  # 같은 틱에 매도까지 하지 않음
            # ② 매도 — 보유분 필요
            if self._done_action(sym, "sell"):
                continue
            h = held.get(sym)
            if not h or h["qty"] <= 0:
                continue
            reason = self._sell_reason(rule, cp, price or h.get("cur_price") or 0,
                                       h.get("avg_price") or 0, hhmm)
            if reason:
                self._mark(sym, "sell")
                self._sell(client, sym, name, h, rule, price or h.get("cur_price") or 0, reason)

    def _sell_reason(self, rule, cp, price, avg, hhmm) -> str | None:
        sp = rule.get("sell_pct")
        if sp is not None and cp is not None and cp >= sp:
            return f"매도 등락률 +{sp:g}% 도달(현재 {cp:+.2f}%)"
        stp = rule.get("stop_price")
        if stp is not None and price > 0 and price <= stp:
            return f"손절가 도달(현재 {int(price):,} ≤ {int(stp):,}원)"
        spct = rule.get("stop_pct")
        if spct is not None and avg > 0 and ((price - avg) / avg * 100) <= spct:
            return f"손절 {spct:g}% 도달(평단 대비 {(price-avg)/avg*100:+.2f}%)"
        st = rule.get("sell_time")
        if st is not None and hhmm >= st:
            return f"예약 매도 시각 도달({st})"
        return None

    def _buy(self, client, sym, name, rule, price, cp) -> None:
        qty = int(rule.get("qty") or 1)
        res = client.place_order(sym, "buy", qty, price=price, cap=qty)
        if res.get("ok"):
            notify_all("🤖 자동 매수 실행",
                       f"{name}({sym}) {min(qty, qty)}주 매수 접수\n"
                       f"사유: 등락률 {cp:+.2f}% ≤ {rule.get('buy_pct'):g}%\n"
                       f"지정가 {price:,}원 · 주문번호 {res.get('order_no','-')}")
        else:
            notify_all("⚠️ 자동 매수 실패",
                       f"{name}({sym}) 매수 시도 실패\n오류: {res.get('error','')}")

    def _sell(self, client, sym, name, h, rule, price, reason) -> None:
        qty = int(h["qty"])
        cap = int(rule.get("qty") or 1)
        res = client.place_order(sym, "sell", qty, price=int(price), cap=cap)
        sold = min(qty, cap)
        if res.get("ok"):
            notify_all("🤖 자동 매도 실행",
                       f"{name}({sym}) {sold}주 매도 접수\n사유: {reason}\n"
                       f"지정가 {int(price):,}원 · 주문번호 {res.get('order_no','-')}")
        else:
            notify_all("⚠️ 자동 매도 실패",
                       f"{name}({sym}) 매도 시도 실패\n사유: {reason}\n오류: {res.get('error','')}")
