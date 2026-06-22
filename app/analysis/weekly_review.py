"""주간 복기 리포트 — 매주 토요일 09:00: 이번주 매매 내역 + 더듬이 적중률 + 보유 현황을
Claude가 복기('잘한 점/아쉬운 점/다음주 포인트') → 카톡·텔레그램 + data/weekly_review.json.

복기·요약만(주문 안 함). 예외는 올리지 않는다.
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

_FILE = Path(settings.db_path).resolve().parent / "weekly_review.json"


def _gather(registry) -> dict:
    orders, holdings = [], []
    src = registry.kr_source()
    if hasattr(src, "_ensure_token"):
        try:
            from app.trading.kis_order import OrderClient
            oc = OrderClient(src)
            res = oc.list_orders(days=7)
            if res.get("ok"):
                orders = res.get("orders") or []
            bal = oc.get_balance()
            if bal.get("ok"):
                holdings = bal.get("holdings") or []
        except Exception:
            pass
    # 더듬이 적중률(종목별)
    acc = []
    try:
        from app.analysis.deudeumi_ai import evaluate_signals
        for sym in settings.kr_symbols:
            e = evaluate_signals(sym, registry)
            if e.get("evaluated"):
                acc.append({"name": sym, "acc": e.get("accuracy_pct"),
                            "n": e.get("evaluated"), "hits": e.get("hits")})
    except Exception:
        pass
    return {"orders": orders, "holdings": holdings, "acc": acc}


def _ai_review(data: dict) -> str:
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ""
    try:
        import anthropic
    except Exception:
        return ""
    parts = []
    od = data["orders"]
    if od:
        buys = sum(1 for o in od if "매수" in (o.get("side") or ""))
        sells = sum(1 for o in od if "매도" in (o.get("side") or ""))
        lines = [f"{o.get('date', '')} {o.get('name')} {o.get('side')} {o.get('ccld_qty', 0)}주 @{o.get('ccld_price', 0):,} [{o.get('status')}]"
                 for o in od[:20]]
        parts.append(f"[이번주 주문 {len(od)}건 (매수{buys}/매도{sells})]\n" + "\n".join(lines))
    else:
        parts.append("[이번주 주문] 없음")
    if data["acc"]:
        parts.append("[더듬이 AI 적중률] " + ", ".join(
            f"{a['name']} {a['acc']}%({a['hits']}/{a['n']})" for a in data["acc"]))
    if data["holdings"]:
        parts.append("[현재 보유] " + ", ".join(
            f"{h['name']} {h.get('pnl_pct', 0):+.1f}%" for h in data["holdings"][:12]))
    prompt = ("내 이번주 주식 활동이다:\n" + "\n".join(parts) +
              "\n\n한국 주식 '판단 보조' 분석가로서 주간 복기를 해라:\n"
              "1) 이번주 한 줄 총평\n2) 잘한 점 / 아쉬운 점\n3) 다음주 점검 포인트 2~3개\n"
              "간결하게(400자 내외), 단정·예측·투자권유 금지.")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model, max_tokens=800,
            system="간결하고 균형 잡힌 주간 매매 복기. 과신·투자권유 금지.",
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            from app.analysis.token_usage import record
            record(resp, settings.deudeumi_model, "weekly_review")
        except Exception:
            pass
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception:
        return ""


def generate(registry, send: bool = True) -> dict:
    data = _gather(registry)
    report = _ai_review(data)
    if not report:
        return {"ok": False, "error": "리포트 생성 실패(AI 키 미설정)"}
    now = datetime.now(KST)
    out = {"ok": True, "ts": now.strftime("%Y-%m-%d %H:%M"), "report": report,
           "counts": {"orders": len(data["orders"]), "holdings": len(data["holdings"])}}
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if send:
        notify_all("🗓️ 주간 복기 리포트", report)
    return out


def load() -> dict:
    try:
        if _FILE.exists():
            return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"ok": False, "report": "", "ts": None}


class WeeklyReviewScheduler:
    """매주 토요일 09:00 주간 복기 1회 발송."""

    def __init__(self, registry, weekday: int = 5, hour: int = 9) -> None:
        self._registry = registry
        self._weekday, self._hour = weekday, hour
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_week: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="weekly-sched")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(40)
        while not self._stop.is_set():
            now = datetime.now(KST)
            week = now.strftime("%G-W%V")   # ISO 연-주차
            if now.weekday() == self._weekday and now.hour >= self._hour and self._last_week != week:
                self._last_week = week
                try:
                    from app.core import alert_config
                    send = "report_weekly" in (alert_config.load().get("types") or [])
                    generate(self._registry, send=send)
                except Exception:
                    pass
            self._stop.wait(600)
