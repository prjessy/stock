"""보유종목 데일리 케어 — 매일 09:30(평일) 자동: 보유종목 손익·지표를 Claude가 보고
홀딩/손절/익절을 '추천'(자동매도 아님) → 카톡·텔레그램 + data/holdings_care.json 저장.

추천만 함(주문 안 함). 예외는 올리지 않는다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.core.market import KST
from app.notify.dispatch import notify_all

_FILE = Path(settings.db_path).resolve().parent / "holdings_care.json"


def _gather(registry) -> list[dict]:
    src = registry.kr_source()
    if not hasattr(src, "_ensure_token"):
        return []
    try:
        from app.trading.kis_order import OrderClient
        bal = OrderClient(src).get_balance()
    except Exception:
        return []
    if not bal.get("ok"):
        return []
    out = []
    for h in (bal.get("holdings") or []):
        sym = h.get("symbol")
        rsi = trend = pos = None
        try:
            from app.analysis.feed import compute_feed
            feed = compute_feed(sym, registry.history(sym, "1y"), registry.quote(sym),
                                registry.fundamentals(sym), registry.investor_flow(sym))
            rsi = (feed.get("indicators") or {}).get("rsi14")
            trend = feed.get("trend")
            pos = (feed.get("levels") or {}).get("w52_position_pct")
        except Exception:
            pass
        out.append({**h, "rsi": rsi, "trend": trend, "pos52": pos})
    return out


def _ai_care(holdings: list[dict]) -> str:
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key or not holdings:
        return ""
    try:
        import anthropic
    except Exception:
        return ""
    lines = []
    for h in holdings:
        lines.append(
            f"{h.get('name')} 손익{h.get('pnl_pct', 0):+.1f}% "
            f"(평단 {int(h.get('avg_price', 0)):,}/현재 {int(h.get('cur_price', 0)):,}) "
            f"RSI {h.get('rsi')} {h.get('trend') or ''} 52주위치 {h.get('pos52')}%")
    prompt = ("내 보유종목 현황이다:\n" + "\n".join(lines) +
              "\n\n한국 주식 '판단 보조' 분석가로서 각 종목에 [홀딩/손절검토/익절검토] 중 하나와 "
              "한 줄 이유를 달아라. 손실 크고 추세 나쁘면 손절검토, 이익 크고 과열이면 익절검토. "
              "단정·예측·투자권유 금지. 간결하게.")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model, max_tokens=700,
            system="간결하고 균형 잡힌 보유종목 코멘트. 추천이지 지시가 아님. 과신 금지.",
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            from app.analysis.token_usage import record
            record(resp, settings.deudeumi_model, "holdings_care")
        except Exception:
            pass
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception:
        return ""


def generate(registry, send: bool = True) -> dict:
    holdings = _gather(registry)
    if not holdings:
        return {"ok": False, "error": "보유종목 없음(또는 계좌 미설정)"}
    report = _ai_care(holdings)
    if not report:
        return {"ok": False, "error": "리포트 생성 실패(AI 키 미설정)"}
    now = datetime.now(KST)
    out = {"ok": True, "ts": now.strftime("%Y-%m-%d %H:%M"), "report": report,
           "count": len(holdings)}
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if send:
        notify_all("💼 보유종목 데일리 케어", report)
    return out


def load() -> dict:
    try:
        if _FILE.exists():
            return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"ok": False, "report": "", "ts": None}


class HoldingsCareScheduler:
    """평일 09:30 보유종목 케어 리포트 1회 발송."""

    def __init__(self, registry, hour: int = 9, minute: int = 30) -> None:
        self._registry = registry
        self._hour, self._minute = hour, minute
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_date: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="care-sched")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(35)
        while not self._stop.is_set():
            now = datetime.now(KST)
            today = now.strftime("%Y-%m-%d")
            after = now.hour > self._hour or (now.hour == self._hour and now.minute >= self._minute)
            if now.weekday() < 5 and after and self._last_date != today:
                self._last_date = today
                try:
                    from app.core import alert_config
                    send = "report_care" in (alert_config.load().get("types") or [])
                    generate(self._registry, send=send)
                except Exception:
                    pass
            self._stop.wait(300)
