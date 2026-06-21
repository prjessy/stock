"""Claude API 토큰 사용량 누적 기록(data/token_usage.json).

앱이 Claude 를 호출할 때마다 resp.usage(입력/출력 토큰)를 날짜별로 더해 저장한다.
설정창에서 '오늘 앱이 쓴 토큰·예상비용'을 보여주기 위함. 예외는 절대 올리지 않는다.

⚠️ 여기서 보이는 건 '이 앱(API)'의 사용량만이다. Claude Code(CLI)·claude.ai 채팅은
별개 창구라 여기 안 잡힌다 — 전체 정확한 금액은 Anthropic Console(Usage)에서 봐야 한다.
가격은 추정치(2026년 기준 공개가, 100만 토큰당 USD). 실제 청구는 Console 기준.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "token_usage.json"

# 모델별 추정 단가(USD / 1M tokens) — (입력, 출력). 미상 모델은 sonnet 으로 추정.
_PRICES = {
    "haiku": (1.0, 5.0),
    "sonnet": (3.0, 15.0),
    "opus": (15.0, 75.0),
}


def _tier(model: str) -> str:
    m = (model or "").lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    return "sonnet"


def _cost(model: str, inp: int, out: int) -> float:
    pi, po = _PRICES[_tier(model)]
    return inp / 1_000_000 * pi + out / 1_000_000 * po


def _load_raw() -> dict:
    try:
        if _FILE.exists():
            return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"days": {}}


def record(resp, model: str, source: str) -> None:
    """messages.create 응답의 usage 를 오늘 날짜에 누적. source='deudeumi'|'briefing'|'marketing'."""
    try:
        u = getattr(resp, "usage", None)
        inp = int(getattr(u, "input_tokens", 0) or 0)
        out = int(getattr(u, "output_tokens", 0) or 0)
        if inp == 0 and out == 0:
            return
        data = _load_raw()
        days = data.setdefault("days", {})
        today = datetime.now().strftime("%Y-%m-%d")
        d = days.setdefault(today, {"input": 0, "output": 0, "calls": 0, "by_source": {}})
        d["input"] += inp
        d["output"] += out
        d["calls"] += 1
        s = d["by_source"].setdefault(source, {"input": 0, "output": 0, "calls": 0})
        s["input"] += inp
        s["output"] += out
        s["calls"] += 1
        # 최근 60일만 유지(파일 비대화 방지).
        if len(days) > 60:
            for k in sorted(days)[:-60]:
                days.pop(k, None)
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def summary() -> dict:
    """오늘·이번달·전체 누적 토큰과 추정비용(USD). model 단가는 현재 설정 모델 기준."""
    data = _load_raw()
    days = data.get("days") or {}
    model = settings.deudeumi_model
    today = datetime.now().strftime("%Y-%m-%d")
    month = today[:7]

    def agg(keys: list[str]) -> dict:
        inp = sum(days[k]["input"] for k in keys)
        out = sum(days[k]["output"] for k in keys)
        calls = sum(days[k]["calls"] for k in keys)
        return {"input": inp, "output": out, "calls": calls,
                "cost_usd": round(_cost(model, inp, out), 4)}

    today_keys = [today] if today in days else []
    month_keys = [k for k in days if k.startswith(month)]
    by_source = {}
    if today in days:
        for src, s in (days[today].get("by_source") or {}).items():
            by_source[src] = {**s, "cost_usd": round(_cost(model, s["input"], s["output"]), 4)}
    return {
        "model": model,
        "today": agg(today_keys),
        "month": agg(month_keys),
        "all": agg(list(days.keys())),
        "today_by_source": by_source,
    }
