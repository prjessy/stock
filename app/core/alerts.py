"""가격 알림 서비스 (오케스트레이션).

1패스 흐름(AC-1):
  1) SourceRegistry 로 워치리스트 전체 시세 조회
  2) 각 시세에 대해 threshold_engine 으로 교차 임계값 산출
  3) dedupe 로 이미 발송한 (거래일, 종목, 임계값)은 제외
  4) 맥락 있는 메시지(R-18) 구성
  5) Notifier 로 전송 → 성공 시에만 mark_sent (실패 시 다음 주기 재시도)

부분 실패 격리(AC-6/AC-9): 한 종목/전송 실패가 나머지를 막지 않는다.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.core import dedupe
from app.core.threshold_engine import crossed_thresholds
from app.datasources.registry import SourceRegistry
from app.notify.base import Notifier
from app.storage.db import Repository

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_NQ_SYMBOL = "NQ=F"
_DELAY_FOOTER = "※ 데이터 약 15분 지연"


def _trade_date() -> str:
    """KST 기준 오늘 날짜(YYYY-MM-DD). 중복키의 '거래일' 성분."""
    return datetime.now(_KST).strftime("%Y-%m-%d")


def _nq_context(quotes: list[dict]) -> str:
    """나스닥 선물(NQ=F) 방향 한 줄. 시세 없으면 빈 문자열."""
    for q in quotes:
        if q.get("symbol") == _NQ_SYMBOL:
            pct = q.get("change_pct")
            if pct is None:
                return ""
            if pct > 0.1:
                mood = "프리장 강세"
            elif pct < -0.1:
                mood = "프리장 약세"
            else:
                mood = "프리장 보합"
            sign = "+" if pct >= 0 else ""
            return f"나스닥 선물 {sign}{pct}% ({mood})"
    return ""


def _format_price(price, currency: str) -> str:
    """통화에 맞춰 가격 표기. KRW 는 정수 천단위, 그 외 소수 2자리."""
    if price is None:
        return "-"
    if currency == "KRW":
        return f"{int(round(price)):,}원"
    return f"{price:,.2f}"


def compose_message(quote: dict, threshold: float, nq_context: str) -> str:
    """맥락 있는 알림 메시지(R-18) 구성.

    포함: 종목명/코드, 등락률, 현재가, 전일종가, 나스닥 선물 방향, 지연 고지.
    NQ=F 자기 자신 알림에는 나스닥 선물 맥락을 중복 표기하지 않는다.
    """
    pct = quote.get("change_pct")
    up = pct is not None and pct >= 0
    arrow = "▲" if up else "▼"
    marker = "🔴" if up else "🔵"  # 국내 관행: 상승=빨강, 하락=파랑
    sign = "+" if up else ""
    currency = quote.get("currency", "")

    lines = [
        f"{marker} {quote.get('name', quote.get('symbol'))} ({quote.get('symbol')}) "
        f"{arrow} {sign}{pct}%  [임계값 {threshold:+g}%]",
        f"현재가 {_format_price(quote.get('price'), currency)} "
        f"(전일 {_format_price(quote.get('prev_close'), currency)})",
    ]
    if quote.get("symbol") != _NQ_SYMBOL and nq_context:
        lines.append(nq_context)
    lines.append(_DELAY_FOOTER)
    return "\n".join(lines)


def run_once(repo: Repository, notifier: Notifier, registry: SourceRegistry) -> int:
    """알림 1패스 실행. 발송한 알림 수를 반환한다.

    각 종목/임계값은 try/except 로 감싸 한 건 실패가 전체를 막지 않게 한다(AC-6/AC-9).
    """
    trade_date = _trade_date()
    quotes = registry.all_quotes()
    nq_context = _nq_context(quotes)
    sent_count = 0

    for quote in quotes:
        symbol = quote.get("symbol", "?")
        try:
            change_pct = quote.get("change_pct")
            for threshold in crossed_thresholds(change_pct, settings.thresholds):
                if dedupe.already_sent(repo, trade_date, symbol, threshold):
                    continue
                message = compose_message(quote, threshold, nq_context)
                if not notifier.send_message(message):
                    # 전송 실패: mark_sent 하지 않음 → 다음 주기 재시도.
                    logger.warning("전송 실패, 다음 주기 재시도: %s @%s", symbol, threshold)
                    continue
                if dedupe.mark_sent(repo, trade_date, symbol, threshold):
                    sent_count += 1
        except Exception as exc:  # 한 종목 실패가 나머지를 막지 않도록 격리.
            logger.exception("알림 처리 중 오류(%s): %s", symbol, exc)

    return sent_count
