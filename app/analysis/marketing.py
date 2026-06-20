"""마케팅 자료 자동 최신화 — 종목별 최신 뉴스 → Claude로 한줄요약 + 홍보 카피.

구글뉴스 RSS(무료)로 헤드라인을 모아 Claude(가성비 모델)로 사실 기반 요약/카피를 만들고
data/marketing.json 에 저장한다(갱신시각 포함). 생성 실패 시 직전 파일을 유지(graceful).
예측·투자 단정이 아니라 '사실 기반 콘텐츠'. 예외는 올리지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "marketing.json"

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "헤드라인을 종합한 한국어 핵심 요약 2~3문장(사실만, 간결히)"},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "summary 안에서 색으로 강조할 핵심 단어·수치 2~4개(요약문에 나온 그대로 발췌)"},
        "sentiment": {"type": "string", "enum": ["긍정", "중립", "부정"]},
    },
    "required": ["summary", "keywords", "sentiment"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 주식 뉴스 에디터다. 주어진 뉴스 헤드라인만 근거로 과장·투자 단정 없이 "
    "핵심을 간결하게 요약한다(2~3문장). 반드시 스키마 JSON으로만 답한다."
)


def default_items() -> list[tuple[str, str]]:
    """워치리스트 (심볼, 종목명) 목록."""
    from app.datasources.kr_price import KR_META
    from app.datasources.us_market import US_META

    def nm(s: str) -> str:
        m = KR_META.get(s) or US_META.get(s)
        return m["name"] if m else s

    return [(s, nm(s)) for s in (list(settings.kr_symbols) + list(settings.us_symbols))]


def _fetch_news(name: str, limit: int = 5) -> list[dict]:
    """구글뉴스 RSS 헤드라인 [{title, link, date}] (실패 시 [])."""
    try:
        q = urllib.parse.quote(name)
        url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            xml = r.read()
        root = ET.fromstring(xml)
        items: list[dict] = []
        for it in root.iter("item"):
            t = (it.findtext("title") or "").strip()
            if not t:
                continue
            items.append({
                "title": t,
                "link": (it.findtext("link") or "").strip(),
                "date": (it.findtext("pubDate") or "").strip(),
            })
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []


def _claude_copy(name: str, headlines: list[dict]) -> dict | None:
    """헤드라인 → 요약/카피/분위기. 실패/미설정 시 None."""
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key or not headlines:
        return None
    try:
        import anthropic
    except Exception:
        return None
    titles = "\n".join(f"- {h['title']}" for h in headlines)
    prompt = (
        f"{name} 관련 최신 뉴스 헤드라인입니다:\n{titles}\n\n"
        f"이 헤드라인들만 근거로 summary(핵심 2~3문장 요약)·keywords(요약문에서 강조할 핵심 단어/수치 2~4개, 원문 그대로)·sentiment(분위기)를 채우세요. "
        f"과장·투자 단정 금지, 간결하게."
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model,
            max_tokens=900,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(text)
    except Exception:
        return None


def generate(items: list[tuple[str, str]] | None = None) -> dict:
    """종목별 뉴스 수집 + Claude 카피 생성 후 저장. 반환=저장 dict."""
    items = items or default_items()
    stocks = []
    for symbol, name in items:
        news = _fetch_news(name)
        copy = _claude_copy(name, news) or {}
        stocks.append({
            "symbol": symbol,
            "name": name,
            "summary": copy.get("summary"),
            "copy": copy.get("copy"),
            "sentiment": copy.get("sentiment"),
            "headlines": news[:5],
        })
    data = {"updated_at": _dt.datetime.now().isoformat(timespec="seconds"), "stocks": stocks}
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return data


def load() -> dict:
    """저장된 마케팅 자료(없으면 {available:False})."""
    try:
        if _FILE.exists():
            return {"available": True, **json.loads(_FILE.read_text(encoding="utf-8"))}
    except Exception:
        pass
    return {"available": False}
