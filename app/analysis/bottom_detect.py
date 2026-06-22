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
_RSI_MAX = 35
_POS52_MAX = 25      # 52주 위치 하위 %
_STOCH_MAX = 25
_FIB_NEAR = 0.02     # 피보 지지 ±2% 근접


def detect(feed: dict, min_score: int = 2) -> dict | None:
    """compute_feed 결과 → 발목 판정 dict. 데이터 부족이면 None.

    5개 지표를 모두 검사해 각각 통과/미통과 + 현재값 + 사유를 담는다(checks).
    score = 통과 개수. score≥min_score 면 '발목(저점권)', 아니면 '발목 아님'으로
    판정하고, 어떤 지표가 충족/미충족인지 근거를 함께 돌려준다.
    """
    if not feed or feed.get("error"):
        return None
    ind = feed.get("indicators") or {}
    lv = feed.get("levels") or {}
    price = feed.get("price")
    if not price:
        return None

    checks = []  # {name, ok, detail}

    rsi = ind.get("rsi14")
    if rsi is None:
        checks.append({"name": "RSI", "ok": False, "detail": "데이터 없음"})
    elif rsi <= _RSI_MAX:
        checks.append({"name": "RSI", "ok": True, "detail": f"{rsi} ≤ {_RSI_MAX} 과매도 ✓"})
    else:
        checks.append({"name": "RSI", "ok": False, "detail": f"{rsi} (과매도 {_RSI_MAX} 이하 아님)"})

    pos = lv.get("w52_position_pct")
    if pos is None:
        checks.append({"name": "52주위치", "ok": False, "detail": "데이터 없음"})
    elif pos <= _POS52_MAX:
        checks.append({"name": "52주위치", "ok": True, "detail": f"하위 {pos}% ≤ {_POS52_MAX}% 저점권 ✓"})
    else:
        checks.append({"name": "52주위치", "ok": False, "detail": f"하위 {pos}% (저점권 {_POS52_MAX}% 이하 아님)"})

    bbl = (ind.get("bollinger") or {}).get("lower")
    if not bbl:
        checks.append({"name": "볼린저", "ok": False, "detail": "데이터 없음"})
    elif price <= bbl:
        checks.append({"name": "볼린저", "ok": True, "detail": f"하단({bbl}) 이탈 ✓"})
    else:
        checks.append({"name": "볼린저", "ok": False, "detail": f"하단({bbl}) 위 (미이탈)"})

    st = ind.get("stochastic_k")
    if st is None:
        checks.append({"name": "스토캐스틱", "ok": False, "detail": "데이터 없음"})
    elif st <= _STOCH_MAX:
        checks.append({"name": "스토캐스틱", "ok": True, "detail": f"{st} ≤ {_STOCH_MAX} 과매도 ✓"})
    else:
        checks.append({"name": "스토캐스틱", "ok": False, "detail": f"{st} (과매도 {_STOCH_MAX} 이하 아님)"})

    fib = lv.get("fib_retracement") or {}
    fib_ok = False
    fib_detail = "깊은 되돌림(61/78%) 지지 안 닿음"
    for label in ("61%", "78%"):   # 깊은 되돌림 지지
        lvl = fib.get(label)
        if lvl and abs(price - lvl) / price <= _FIB_NEAR:
            fib_ok = True
            fib_detail = f"{label} 지지({lvl}) 근접 ✓"
            break
    checks.append({"name": "피보지지", "ok": fib_ok, "detail": fib_detail})

    passed = [c for c in checks if c["ok"]]
    score = len(passed)
    is_balmok = score >= min_score
    reason = (
        f"{score}/5 신호 충족 → 발목(저점권)" if is_balmok
        else f"{score}/5 신호만 충족(기준 {min_score}개) → 발목 아님"
    )

    return {
        "symbol": feed.get("symbol"),
        "name": feed.get("name"),
        "price": price,
        "change_pct": feed.get("change_pct"),
        "rsi": rsi,
        "pos52": pos,
        "score": score,
        "is_balmok": is_balmok,
        "verdict": "발목(저점권)" if is_balmok else "발목 아님",
        "reason": reason,
        "checks": checks,
        "signals": [c["detail"] for c in passed],  # 하위호환
    }


def scan(registry, min_score: int = 2) -> dict:
    """워치리스트(국내) 전 종목 발목 스캔.

    - items: score≥min_score (발목 판정). 점수 내림차순.
    - ranking: 스캔된 전 종목을 52주 하위 위치(저점 근접) 순으로 정렬한 표.
      신호가 안 잡혀도 "어디까지 떨어졌는지"를 항상 보여줘 빈 화면을 막는다.
    """
    all_rows = []
    errors = []
    for sym in settings.kr_symbols:
        try:
            rows = registry.history(sym, "1y")
            quote = registry.quote(sym)
            feed = compute_feed(sym, rows, quote,
                                registry.fundamentals(sym), registry.investor_flow(sym))
            d = detect(feed, min_score)
            if d:
                all_rows.append(d)
            else:
                errors.append(sym)
        except Exception as exc:  # 어떤 종목이 왜 빠졌는지 표시용
            errors.append(f"{sym}({exc})")
            continue

    items = [d for d in all_rows if d["score"] >= min_score]
    items.sort(key=lambda x: (-x["score"], x.get("pos52") if x.get("pos52") is not None else 999))
    # 저점 근접 순위(52주 하위 %가 낮을수록 위). pos52 없는 건 뒤로.
    ranking = sorted(all_rows, key=lambda x: x.get("pos52") if x.get("pos52") is not None else 999)
    return {
        "ok": True,
        "items": items,
        "ranking": ranking,
        "min_score": min_score,
        "scanned": len(all_rows),
        "errors": errors,
    }
