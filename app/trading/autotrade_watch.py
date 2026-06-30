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
        self._last_balmok = None           # 발목 스캔 throttle(10분)
        self._grid: dict[str, dict] = {}   # {거래일: {종목: 그리드상태}} — 반복매매 사이클

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

    def _in_trade_window(self, cfg: dict, hhmm: str) -> bool:
        """자동매매 허용 시간대 안인지. trade_time_start~end(HH:MM, 미설정이면 장 전체)."""
        ts = cfg.get("trade_time_start")
        te = cfg.get("trade_time_end")
        if ts and hhmm < ts:
            return False
        if te and hhmm >= te:
            return False
        return True

    def _check(self) -> None:
        now = datetime.now(_KST)
        if not is_market_open(now):
            return
        cfg = autotrade_config.load()
        enabled = cfg.get("enabled")
        rules = cfg.get("rules") or {}
        balmok = cfg.get("balmok") or {}
        splits = cfg.get("splits") or []
        hhmm = now.strftime("%H:%M")
        # 사용자 지정 시간 범위(시작~종료) 밖이면 실거래는 멈춘다(발목 '알람'만 예외).
        in_window = self._in_trade_window(cfg, hhmm)
        trade_on = bool(enabled and in_window)
        run_balmok = balmok.get("alert") or (balmok.get("auto_buy") and trade_on)
        if not enabled and not balmok.get("alert"):
            return   # 아무것도 안 켜짐
        src = self._registry.kr_source()
        if not hasattr(src, "_ensure_token"):
            return
        client = OrderClient(src)
        quotes = {q.get("symbol"): q for q in self._poller.quotes() if not q.get("stale")}
        # 🦶 발목 감지(알람·옵션 자동매수) — 알람은 master·시간범위 무관, 자동매수는 trade_on 일 때만
        if run_balmok:
            self._maybe_balmok(now, balmok, trade_on, client, quotes)
        # 이하 실거래(분할·밴드·손절·예약)는 master ON + 시간 범위 안일 때만
        if not trade_on:
            return
        # ⏱️ 분할 매수/매도 스케줄 — 예정 시각 도달 시 1회씩(거래일 단위 중복방지)
        if splits:
            try:
                self._check_splits(client, splits, quotes, hhmm)
            except Exception:
                logger.exception("분할 스케줄 틱 실패")
        # 등락률 밴드·손절·예약 주문은 규칙 있을 때만
        if not rules:
            return
        bal = client.get_balance()
        held = {h["symbol"]: h for h in (bal.get("holdings") or [])} if bal.get("ok") else {}
        for sym, rule in rules.items():
            q = quotes.get(sym)
            if not q:
                continue
            if not rule.get("on", True):
                continue   # 종목별 OFF — 전체 ON이어도 이 종목은 건너뜀
            cp = q.get("change_pct")
            price = q.get("price") or 0
            name = q.get("name") or sym
            # 🔁 반복 그리드(완전자동) 모드 — 매도가 기준 N회 반복
            if rule.get("mode") == "grid":
                try:
                    self._check_grid(client, sym, name, rule, q, held.get(sym))
                except Exception:
                    logger.exception("grid 틱 실패 %s", sym)
                continue
            # ① 매수 (밴드) — 기준가(가격 ≤ 기준가) 또는 등락률(cp ≤ 매수%). 보유 여부 무관.
            #    주문은 '현재가' 지정가(즉시 체결). 기준가는 '언제 살지' 트리거일 뿐 주문가가 아님.
            bp = rule.get("buy_pct")
            base = rule.get("base_price")
            why = None
            if base is not None and price > 0 and price <= base:
                why = f"기준가 {int(base):,}원 이하(현재 {int(price):,})"
            elif bp is not None and cp is not None and price > 0 and cp <= bp:
                why = f"등락률 {cp:+.2f}% ≤ {bp:g}%"
            if why and not self._done_action(sym, "buy"):
                self._mark(sym, "buy")
                self._buy(client, sym, name, rule, int(price), why)
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

    def _check_splits(self, client, splits, quotes, hhmm) -> None:
        """분할 매수/매도 스케줄 실행. 예정 시각(time) 도달분만, 종목·시각·방향당 거래일 1회."""
        for s in splits:
            sym = s.get("symbol")
            t = s.get("time")
            side = s.get("side")
            qty = int(s.get("qty") or 0)
            if not sym or not t or side not in ("buy", "sell") or qty <= 0:
                continue
            if hhmm < t:
                continue  # 아직 예정 시각 전
            action = f"split:{t}:{side}"
            if self._done_action(sym, action):
                continue
            q = quotes.get(sym)
            if not q or not q.get("price"):
                continue
            price = int(q.get("price"))
            name = q.get("name") or sym
            self._mark(sym, action)  # 주문 전 마킹(중복 방지) — 실패해도 같은 틱 재시도 안 함
            res = client.place_order(sym, side, qty, price=price, cap=qty)
            label = "매수" if side == "buy" else "매도"
            if res.get("ok"):
                notify_all(f"⏱️ 분할 {label} 실행",
                           f"{name}({sym}) {qty}주 {label} 접수\n"
                           f"예정 {t} · 지정가 {price:,}원 · 주문번호 {res.get('order_no','-')}")
            else:
                notify_all(f"⚠️ 분할 {label} 실패",
                           f"{name}({sym}) {label} 시도 실패\n예정 {t}\n오류: {res.get('error','')}")

    def _maybe_balmok(self, now, balmok, master_on, client, quotes) -> None:
        """발목(저점권) 스캔 → 알람(텔레그램·카톡) + 옵션 자동매수. 10분 throttle, 종목당 하루 1회."""
        if self._last_balmok and (now - self._last_balmok).total_seconds() < 600:
            return
        self._last_balmok = now
        from app.analysis.bottom_detect import scan
        res = scan(self._registry, int(balmok.get("min_score") or 2))
        for d in (res.get("items") or []):
            sym, name = d["symbol"], (d.get("name") or d["symbol"])
            body = (f"{name}({sym}) 발목 신호 {d['score']}개\n"
                    f"{' · '.join(d['signals'])}\n"
                    f"현재가 {int(d['price']):,}원 ({(d.get('change_pct') or 0):+.2f}%)")
            # ① 알람 (master 무관)
            if balmok.get("alert") and not self._done_action(sym, "balmok-alert"):
                self._mark(sym, "balmok-alert")
                notify_all("🦶 발목(저점권) 감지", body)
            # ② 자동 매수 (옵션 · master ON 필요)
            if balmok.get("auto_buy") and master_on and not self._done_action(sym, "balmok-buy"):
                self._mark(sym, "balmok-buy")
                q = quotes.get(sym)
                price = int((q.get("price") if q else None) or d["price"])
                qty = int(balmok.get("qty") or 1)
                # ②-1 AI 판단(옵션): 더듬이2·3가 매수 신호일 때만 매수
                if balmok.get("ai_judge"):
                    ok, sig = self._ai_approves_buy(sym, q)
                    if not ok:
                        notify_all("🦶 발목 감지 · AI 보류",
                                   f"{name}({sym}) 발목 {d['score']}개지만 AI 판단='{sig}' → 매수 안 함")
                        continue
                    tag = f"발목 {d['score']}개 + AI '{sig}' 승인"
                else:
                    tag = f"발목 {d['score']}개"
                r = client.place_order(sym, "buy", qty, price=price, cap=qty)
                if r.get("ok"):
                    notify_all("🤖 발목 자동 매수 실행",
                               f"{name}({sym}) {qty}주 매수 접수\n사유: {tag}\n"
                               f"지정가 {price:,}원 · 주문번호 {r.get('order_no','-')}")
                else:
                    notify_all("⚠️ 발목 자동 매수 실패",
                               f"{name}({sym}) 매수 실패\n오류: {r.get('error','')}")

    def _ai_approves_buy(self, sym: str, q: dict | None) -> tuple[bool, str]:
        """더듬이2·3 AI 판단 → 매수 신호(buy/strong_buy)면 (True, 라벨). 실패 시 보수적으로 (False, ...)."""
        try:
            from app.analysis.deudeumi_ai import analyze, evaluate_signals, recent_signals
            from app.analysis.feed import compute_feed
            rows = self._registry.history(sym, "1y")
            quote = q or self._registry.quote(sym)
            feed = compute_feed(sym, rows, quote,
                                self._registry.fundamentals(sym), self._registry.investor_flow(sym))
            ai = analyze(sym, feed, recent_signals(sym), evaluate_signals(sym, self._registry))
            sig = ai.get("signal") or "?"
            return (sig in ("buy", "strong_buy"), sig)
        except Exception as exc:
            logger.exception("발목 AI 판단 실패")
            return (False, f"판단실패:{exc}")

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

    def _buy(self, client, sym, name, rule, price, why) -> None:
        qty = int(rule.get("qty") or 1)
        res = client.place_order(sym, "buy", qty, price=price, cap=qty)
        if res.get("ok"):
            notify_all("🤖 자동 매수 실행",
                       f"{name}({sym}) {qty}주 매수 접수\n"
                       f"사유: {why}\n"
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

    # ===== 🔁 반복 그리드(완전자동) — 1차 기준 후 '매도가 기준' N회 반복 =====
    def _grid_state(self, sym: str) -> dict:
        """종목별 그리드 상태(오늘). {phase, cycle, ref_buy, ref_sell}. 날짜 바뀌면 리셋."""
        today = self._today()
        day = self._grid.get(today)
        if day is None:
            self._grid.clear()
            day = self._grid[today] = {}
        return day.setdefault(sym, {"phase": "wait_buy", "cycle": 0, "ref_buy": None, "ref_sell": None})

    def _check_grid(self, client, sym, name, rule, q, held) -> None:
        st = self._grid_state(sym)
        repeat = int(rule.get("repeat") or 1)
        step = float(rule.get("step_pct") or 0)
        if step <= 0 or st["cycle"] >= repeat:
            return
        price = q.get("price") or 0
        cp = q.get("change_pct")
        if price <= 0:
            return

        if st["phase"] == "wait_buy":
            # 1차(cycle 0): 사용자 기준가(base_price) 또는 종가기준 등락률(buy_pct).
            # 2차~: 직전 매도가 × (1 - step%) 이하면 재매수.
            if st["cycle"] == 0:
                base = rule.get("base_price")
                if base is not None:
                    trigger = price <= base
                    why = f"기준가 {int(base):,}원 이하(현재 {int(price):,})"
                else:
                    bp = rule.get("buy_pct")
                    trigger = bp is not None and cp is not None and cp <= bp
                    why = f"전일대비 {cp:+.2f}% ≤ {bp:g}%"
            else:
                tgt = st["ref_sell"] * (1 - step / 100)
                trigger = st["ref_sell"] and price <= tgt
                why = f"직전 매도가 {int(st['ref_sell']):,} 대비 -{step:g}%({int(tgt):,}) 이하"
            if not trigger:
                return
            # 1차 매수 전 AI 판단(옵션): 위험이면 보류
            if st["cycle"] == 0 and rule.get("ai_judge"):
                ok, verdict = self._grid_ai_ok(sym, q)
                if not ok:
                    if not self._done_action(sym, "grid-ai-hold"):
                        self._mark(sym, "grid-ai-hold")
                        notify_all("🔁 반복매매 · AI 보류",
                                   f"{name}({sym}) 1차 매수 조건이나 AI 판단='{verdict}' → 보류")
                    return
            qty = int(rule.get("qty") or 1)
            res = client.place_order(sym, "buy", qty, price=int(price), cap=qty)
            if res.get("ok"):
                st["phase"] = "holding"
                st["ref_buy"] = price
                notify_all("🔁 반복매매 매수",
                           f"{name}({sym}) {st['cycle']+1}/{repeat}회차 {qty}주 매수\n사유: {why}\n"
                           f"지정가 {int(price):,}원 · 주문번호 {res.get('order_no','-')}")
            else:
                notify_all("⚠️ 반복매매 매수 실패", f"{name}({sym})\n{res.get('error','')}")

        elif st["phase"] == "holding":
            if not held or held.get("qty", 0) <= 0:
                return
            target = st["ref_buy"] * (1 + step / 100) if st["ref_buy"] else None
            # 익절(증감율 도달) 또는 손절(설정 시)
            reason = None
            if target and price >= target:
                reason = f"매수가 {int(st['ref_buy']):,} 대비 +{step:g}%({int(target):,}) 도달"
            else:
                sr = self._sell_reason(rule, cp, price, held.get("avg_price") or 0,
                                       datetime.now(_KST).strftime("%H:%M"))
                if sr:
                    reason = sr
            if not reason:
                return
            qty = int(held["qty"])
            cap = int(rule.get("qty") or 1)
            res = client.place_order(sym, "sell", qty, price=int(price), cap=cap)
            if res.get("ok"):
                st["phase"] = "wait_buy"
                st["ref_sell"] = price
                st["cycle"] += 1
                done = st["cycle"] >= repeat
                notify_all("🔁 반복매매 매도",
                           f"{name}({sym}) {st['cycle']}/{repeat}회차 매도\n사유: {reason}\n"
                           f"지정가 {int(price):,}원 · 주문번호 {res.get('order_no','-')}"
                           + ("\n✅ 설정 회수 완료 — 오늘 종료" if done else "\n→ 다음 회차는 이 매도가 기준 재매수"))
            else:
                notify_all("⚠️ 반복매매 매도 실패", f"{name}({sym})\n{res.get('error','')}")

    def _grid_ai_ok(self, sym: str, q: dict) -> tuple[bool, str]:
        """1차 매수 전 AI 판단(ai_advisor). '위험'이면 (False, ...). 실패 시 보수적 통과."""
        try:
            from app.analysis.feed import compute_feed
            from app.trading.ai_advisor import judge
            rows = self._registry.history(sym, "1y")
            feed = compute_feed(sym, rows, q or self._registry.quote(sym),
                                self._registry.fundamentals(sym), self._registry.investor_flow(sym))
            res = judge(sym, feed)
            v = res.get("verdict") or "?"
            return (v != "위험", v)
        except Exception:
            return (True, "판단생략")
