"""알림 데몬 스케줄러 (APScheduler 기반).

매 `settings.poll_interval_seconds`(기본 60초)마다 알림 1패스를 돌린다.
단, 한국 정규장 시간(settings.market_open ~ market_close, KST) + 평일에만 실행한다.
장 시간 밖/주말 틱은 조용히 건너뛴다.

견고성(AC-6): 한 틱의 예외가 루프를 죽이지 않도록 콜백 전체를 try/except 로 감싼다.

한계: 공휴일/임시휴장 처리는 범위 밖(평일+장시간만 본다). KIS 실시간 전환 시 보강 예정.
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import settings
from app.core import alerts
from app.datasources.registry import SourceRegistry
from app.notify.base import Notifier
from app.storage.db import Repository

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")


def _parse_hhmm(value: str) -> dtime:
    """'09:00' -> datetime.time."""
    hh, mm = value.split(":")
    return dtime(int(hh), int(mm))


def is_market_open(now: datetime) -> bool:
    """KST 평일 + 정규장 시간 안인지 판정. (공휴일은 미고려.)"""
    if now.weekday() >= 5:  # 5=토, 6=일
        return False
    open_t = _parse_hhmm(settings.market_open)
    close_t = _parse_hhmm(settings.market_close)
    return open_t <= now.time() <= close_t


def make_tick(repo: Repository, notifier: Notifier, registry: SourceRegistry):
    """스케줄러가 매 주기 호출할 콜백을 만든다."""

    def tick() -> None:
        now = datetime.now(_KST)
        if not is_market_open(now):
            logger.debug("장 시간 외/주말 — 틱 건너뜀 (%s)", now.isoformat())
            return
        try:
            sent = alerts.run_once(repo, notifier, registry)
            if sent:
                logger.info("알림 %d건 발송", sent)
        except Exception:  # 한 틱 실패가 루프를 죽이지 않게.
            logger.exception("틱 처리 실패 — 루프는 계속 진행")

    return tick


def run(repo: Repository, notifier: Notifier, registry: SourceRegistry) -> None:
    """블로킹 스케줄러를 기동한다(Ctrl+C 까지 실행)."""
    scheduler = BlockingScheduler(timezone=_KST)
    scheduler.add_job(
        make_tick(repo, notifier, registry),
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="price_alert_poll",
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "알림 데몬 시작: %d초 간격, 장 시간 %s~%s KST",
        settings.poll_interval_seconds,
        settings.market_open,
        settings.market_close,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("알림 데몬 종료")
