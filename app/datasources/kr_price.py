"""국내 시세/이력 — FinanceDataReader 기반.

대상: 삼성전자(005930), SK하이닉스(000660).
출처/네트워크 실패 시 예외를 올리지 않고 placeholder quote 를 반환한다.
"""
from __future__ import annotations

import time

from app.datasources.base import PriceSource, TTLCache, empty_quote

# 종목 메타 (이름/표시 비고). 코드에 없는 심볼은 코드 자체를 이름으로 사용.
KR_META: dict[str, dict[str, str]] = {
    "005930": {"name": "삼성전자", "note": "코스피"},
    "000660": {"name": "SK하이닉스", "note": "코스피"},
    "0193W0": {"name": "KODEX 삼성전자레버리지", "note": "레버리지 2X"},
    "0193T0": {"name": "KODEX SK하이닉스레버리지", "note": "레버리지 2X"},
}

# period(대시보드 약식) -> 가져올 영업일 수 대략치
_PERIOD_DAYS = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 372,
}


# KRX 전체 종목명 캐시(코드→이름). FinanceDataReader StockListing 으로 1회 적재.
_KRX_NAMES: dict[str, str] = {}
_KRX_LOADED = False


def _load_krx_names() -> None:
    """KRX 전 종목 코드→이름 맵을 1회 적재(실패해도 조용히 빈 맵)."""
    global _KRX_LOADED
    if _KRX_LOADED:
        return
    _KRX_LOADED = True  # 재시도 폭주 방지: 시도 자체는 1회만
    try:
        import FinanceDataReader as fdr  # type: ignore
        df = fdr.StockListing("KRX")
        # 컬럼명이 버전마다 'Code'/'Symbol', 'Name' 등으로 다를 수 있어 방어적으로 매핑.
        cols = {c.lower(): c for c in df.columns}
        code_col = cols.get("code") or cols.get("symbol")
        name_col = cols.get("name")
        if code_col and name_col:
            for code, name in zip(df[code_col], df[name_col]):
                c = str(code).strip()
                if c.isdigit():
                    c = c.zfill(6)
                if c and isinstance(name, str) and name.strip():
                    _KRX_NAMES[c] = name.strip()
    except Exception:
        pass
    if not _KRX_NAMES:
        # 해외 VPS 등에서 KRX 리스트 접근이 막히면 배포본에 동봉한 스냅샷으로 폴백.
        try:
            import json
            from pathlib import Path
            snap = Path(__file__).with_name("krx_names.json")
            if snap.exists():
                _KRX_NAMES.update(json.loads(snap.read_text(encoding="utf-8")))
        except Exception:
            pass


def search_names(query: str, limit: int = 10) -> list[dict]:
    """종목명 부분일치 검색 → [{symbol, name}]. 공백 무시, 정확일치 우선."""
    q = (query or "").strip().replace(" ", "").lower()
    if not q:
        return []
    if not _KRX_LOADED:
        _load_krx_names()
    pool: dict[str, str] = dict(_KRX_NAMES)
    for code, meta in KR_META.items():
        pool.setdefault(code, meta["name"])
    if q.isdigit():
        hits = [{"symbol": c, "name": n} for c, n in pool.items() if c.startswith(q)]
        return sorted(hits, key=lambda x: x["symbol"])[:limit]
    exact: list[dict] = []
    starts: list[dict] = []
    contains: list[dict] = []
    for code, name in pool.items():
        n = name.replace(" ", "").lower()
        if n == q:
            exact.append({"symbol": code, "name": name})
        elif n.startswith(q):
            starts.append({"symbol": code, "name": name})
        elif q in n:
            contains.append({"symbol": code, "name": name})
    # 짧은 이름 우선(예: '삼성' → 삼성전자가 삼성바이오로직스보다 앞에)
    by_len = lambda x: (len(x["name"]), x["name"])
    ranked = exact + sorted(starts, key=by_len) + sorted(contains, key=by_len)
    return ranked[:limit]


def resolve_name(symbol: str) -> str:
    """심볼 → 표시용 종목명. 하드코딩 메타 > KRX 리스트 > 심볼 순."""
    if symbol in KR_META:
        return KR_META[symbol]["name"]
    if not _KRX_LOADED:
        _load_krx_names()
    return _KRX_NAMES.get(symbol) or _KRX_NAMES.get(str(symbol).zfill(6)) or symbol


def _meta(symbol: str) -> dict[str, str]:
    if symbol in KR_META:
        return KR_META[symbol]
    return {"name": resolve_name(symbol), "note": "코스피/코스닥"}


class KrPriceSource(PriceSource):
    """FinanceDataReader 로 국내 시세를 조회한다."""

    def __init__(self, cache_ttl: float = 45.0) -> None:
        self._cache = TTLCache(cache_ttl)
        # FinanceDataReader 는 import 비용이 있어 지연 로딩한다.
        self._fdr = None
        self._import_error = ""

    def _reader(self):
        if self._fdr is None and not self._import_error:
            try:
                import FinanceDataReader as fdr  # type: ignore
                self._fdr = fdr
            except Exception as exc:  # 라이브러리 미설치 등
                self._import_error = f"FinanceDataReader 불가: {exc}"
        return self._fdr

    def get_quote(self, symbol: str) -> dict:
        meta = _meta(symbol)
        cache_key = f"quote:{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        fdr = self._reader()
        if fdr is None:
            return empty_quote(symbol, meta["name"], "KRW", meta["note"], self._import_error)

        try:
            # 최근 7영업일이면 전일 종가 + 현재가 산출에 충분.
            df = fdr.DataReader(symbol)
            df = df.tail(7)
            if df is None or df.empty:
                raise ValueError("빈 데이터")
            closes = df["Close"].tolist()
            price = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) >= 2 else price
            change_pct = ((price - prev_close) / prev_close * 100.0) if prev_close else 0.0
            quote = {
                "symbol": symbol,
                "name": meta["name"],
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "currency": "KRW",
                "note": meta["note"],
                "ts": int(time.time()),
                "error": "",
            }
            self._cache.set(cache_key, quote)
            return quote
        except Exception as exc:
            return empty_quote(symbol, meta["name"], "KRW", meta["note"], f"조회 실패: {exc}")

    def get_history(self, symbol: str, period: str) -> list[dict]:
        cache_key = f"hist:{symbol}:{period}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        fdr = self._reader()
        if fdr is None:
            return []

        try:
            days = _PERIOD_DAYS.get(period, 93)
            df = fdr.DataReader(symbol)
            df = df.tail(days)
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
