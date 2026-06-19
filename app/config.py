"""설정 로딩 (.env 기반).

secrets/환경 종속 값은 모두 .env 에서 읽는다 (하드코딩 금지).
로컬·VPS 어디서나 .env 만 바꾸면 동작하도록 한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# .env 를 1회 로드 (없으면 OS 환경변수/기본값 사용)
load_dotenv()


def _split_csv(raw: str) -> list[str]:
    """콤마 구분 문자열 -> 공백 제거된 항목 리스트."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_floats(raw: str) -> list[float]:
    return [float(item) for item in _split_csv(raw)]


@dataclass(frozen=True)
class Settings:
    """앱 전역 설정. 불변 객체로 두어 실수 변경 방지."""

    # 감시 대상
    kr_symbols: list[str] = field(
        default_factory=lambda: ["005930", "0193W0", "000660", "0193T0"]
    )
    us_symbols: list[str] = field(default_factory=lambda: ["MU", "^SOX", "NQ=F"])

    # 임계값 (전일 종가 대비 %). 설정만 바꿔 확장 가능 (예: +6/-6).
    thresholds: list[float] = field(default_factory=lambda: [3.0, -3.0])

    # 폴링 / 시장 시간 (KST)
    poll_interval_seconds: int = 60
    market_open: str = "09:00"
    market_close: str = "15:30"
    briefing_time: str = "07:00"

    # 접근 제어: 허용된 Telegram chat id 목록
    allowed_chat_ids: list[str] = field(default_factory=list)

    # Hermes 게이트웨이 (localhost 내부 통신)
    hermes_base_url: str = "http://localhost:8080"

    # 저장소
    db_path: str = "data/stock.db"


def load_settings() -> Settings:
    """환경변수에서 Settings 를 구성한다. 값이 없으면 기본값 사용."""
    return Settings(
        kr_symbols=_split_csv(os.getenv("KR_SYMBOLS", "005930,0193W0,000660,0193T0")),
        us_symbols=_split_csv(os.getenv("US_SYMBOLS", "MU,^SOX,NQ=F")),
        thresholds=_parse_floats(os.getenv("THRESHOLDS", "3.0,-3.0")),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "15:30"),
        briefing_time=os.getenv("BRIEFING_TIME", "07:00"),
        allowed_chat_ids=_split_csv(os.getenv("ALLOWED_CHAT_IDS", "")),
        hermes_base_url=os.getenv("HERMES_BASE_URL", "http://localhost:8080"),
        db_path=os.getenv("DB_PATH", "data/stock.db"),
    )


# 모듈 임포트 시점에 1회 구성하여 공유한다.
settings = load_settings()
