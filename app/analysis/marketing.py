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
        "headline": {"type": "string", "description": "이 종목의 가장 중요한 핵심 한 줄(무슨 일인지 한눈에, 명사형으로 짧게)"},
        "points": {"type": "array", "items": {"type": "string"}, "description": "핵심 포인트 2~4개, 각 짧은 한 줄(사실 중심, 서로 다른 이슈, 과장 금지)"},
        "sentiment": {"type": "string", "enum": ["긍정", "중립", "부정"]},
    },
    "required": ["headline", "points", "sentiment"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 주식 뉴스 에디터다. 주어진 뉴스 헤드라인만 근거로 '무엇이 핵심인지' 한 줄로 짚고 "
    "핵심 포인트를 짧게 정리한다. 과장·투자 단정 금지. 반드시 스키마 JSON으로만 답한다."
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
        f"이 헤드라인들만 근거로 headline(가장 중요한 핵심 한 줄)·points(핵심 포인트 2~4개, 각 짧은 한 줄)·sentiment(분위기)를 채우세요. "
        f"과장·투자 단정 금지, 한눈에 들어오게 간결히."
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
        from app.analysis.token_usage import record as _rec_usage
        _rec_usage(resp, settings.deudeumi_model, "marketing")
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(text)
    except Exception:
        return None


def _sig(st: dict) -> str:
    """내용 비교용 서명 = '실제 뉴스 헤드라인 집합'. Claude 표현이 매번 달라도 뉴스가 같으면 '동일'.

    AI 요약문이 아니라 원본 기사 제목으로 비교해야 의미가 맞다(Claude는 비결정적이라 같은
    뉴스도 표현이 매번 달라짐).
    """
    titles = sorted((h.get("title") or "") for h in (st.get("headlines") or []))
    return "||".join(titles)


def generate(items: list[tuple[str, str]] | None = None) -> dict:
    """종목별 뉴스 수집 + Claude 요약 생성 후 저장. 직전 내용과 비교해 status(신규/변경/동일) 부여."""
    items = items or default_items()
    prev = {st.get("symbol"): _sig(st) for st in (load().get("stocks") or [])}
    stocks = []
    for symbol, name in items:
        news = _fetch_news(name)
        copy = _claude_copy(name, news) or {}
        st = {
            "symbol": symbol,
            "name": name,
            "headline": copy.get("headline"),
            "points": copy.get("points") or [],
            "sentiment": copy.get("sentiment"),
            "headlines": news[:5],
        }
        sig = _sig(st)
        if symbol not in prev:
            st["status"] = "new"       # 신규(처음 등장)
        elif sig == prev[symbol]:
            st["status"] = "same"      # 동일(직전과 같은 내용)
        else:
            st["status"] = "updated"   # 변경(내용 바뀜)
        stocks.append(st)
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
