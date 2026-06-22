"""자동매매 AI 판단 — 기준값(목표/현재가)·예산을 주면 Claude가 매수 적정성을 판단.

사용자가 '기준값(base_price)'을 정하면, 현재 지표묶음(feed)과 함께 Claude에 넘겨
  - 의견: 적정 / 위험 / 중립
  - 매수 적정 금액(예산 대비) + 권장 수량
  - 근거 / 리스크
를 돌려준다. 예측이 아니라 '판단 보조'. 예외는 올리지 않는다.
"""
from __future__ import annotations

import json
import os

from app.config import settings

_VERDICT = {"type": "string", "enum": ["적정", "위험", "중립"]}
_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": _VERDICT,  # 지금 매수가 적정/위험/중립
        "headline": {"type": "string", "description": "한 줄 결론(한국어)"},
        "summary": {"type": "string", "description": "두세 문장 판단 요약"},
        "buy_amount_krw": {"type": "integer", "description": "권장 매수 금액(원). 예산 내. 매수 비권장이면 0"},
        "buy_qty": {"type": "integer", "description": "권장 매수 수량(주). 비권장이면 0"},
        "entry_zone": {"type": "string", "description": "분할매수 권장 가격대(없으면 '-')"},
        "stop_ref": {"type": "string", "description": "손절 참고가(없으면 '-')"},
        "reasons": {"type": "array", "items": {"type": "string"}, "description": "판단 근거 3~5개"},
        "risk_note": {"type": "string", "description": "주의/리스크 한 문장"},
    },
    "required": ["verdict", "headline", "summary", "buy_amount_krw", "buy_qty",
                 "entry_zone", "stop_ref", "reasons", "risk_note"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 주식 매수 '판단 보조' 분석가다. 미래를 단정·예측하지 않고 주어진 지표·기준값·예산만으로 "
    "지금 매수의 적정성을 적정/위험/중립으로 보수적으로 판단한다. 과신 금지, 손실 가능성 항상 명시. "
    "권장 매수 금액은 절대 예산을 넘기지 말고, 위험하면 0이나 소액 분할을 권한다. 반드시 스키마 JSON으로만 답한다."
)


def judge(symbol: str, feed: dict, base_price: float | None = None,
          budget: float | None = None) -> dict:
    """기준값·예산 기반 매수 적정성 판단. 실패 시 {error:...}."""
    if not feed or feed.get("error"):
        return {"symbol": symbol, "error": feed.get("error") if feed else "지표 없음"}
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"symbol": symbol, "error": "ANTHROPIC_API_KEY 미설정"}
    try:
        import anthropic
    except Exception:
        return {"symbol": symbol, "error": "anthropic 패키지 미설치"}

    price = feed.get("price")
    ctx = [f"현재가: {price}"]
    if base_price:
        ctx.append(f"사용자 기준값(목표/판단 기준 가격): {base_price}")
        if price:
            try:
                gap = (price - float(base_price)) / float(base_price) * 100
                ctx.append(f"현재가는 기준값 대비 {gap:+.1f}%")
            except Exception:
                pass
    if budget:
        ctx.append(f"매수 가능 예산: {int(budget):,}원 (권장 금액은 이 안에서)")
    ctx_txt = "\n".join("- " + c for c in ctx)

    prompt = (
        f"종목 {feed.get('name')}({symbol})의 매수 적정성을 판단하세요.\n\n"
        f"[기준/예산]\n{ctx_txt}\n\n"
        f"[지표 묶음(JSON)]\n```json\n{json.dumps(feed, ensure_ascii=False)}\n```\n\n"
        f"- verdict: 지금 매수가 '적정'(우호적)/'위험'(고평가·과열·하락위험)/'중립'(관망) 중 하나.\n"
        f"- buy_amount_krw/buy_qty: 예산 내 권장 매수 금액·수량. 위험하면 0 또는 소액.\n"
        f"- entry_zone: 분할매수 권장 가격대. stop_ref: 손절 참고가.\n"
        f"예측이 아닌 근거 기반 판단 보조로. 손실 가능성을 risk_note에 반드시."
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
        try:
            from app.analysis.token_usage import record as _rec_usage
            _rec_usage(resp, settings.deudeumi_model, "ai_advisor")
        except Exception:
            pass
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data = json.loads(text)
    except Exception as exc:
        return {"symbol": symbol, "error": f"AI 판단 실패: {exc}"}

    # 예산 초과 방지(안전망) + 수량/금액 정합.
    try:
        if budget and data.get("buy_amount_krw", 0) > budget:
            data["buy_amount_krw"] = int(budget)
        if price and price > 0:
            amt = data.get("buy_amount_krw") or 0
            qty_from_amt = int(amt // price)
            # AI가 준 수량이 금액과 크게 어긋나면 금액 기준으로 보정.
            if data.get("buy_qty", 0) <= 0 or abs(data.get("buy_qty", 0) - qty_from_amt) > max(1, qty_from_amt):
                data["buy_qty"] = qty_from_amt
    except Exception:
        pass

    data["symbol"] = symbol
    data["name"] = feed.get("name")
    data["price"] = price
    data["base_price"] = base_price
    data["budget"] = budget
    return data
