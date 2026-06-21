"""더듬이4 — 테마별(조선·방산·원전·반도체·반도체 소부장) '신규 매수세 유입' 종목 탐지.

신규 유입 기준: 당일 순매수>0 인데 직전(5일합−당일)≤0 → 최근엔 안 사다 오늘 매수 전환.
외국인·기관 각각 검사. 알림(alert_watch)·화면(API) 양쪽에서 공유한다.
예외는 올리지 않고 빈 결과로 처리한다. 잘못된 코드는 조회 실패→건너뜀(안전).

⚠️ 종목 코드는 사용자 확인 필요 — 틀리거나 빠진 게 있으면 THEMES 만 고치면 된다.
"""
from __future__ import annotations

import time

_THROTTLE = 0.15   # KIS 초당 호출 제한 회피
_SURGE_MULT = 2.0  # 급증 판정: 당일 순매수 ≥ 직전 4일 일평균 × 이 배수

# 테마별 대표 종목 [(코드, 이름)]. 필요 시 여기만 수정.
THEMES: dict[str, list[tuple[str, str]]] = {
    "조선": [
        ("009540", "HD한국조선해양"), ("329180", "HD현대중공업"), ("010140", "삼성중공업"),
        ("042660", "한화오션"), ("010620", "HD현대미포"),
    ],
    "방산": [
        ("012450", "한화에어로스페이스"), ("047810", "한국항공우주"), ("079550", "LIG넥스원"),
        ("064350", "현대로템"), ("272210", "한화시스템"),
    ],
    "원전": [
        ("034020", "두산에너빌리티"), ("052690", "한전기술"), ("051600", "한전KPS"),
        ("015760", "한국전력"), ("100090", "삼강엠앤티"),
    ],
    "반도체": [
        ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("000990", "DB하이텍"),
    ],
    "반도체 소부장": [
        ("042700", "한미반도체"), ("240810", "원익IPS"), ("058470", "리노공업"),
        ("039030", "이오테크닉스"), ("403870", "HPSP"), ("036930", "주성엔지니어링"),
        ("005290", "동진쎄미켐"),
    ],
}


def scan_inflow(registry, etf_code: str | None = None, limit: int | None = None) -> dict:
    """테마 대표 종목을 훑어 신규 매수세 유입 종목 목록 반환(테마 태그 포함).

    반환 {ok, scanned, items:[{code,name,theme,who,qty,reason}], note}. etf_code/limit 인자는 호환용(무시).
    reason: "신규편입"(안 사다 오늘 매수 전환) | "급증매수"(평소보다 급증).
    """
    items = []
    scanned = 0
    for theme, stocks in THEMES.items():
        for code, name in stocks:
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
                if not day or day <= 0 or tot is None:
                    continue
                prior4 = tot - day            # 직전 4일 순매수 합
                if prior4 <= 0:
                    reason = "신규편입"        # 안 사다 오늘 매수 전환
                elif day >= _SURGE_MULT * (prior4 / 4):
                    reason = "급증매수"        # 평소(직전 4일 평균)보다 2배+ 매수
                else:
                    continue                  # 평범한 지속 매수 — 제외
                items.append({"code": code, "name": name, "theme": theme,
                              "who": who, "qty": int(day), "reason": reason})
                break
    if scanned == 0:
        return {"ok": False, "scanned": 0, "items": [], "note": "테마 종목 없음"}
    items.sort(key=lambda x: x["qty"], reverse=True)
    return {"ok": True, "scanned": scanned, "items": items, "note": ""}
