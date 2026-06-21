"""더듬이4 — ETF(기본 KODEX 200) 구성종목 중 '신규 매수세 유입' 종목 탐지.

신규 유입 기준: 당일 순매수>0 인데 직전(5일합−당일)≤0 → 최근엔 안 사다 오늘 매수 전환.
외국인·기관 각각 검사. 알림(alert_watch)·화면(API) 양쪽에서 공유한다.
예외는 올리지 않고 빈 결과로 처리한다.
"""
from __future__ import annotations

import time

_DEFAULT_ETF = "069500"  # KODEX 200
_THROTTLE = 0.15         # KIS 초당 호출 제한 회피(폴러와 겹쳐도 여유)


def scan_inflow(registry, etf_code: str = _DEFAULT_ETF, limit: int = 60) -> dict:
    """구성종목을 훑어 신규 매수세 유입 종목 목록을 반환.

    반환 {ok, etf, scanned, items:[{code,name,who,qty}], note}. items 는 외인/기관 신규 유입.
    """
    constituents = registry.etf_constituents(etf_code)
    if not constituents:
        return {"ok": False, "etf": etf_code, "scanned": 0, "items": [],
                "note": "구성종목 없음(휴장/장개시 전이거나 ETF 코드 미지원)"}
    items = []
    scanned = 0
    for code, name in constituents[:limit]:
        scanned += 1
        try:
            flow = registry.investor_flow(code)
        except Exception:
            flow = None
        time.sleep(_THROTTLE)
        if not flow:
            continue
        for who, day_key, sum_key in (("외국인", "frgn_ntby_qty", "frgn_ntby_sum"),
                                      ("기관", "orgn_ntby_qty", "orgn_ntby_sum")):
            day = flow.get(day_key)
            tot = flow.get(sum_key)
            if day and day > 0 and tot is not None and (tot - day) <= 0:
                items.append({"code": code, "name": name, "who": who, "qty": int(day)})
                break
    items.sort(key=lambda x: x["qty"], reverse=True)
    return {"ok": True, "etf": etf_code, "scanned": scanned, "items": items, "note": ""}
