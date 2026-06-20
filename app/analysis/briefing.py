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
        "overview": {"type": "string", "description": "밤사이 미국 증시 흐름 2~3문장 요약"},
        "semiconductor": {"type": "string", "description": "반도체 섹터(필라델피아 반도체지수·마이크론) 동향과 한국 반도체(삼성전자·SK하이닉스)에의 영향"},
        "kr_implication": {"type": "string", "description": "오늘 한국장 개장 전 참고할 시사점"},
        "one_liner": {"type": "string", "description": "핵심 한 줄"},
        "bias": {"type": "string", "enum": ["우호적", "중립", "주의"]},
    },
    "required": ["overview", "semiconductor", "kr_implication", "one_liner", "bias"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 투자자를 위한 미국 시황 애널리스트다. 밤사이 미국 증시 데이터와 뉴스만 근거로 "
    "과장·단정 없이 한국장 개장 전 참고용 브리핑을 쓴다. 반드시 스키마 JSON으로만 답한다."
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


def _claude(quotes: list[dict], headlines: list[dict]) -> dict | None:
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
    prompt = (
        f"밤사이 미국 시장 데이터:\n{qtxt or '(데이터 없음)'}\n\n"
        f"관련 뉴스 헤드라인:\n{htxt or '(없음)'}\n\n"
        f"한국 투자자가 오늘 개장(09:00) 전에 볼 '미국 브리핑'을 스키마대로 작성하세요. "
        f"반도체(삼성전자·SK하이닉스) 연관 영향을 강조."
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model,
            max_tokens=800,
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
    body = _claude(quotes, uniq) or {}
    out = {"updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
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
