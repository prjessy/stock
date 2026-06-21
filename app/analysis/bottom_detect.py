"""발목(저점권) 감지 — 여러 과매도/저점 신호가 겹치면 '발목'으로 판정.

바닥(절대 최저점)은 사전에 알 수 없다. 대신 저점권("발목")을 지표 합류로 추정한다:
  ① RSI 과매도  ② 52주 위치 하위권  ③ 볼린저 하단 이탈  ④ 스토캐스틱 과매도
  ⑤ 피보나치 깊은 되돌림(61.8%/78.6%) 지지 근접
신호가 min_score(기본 2)개 이상 겹치면 발목으로 본다. compute_feed 결과를 입력으로 쓴다.
순수 계산 — 예외 안 올림.
"""
from __future__ import annotations

from app.analysis.feed import compute_feed
from app.config import settings

# 임계값(보수적 기본). 필요 시 조정.
_RSI_MAX = 32
_POS52_MAX = 22      # 52주 위치 하위 %
_STOCH_MAX = 22
_FIB_NEAR = 0.015    # 피보 지지 ±1.5% 근접


def detect(feed: dict) -> dict | None:
    """compute_feed 결과 → 발목 판정 dict. 데이터 부족이면 None."""
    if not feed or feed.get("error"):
        return None
    ind = feed.get("indicators") or {}
    lv = feed.get("levels") or {}
    price = feed.get("price")
    if not price:
        return None
    signals = []

    rsi = ind.get("rsi14")
    if rsi is not None and rsi <= _RSI_MAX:
        signals.append(f"RSI {rsi} 과매도")

    pos = lv.get("w52_position_pct")
    if pos is not None and pos <= _POS52_MAX:
        signals.append(f"52주 하위 {pos}%")

    bbl = (ind.get("bollinger") or {}).get("lower")
    if bbl and price <= bbl:
        signals.append("볼린저 하단 이탈")

    st = ind.get("stochastic_k")
    if st is not None and st <= _STOCH_MAX:
        signals.append(f"스토캐스틱 {st} 과매도")

    fib = lv.get("fib_retracement") or {}
    for label in ("61%", "78%"):   # 깊은 되돌림 지지
        lvl = fib.get(label)
        if lvl and abs(price - lvl) / price <= _FIB_NEAR:
            signals.append(f"피보 {label} 지지 근접")
            break

    return {
        "symbol": feed.get("symbol"),
        "name": feed.get("name"),
        "price": price,
        "change_pct": feed.get("change_pct"),
        "rsi": rsi,
        "pos52": pos,
        "score": len(signals),
        "signals": signals,
    }


def scan(registry, min_score: int = 2) -> dict:
    """워치리스트(국내) 전 종목 발목 스캔. score≥min_score 만 반환(점수 내림차순)."""
    out = []
    for sym in settings.kr_symbols:
        try:
            rows = registry.history(sym, "1y")
            quote = registry.quote(sym)
            feed = compute_feed(sym, rows, quote,
                                registry.fundamentals(sym), registry.investor_flow(sym))
            d = detect(feed)
            if d and d["score"] >= min_score:
                out.append(d)
        except Exception:
            continue
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"ok": True, "items": out, "min_score": min_score}
