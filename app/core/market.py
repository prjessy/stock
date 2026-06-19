"""장 세션 판정 (KST).

사용자 정의 세션:
    프리장   08:00 ~ 09:00
    본장     09:00 ~ 15:40
    에프터장 15:40 ~ 20:00
    그 외 = 장마감

순수 '시각'만으로 판정한다(요일/주말 구분 없음 — 미국 선물 등은 주말에도 거래되므로
실시간 폴링 자체는 세션과 무관하게 항상 돈다). 경계 시각은 settings(.env)에서 조절 가능,
겹치는 구간은 본장 우선. 대시보드 세션 배지와 알림 데몬 가동 시간대 안내에 쓰인다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings

KST = timezone(timedelta(hours=9))


def _to_minutes(hhmm: str) -> int:
    """'HH:MM' -> 자정 기준 분. 파싱 실패 시 -1."""
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def current_session(now: datetime | None = None) -> dict:
    """현재 세션 정보.

    반환: {session, label, open, now}
        session: pre | regular | after | closed
        label:   프리장 | 본장 | 에프터장 | 장마감
        open:    거래 가능 시간대 여부(bool)
        now:     판정에 사용한 KST 시각 'HH:MM'
    """
    now = now or datetime.now(KST)
    mins = now.hour * 60 + now.minute
    pre = _to_minutes(settings.session_pre_open)
    reg = _to_minutes(settings.session_regular_open)
    reg_close = _to_minutes(settings.session_regular_close)
    after_close = _to_minutes(settings.session_after_close)

    if pre <= mins < reg:
        s = ("pre", "프리장", True)
    elif reg <= mins < reg_close:
        s = ("regular", "본장", True)
    elif reg_close <= mins < after_close:
        s = ("after", "에프터장", True)
    else:
        s = ("closed", "장마감", False)

    return {"session": s[0], "label": s[1], "open": s[2], "now": now.strftime("%H:%M")}


def is_market_open(now: datetime | None = None) -> bool:
    """프리/본/에프터 중 하나면 True."""
    return current_session(now)["open"]
