"""미국 시세/지수/선물 — yfinance 기반.

대상: 마이크론(MU), 필라델피아 반도체지수(^SOX), 나스닥 선물(NQ=F).
출처/네트워크 실패 시 예외를 올리지 않고 placeholder quote 를 반환한다.
"""
from __future__ import annotations

import time

from app.datasources.base import PriceSource, TTLCache, empty_quote

# 심볼 메타 (이름/통화/표시 비고).
US_META: dict[str, dict[str, str]] = {
    "MU": {"name": "마이크론", "currency": "USD", "note": "나스닥"},
    "^SOX": {"name": "필라델피아 반도체지수", "currency": "USD", "note": "지수"},
    "NQ=F": {"name": "나스닥 선물", "currency": "USD", "note": "선물"},
}

# 대시보드 약식 period -> yfinance period 그대로 사용 가능
_VALID_PERIODS = {"1mo", "3mo", "6mo", "1y"}


def _meta(symbol: str) -> dict[str, str]:
    return US_META.get(symbol, {"name": symbol, "currency": "USD", "note": ""})


class UsMarketSource(PriceSource):
    """yfinance 로 미국 시세/지수/선물을 조회한다."""

    def __init__(self, cache_ttl: float = 45.0) -> None:
        self._cache = TTLCache(cache_ttl)
        self._yf = None
        self._import_error = ""

    def _client(self):
        if self._yf is None and not self._import_error:
            try:
                import yfinance as yf  # type: ignore
                self._yf = yf
            except Exception as exc:
                self._import_error = f"yfinance 불가: {exc}"
        return self._yf

    def get_quote(self, symbol: str) -> dict:
        meta = _meta(symbol)
        cache_key = f"quote:{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        yf = self._client()
        if yf is None:
            return empty_quote(symbol, meta["name"], meta["currency"], meta["note"], self._import_error)

        try:
            # 최근 5일 일봉이면 전일 종가 + 최신가 산출에 충분. timeout 으로 무한 대기 방지.
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="5d", interval="1d", timeout=8)
            if df is None or df.empty:
                raise ValueError("빈 데이터")
            closes = df["Close"].dropna().tolist()
            if not closes:
                raise ValueError("종가 없음")
            price = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) >= 2 else price
            change_pct = ((price - prev_close) / prev_close * 100.0) if prev_close else 0.0
            quote = {
                "symbol": symbol,
                "name": meta["name"],
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "currency": meta["currency"],
                "note": meta["note"],
                "ts": int(time.time()),
                "error": "",
            }
            self._cache.set(cache_key, quote)
            return quote
        except Exception as exc:
            return empty_quote(symbol, meta["name"], meta["currency"], meta["note"], f"조회 실패: {exc}")

    def get_history(self, symbol: str, period: str) -> list[dict]:
        cache_key = f"hist:{symbol}:{period}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        yf = self._client()
        if yf is None:
            return []

        try:
            use_period = period if period in _VALID_PERIODS else "3mo"
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=use_period, interval="1d", timeout=8)
            if df is None or df.empty:
                return []
            rows: list[dict] = []
            for idx, row in df.iterrows():
                rows.append(
                    {
                        "date": idx.strftime("%Y-%m-%d"),
                        "open": _f(row.get("Open")),
                        "high": _f(row.get("High")),
                        "low": _f(row.get("Low")),
                        "close": _f(row.get("Close")),
                        "volume": _f(row.get("Volume")),
                    }
                )
            self._cache.set(cache_key, rows)
            return rows
        except Exception:
            return []


def _f(value) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 4)
    except Exception:
        return None
