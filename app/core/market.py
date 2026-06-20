"""장 세션 판정 (KST).

사용자 정의 세션(평일 한정):
    프리장   08:00 ~ 09:00
    본장     09:00 ~ 15:40
    에프터장 15:40 ~ 20:00
    그 외 = 휴장
    토·일·공휴일 = 시각과 무관하게 휴장

경계 시각은 settings(.env)에서 조절 가능, 겹치는 구간은 본장 우선. 공휴일은 환경변수
KR_HOLIDAYS(YYYY-MM-DD 쉼표구분)로 덮어쓸 수 있고, 없으면 아래 기본 목록(매년 갱신).
대시보드 세션 배지와 알림 데몬 가동 시간대 안내에 쓰인다.

주의: 실시간 폴링 자체는 세션과 무관하게 항상 돈다(미국 선물은 주말에도 거래). 여기서는
'표시용 한국장 세션'만 판정한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.config import settings

KST = timezone(timedelta(hours=9))

# 2026 한국 증시 휴장일(공휴일·연말폐장). 매년 갱신 필요. 환경변수 KR_HOLIDAYS 로 덮어쓰기 가능.
_DEFAULT_HOLIDAYS = {
    "2026-01-01",  # 신정
    "2026-02-16", "2026-02-17", "2026-02-18",  # 설날 연휴
    "2026-03-02",  # 삼일절 대체공휴일(3/1 일)
    "2026-05-05",  # 어린이날
    "2026-05-25",  # 부처님오신날 대체공휴일(5/24 일)
    "2026-06-06",  # 현충일(토)
    "2026-08-17",  # 광복절 대체공휴일(8/15 토)
    "2026-09-24", "2026-09-25",  # 추석 연휴(9/26 토)
    "2026-09-28",  # 추석 대체공휴일
    "2026-10-05",  # 개천절 대체공휴일(10/3 토)
    "2026-10-09",  # 한글날
    "2026-12-25",  # 성탄절
    "2026-12-31",  # 연말 폐장
}


def _holidays() -> set[str]:
    env = os.getenv("KR_HOLIDAYS")
    if env:
        return {x.strip() for x in env.split(",") if x.strip()}
    return _DEFAULT_HOLIDAYS


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
        label:   프리장 | 본장 | 에프터장 | 휴장
        open:    거래 가능 시간대 여부(bool)
        now:     판정에 사용한 KST 시각 'HH:MM'
    """
    now = now or datetime.now(KST)
    # 토(5)·일(6)·공휴일 = 시각 무관 휴장
    if now.weekday() >= 5 or now.strftime("%Y-%m-%d") in _holidays():
        return {"session": "closed", "label": "휴장", "open": False, "now": now.strftime("%H:%M")}

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
        s = ("closed", "휴장", False)

    return {"session": s[0], "label": s[1], "open": s[2], "now": now.strftime("%H:%M")}


def is_market_open(now: datetime | None = None) -> bool:
    """프리/본/에프터 중 하나면 True."""
    return current_session(now)["open"]


# 2026 미국 증시 휴장일(ET 기준). 환경변수 US_HOLIDAYS 로 덮어쓰기 가능.
_US_HOLIDAYS = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (관측, 7/4 토)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}


def _us_holidays() -> set[str]:
    env = os.getenv("US_HOLIDAYS")
    if env:
        return {x.strip() for x in env.split(",") if x.strip()}
    return _US_HOLIDAYS


def us_session(now_utc: datetime | None = None) -> dict:
    """미국 증시 세션(ET, 서머타임 자동). NQ=F 등 미국 자산 배지용.

    ET 기준 시각·요일로 판정하므로 KST 날짜 밀림(미국 주말이 KST 토~월)이 자연히 반영된다.
        프리장   04:00 ~ 09:30 ET
        정규장   09:30 ~ 16:00 ET
        애프터장 16:00 ~ 20:00 ET
        토·일·공휴일·그 외 = 휴장
    타임존 데이터 부재 시 안전 기본값 반환(500 금지).
    """
    try:
        from zoneinfo import ZoneInfo
        et = (now_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return {"session": "unknown", "label": "미국 —", "open": False, "now": ""}

    nowtxt = et.strftime("%H:%M ET")
    if et.weekday() >= 5 or et.strftime("%Y-%m-%d") in _us_holidays():
        return {"session": "closed", "label": "휴장", "open": False, "now": nowtxt}

    mins = et.hour * 60 + et.minute
    if 4 * 60 <= mins < 9 * 60 + 30:
        s = ("pre", "프리장", True)
    elif 9 * 60 + 30 <= mins < 16 * 60:
        s = ("regular", "정규장", True)
    elif 16 * 60 <= mins < 20 * 60:
        s = ("after", "애프터장", True)
    else:
        s = ("closed", "휴장", False)
    return {"session": s[0], "label": s[1], "open": s[2], "now": nowtxt}


def us_futures_open(now_utc: datetime | None = None) -> bool:
    """미국 지수 선물(NQ=F 등) CME 글로벡스 거래 여부.

    일요일 18:00 ET 개장 ~ 금요일 17:00 ET 마감(주중 거의 24시간). 토요일·일요일 낮·
    금요일 야간은 휴장. (월~목 17:00~18:00 일일 정산휴식은 단순화 위해 무시.)
    """
    try:
        from zoneinfo import ZoneInfo
        et = (now_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return False
    wd, mins = et.weekday(), et.hour * 60 + et.minute  # Mon=0..Sun=6
    if wd == 5:        # 토: 종일 휴장
        return False
    if wd == 6:        # 일: 18:00 ET 개장
        return mins >= 18 * 60
    if wd == 4:        # 금: 17:00 ET 마감
        return mins < 17 * 60
    return True         # 월~목
