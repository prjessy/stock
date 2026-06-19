"""오늘(당일) 분봉 — yfinance N분 간격.

대시보드의 '1일·5분' 차트용. 국내 코드는 `<코드>.KS` 티커로 조회한다.
무료 소스라 ~15분 지연이 있을 수 있으나, 당일 5분봉 흐름 파악에는 충분하다.
실패 시 빈 리스트를 반환한다(부분 실패 격리).

반환: [{t(epoch sec, UTC), open, high, low, close, volume}, ...] (오래된→최신)
"""
from __future__ import annotations

from app.datasources.base import TTLCache

# 분봉은 자주 안 바뀌므로 60초 캐시(yfinance 과호출 방지).
_cache = TTLCache(60.0)


def get_intraday(symbol: str, interval: str = "5m") -> list[dict]:
    key = f"{symbol}:{interval}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    try:
        import yfinance as yf  # 지연 임포트
    except Exception:
        return []

    yf_sym = f"{symbol}.KS" if symbol.isdigit() else symbol
    try:
        df = yf.Ticker(yf_sym).history(period="1d", interval=interval)
        if df is None or df.empty:
            return []
        rows: list[dict] = []
        for idx, row in df.iterrows():
            try:
                t = int(idx.timestamp())
            except Exception:
                continue
            rows.append(
                {
                    "t": t,
                    "open": _f(row.get("Open")),
                    "high": _f(row.get("High")),
                    "low": _f(row.get("Low")),
                    "close": _f(row.get("Close")),
                    "volume": _f(row.get("Volume")),
                }
            )
        _cache.set(key, rows)
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
