"""미국 브리핑 — 밤사이 미국 시황 분석(한국장 개장 전 참고).

미국 지수/선물 시세 + 미국장 뉴스를 모아 Claude로 한국어 브리핑을 만들고 data/briefing.json
에 저장한다. 한국장 프리장(08:00)·본장(09:00) 전에 준비되도록 새벽에 생성. 실패 시 직전
파일 유지(graceful). 예측·매매 지시가 아니라 '개장 전 참고 분석'. 예외는 올리지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from app.analysis.marketing import _fetch_news  # 구글뉴스 RSS 재사용
from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "briefing.json"

# 다우·필라델피아 반도체·나스닥 선물·마이크론
_US_SYMS = ["^DJI", "^SOX", "NQ=F", "MU"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "직전 미국 정규장 흐름 2~3문장 요약(기준일 명시)"},
        "semiconductor": {"type": "string", "description": "반도체 섹터(필라델피아 반도체지수·마이크론) 동향과 한국 반도체(삼성전자·SK하이닉스)에의 영향"},
        "kr_implication": {"type": "string", "description": "오늘 한국장 개장 전 참고할 시사점"},
        "one_liner": {"type": "string", "description": "핵심 한 줄"},
        "bias": {"type": "string", "enum": ["우호적", "중립", "주의"]},
    },
    "required": ["overview", "semiconductor", "kr_implication", "one_liner", "bias"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 투자자를 위한 미국 시황 애널리스트다. 제공된 '직전 미국 정규장' 데이터와 뉴스만 "
    "근거로 과장·단정 없이 한국장 개장 전 참고용 브리핑을 쓴다. "
    "데이터는 직전 '완료된' 정규장 마감 기준이므로 '밤사이/간밤에'처럼 방금 끝난 장인 양 쓰지 말고 "
    "반드시 기준일(예: 6/18 목)을 명시한다. 그 이후 미국장이 휴장(주말·공휴일)이었다면 그 사실을 분명히 한다. "
    "반드시 스키마 JSON으로만 답한다."
)


def _us_quotes(registry) -> list[dict]:
    out = []
    for s in _US_SYMS:
        try:
            q = registry.quote(s)
        except Exception:
            q = None
        if q:
            out.append({"symbol": s, "name": q.get("name"),
                        "price": q.get("price"), "change_pct": q.get("change_pct")})
    return out


def _claude(quotes: list[dict], headlines: list[dict], ctx: dict) -> dict | None:
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
    except Exception:
        return None
    qtxt = "\n".join(
        f"- {q['name']}: {q['price']} ({q['change_pct']:+.2f}%)"
        for q in quotes if q.get("change_pct") is not None
    )
    htxt = "\n".join(f"- {h['title']}" for h in headlines)
    last_label = ctx.get("last_session_label") or "직전 거래일"
    mkt = ctx.get("market_label") or "—"
    closed_note = "" if ctx.get("market_open") else (
        f" 현재 미국장은 '{mkt}'(휴장/장외) 상태이며, 위 기준일 이후 새로 열린 정규장은 없다."
    )
    prompt = (
        f"[기준] 직전 완료 미국 정규장: {last_label} 마감. 현재 미국장 상태: {mkt}.{closed_note}\n"
        f"아래 시세는 그 직전 정규장의 종가·등락이다(실시간 아님):\n{qtxt or '(데이터 없음)'}\n\n"
        f"관련 뉴스 헤드라인:\n{htxt or '(없음)'}\n\n"
        f"한국 투자자가 오늘 개장(09:00) 전에 볼 '미국 브리핑'을 스키마대로 작성하세요. "
        f"서두에 기준일({last_label})을 자연스럽게 명시하고, '밤사이' 같은 표현은 쓰지 마세요. "
        f"반도체(삼성전자·SK하이닉스) 연관 영향을 강조."
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model,
            max_tokens=2000,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(text)
    except Exception:
        return None


def generate(registry) -> dict:
    """미국 지수/뉴스 수집 + Claude 브리핑 생성 후 저장. 반환=저장 dict."""
    quotes = _us_quotes(registry)
    news: list[dict] = []
    for q in ["미국 증시", "필라델피아 반도체지수", "엔비디아"]:
        news += _fetch_news(q, limit=3)
    seen, uniq = set(), []
    for h in news:
        if h["title"] not in seen:
            seen.add(h["title"])
            uniq.append(h)
    uniq = uniq[:8]
    from app.core.market import us_brief_context
    ctx = us_brief_context()
    body = _claude(quotes, uniq, ctx) or {}
    out = {"updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
           "market_label": ctx.get("market_label"),
           "market_open": ctx.get("market_open"),
           "last_session_date": ctx.get("last_session_date"),
           "last_session_label": ctx.get("last_session_label"),
           "quotes": quotes, "headlines": uniq, **body}
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return out


def load() -> dict:
    """저장된 미국 브리핑(없으면 {available:False})."""
    try:
        if _FILE.exists():
            return {"available": True, **json.loads(_FILE.read_text(encoding="utf-8"))}
    except Exception:
        pass
    return {"available": False}
