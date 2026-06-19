"""데이터소스 인터페이스.

가격/이력 출처(국내 FinanceDataReader, 미국 yfinance 등)는 이 인터페이스로 추상화한다.
출처가 막히거나 실패해도 예외를 올리지 않고 price=None + error 를 담은 dict 를 반환하여
대시보드가 항상 렌더되도록 한다(부분 실패 격리).
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod


class PriceSource(ABC):
    """시세/이력 조회 추상 인터페이스."""

    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """현재가 1건 조회.

        반환 dict 키:
            symbol, name, price, prev_close, change_pct, currency, ts(epoch sec)
            실패 시 price/prev_close/change_pct=None, error 키 포함.
        """
        raise NotImplementedError

    @abstractmethod
    def get_history(self, symbol: str, period: str) -> list[dict]:
        """OHLC 이력 조회.

        반환: [{date, open, high, low, close, volume}, ...] (오래된 → 최신)
        실패 시 빈 리스트.
        """
        raise NotImplementedError


def empty_quote(symbol: str, name: str, currency: str, note: str = "", error: str = "") -> dict:
    """가격을 못 구했을 때 쓰는 placeholder quote dict."""
    return {
        "symbol": symbol,
        "name": name,
        "price": None,
        "prev_close": None,
        "change_pct": None,
        "currency": currency,
        "note": note,
        "ts": int(time.time()),
        "error": error,
    }


class TTLCache:
    """아주 작은 in-process TTL 캐시. 반복 새로고침이 출처를 과도하게 호출하지 않도록 한다."""

    def __init__(self, ttl_seconds: float = 45.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        """강제 동기화용: 캐시 비우기."""
        self._store.clear()
