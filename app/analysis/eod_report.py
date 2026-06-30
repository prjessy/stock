"""장 마감 AI 리포트 — 매일 15:40(장 마감) 자동: 오늘 수급/테마/발목/보유종목을 모아
Claude가 '오늘 요약 + 내일 관전포인트'로 정리 → 카톡·텔레그램 발송 + data/eod_report.json 저장.

위험 0(주문 안 함). 데이터 수집은 결정적, 판단/요약만 AI. 예외는 올리지 않는다.
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

_FILE = Path(settings.db_path).resolve().parent / "eod_report.json"


def _gather(registry) -> dict:
    """리포트용 데이터 수집(결정적)."""
    quotes = []
    for sym in settings.kr_symbols:
        try:
            q = registry.quote(sym)
            if q and q.get("price") is not None:
                quotes.append({"name": q.get("name", sym), "change_pct": q.get("change_pct")})
        except Exception:
            continue
    try:
        from app.analysis.etf_flow import scan_inflow
        flow = (scan_inflow(registry).get("items") or [])
    except Exception:
        flow = []
    try:
        from app.analysis.bottom_detect import scan as bscan
        balmok = (bscan(registry, 2).get("items") or [])
    except Exception:
        balmok = []
    holdings = []
    try:
        src = registry.kr_source()
        if hasattr(src, "_ensure_token"):
            from app.trading.kis_order import OrderClient
            bal = OrderClient(src).get_balance()
            if bal.get("ok"):
                holdings = bal.get("holdings") or []
    except Exception:
        pass
    return {"quotes": quotes, "flow": flow, "balmok": balmok, "holdings": holdings}


def _ai_report(data: dict) -> str:
    from app import llm
    if not llm.configured():
        return ""
    parts = []
    if data["quotes"]:
        parts.append("[관심종목 등락] " + ", ".join(
            f"{q['name']} {q['change_pct']:+.2f}%" for q in data["quotes"] if q.get("change_pct") is not None))
    if data["flow"]:
        parts.append("[테마 수급 포착] " + ", ".join(
            f"{i['theme']}·{i['name']} {i['who']} {i.get('reason', '')}" for i in data["flow"][:12]))
    if data["balmok"]:
        parts.append("[발목(저점권)] " + ", ".join(
            f"{b['name']}(신호{b['score']})" for b in data["balmok"][:12]))
    if data["holdings"]:
        parts.append("[보유종목] " + ", ".join(
            f"{h['name']} {h.get('pnl_pct', 0):+.1f}%" for h in data["holdings"][:12]))
    if not parts:
        return ""
    prompt = ("오늘 한국 장 마감 데이터다:\n" + "\n".join(parts) +
              "\n\n한국 주식 '판단 보조' 분석가로서 정리해라:\n"
              "1) 오늘 한 줄 요약\n2) 눈에 띄는 수급/테마 흐름\n3) 내일 관전포인트 2~3개\n"
              "간결하게(전체 300자 내외), 단정·예측·투자권유 금지.")
    try:
        return llm.chat_text("간결하고 균형 잡힌 한국 장 마감 코멘트. 과신 금지.",
                             prompt, max_tokens=700, source="eod_report")
    except Exception:
        return ""


def generate(registry, send: bool = True) -> dict:
    """리포트 생성 → 저장 (+옵션 발송). 반환 {ok, ts, report}."""
    data = _gather(registry)
    report = _ai_report(data)
    if not report:
        return {"ok": False, "error": "리포트 생성 실패(데이터 없음 또는 AI 키 미설정)"}
    now = datetime.now(KST)
    out = {"ok": True, "ts": now.strftime("%Y-%m-%d %H:%M"), "report": report,
           "counts": {"flow": len(data["flow"]), "balmok": len(data["balmok"]),
                      "holdings": len(data["holdings"])}}
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if send:
        notify_all("📊 장 마감 AI 리포트", report, shared=True)
    return out


def load() -> dict:
    try:
        if _FILE.exists():
            return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"ok": False, "report": "", "ts": None}


class EodScheduler:
    """평일 장 마감(15:40) 직후 1회 리포트 자동 발송."""

    def __init__(self, registry, hour: int = 15, minute: int = 40) -> None:
        self._registry = registry
        self._hour, self._minute = hour, minute
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_date: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="eod-sched")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(30)
        while not self._stop.is_set():
            now = datetime.now(KST)
            today = now.strftime("%Y-%m-%d")
            after = now.hour > self._hour or (now.hour == self._hour and now.minute >= self._minute)
            if now.weekday() < 5 and after and self._last_date != today:
                self._last_date = today
                try:
                    from app.core import alert_config
                    send = "report_eod" in (alert_config.load().get("types") or [])
                    generate(self._registry, send=send)  # 발송은 알림설정 토글, 생성은 항상(앱 표시)
                except Exception:
                    pass
            self._stop.wait(300)  # 5분마다 시각 체크
