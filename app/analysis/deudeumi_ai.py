"""더듬이 2·3 — AI(Claude) 기반 매수/매도 시점 판단 (판단 보조).

앱이 /api/feed 지표 묶음을 Claude(Anthropic API)에 넘겨 변동장 시점(더듬이2)·종목 추이(더듬이3)를
판단한다. 모든 신호는 data/deudeumi_signals.jsonl 에 기록(진화 씨앗) — 과거 신호를 다음 판단의
맥락으로 다시 넣는다. 예측이 아니라 근거 기반 '판단 보조'. 예외는 올리지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from app.config import settings

_LOG = Path(settings.db_path).resolve().parent / "deudeumi_signals.jsonl"

_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["strong_buy", "buy", "hold", "sell", "strong_sell"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string", "description": "한국어 한두 문장 판단 요약"},
        "buy_zone": {"type": "string", "description": "매수 적정 구간(가격), 없으면 '-'"},
        "sell_zone": {"type": "string", "description": "매도 목표 구간(가격), 없으면 '-'"},
        "stop": {"type": "string", "description": "손절 참고가, 없으면 '-'"},
        "key_factors": {"type": "array", "items": {"type": "string"}, "description": "판단 핵심 근거 지표 3~5개"},
    },
    "required": ["signal", "confidence", "summary", "buy_zone", "sell_zone", "stop", "key_factors"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 주식 매매 '판단 보조' 분석가다. 미래를 단정·예측하지 않고, 주어진 지표 근거로만 "
    "현재 시점의 매수/매도 우호도를 판단한다. 과신 금지. 반드시 스키마 JSON으로만 답한다."
)


def recent_signals(symbol: str, limit: int = 5) -> list[dict]:
    """해당 종목의 최근 신호 기록(진화 맥락용)."""
    if not _LOG.exists():
        return []
    out = []
    try:
        for line in _LOG.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("symbol") == symbol:
                    out.append(rec)
            except Exception:
                continue
    except Exception:
        return []
    return out[-limit:]


def _log_signal(symbol: str, data: dict) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), "symbol": symbol,
               "signal": data.get("signal"), "confidence": data.get("confidence"),
               "price": data.get("price"), "summary": data.get("summary")}
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def analyze(symbol: str, feed: dict, recent: list[dict] | None = None) -> dict:
    if feed.get("error"):
        return {"symbol": symbol, "error": feed["error"]}
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"symbol": symbol, "error": "ANTHROPIC_API_KEY 미설정"}
    try:
        import anthropic
    except Exception:
        return {"symbol": symbol, "error": "anthropic 패키지 미설치"}

    hist_txt = ""
    if recent:
        lines = [f"- {r.get('ts','')}: {r.get('signal')}({r.get('confidence')}) @ {r.get('price')}" for r in recent]
        hist_txt = "\n\n과거 내 신호 기록(참고, 일관성·과매매 점검용):\n" + "\n".join(lines)

    prompt = (
        f"다음은 {feed.get('name')}({symbol}) 의 기술/밸류 지표 묶음(JSON)입니다.\n"
        f"```json\n{json.dumps(feed, ensure_ascii=False)}\n```\n"
        f"[더듬이2] 최근 변동성·추세 흐름상 매수/매도 시점이 임박했는가?\n"
        f"[더듬이3] 이 종목의 최근 추이 특성(정/역배열, 거래량, 모멘텀)을 반영해 판단.\n"
        f"추세·지지/저항·거래량·모멘텀(RSI/스토캐스틱/MACD)·밸류(PER/PBR)·52주 위치를 종합해 "
        f"매수/매도 우호도와 근거를 판단 보조로 제시하세요.{hist_txt}"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model,
            max_tokens=1024,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data = json.loads(text)
    except Exception as exc:
        return {"symbol": symbol, "error": f"분석 실패: {exc}"}

    data["symbol"] = symbol
    data["name"] = feed.get("name")
    data["price"] = feed.get("price")
    data["asof"] = feed.get("asof")
    _log_signal(symbol, data)
    return data
