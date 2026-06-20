"""워치리스트 심볼 -> 담당 데이터소스 매핑(소형 디스패처).

API 가 워치리스트 전체를 출처에 상관없이 동일하게 순회할 수 있도록 한다.
추후 KIS 실시간 등 새 출처는 여기서만 연결하면 UI 변경 없이 교체 가능.
"""
from __future__ import annotations

from app.config import settings
from app.datasources.base import PriceSource
from app.datasources.kr_price import KrPriceSource
from app.datasources.us_market import UsMarketSource


def _make_kr_source() -> PriceSource:
    """국내 시세 출처 선택: KIS 키가 있으면 실시간(KIS), 없으면 무료 소스(FDR)로 폴백."""
    if settings.kis_app_key and settings.kis_app_secret:
        from app.datasources.kis_price import KisPriceSource
        return KisPriceSource()
    return KrPriceSource()


class SourceRegistry:
    """심볼별 PriceSource 라우팅. 출처 인스턴스(=TTL 캐시)는 앱 수명 동안 재사용."""

    def __init__(self) -> None:
        self._kr = _make_kr_source()
        self._us = UsMarketSource()
        self._kr_set = set(settings.kr_symbols)
        self._us_set = set(settings.us_symbols)

    def source_for(self, symbol: str) -> PriceSource:
        """심볼 -> 담당 데이터소스. 국내 코드면 KR, 그 외 US."""
        if symbol in self._kr_set:
            return self._kr
        if symbol in self._us_set:
            return self._us
        # 6자리 숫자면 국내로 간주, 아니면 미국.
        return self._kr if symbol.isdigit() else self._us

    def watchlist(self) -> list[str]:
        """대시보드 표시 순서: 국내 종목 먼저, 그다음 미국."""
        return list(settings.kr_symbols) + list(settings.us_symbols)

    def quote(self, symbol: str) -> dict:
        """단일 시세. 라이브 실패(가격 null) 시 일봉 마지막 종가로 폴백한다.

        휴장/주말/점검으로 실시간이 안 와도 '직전 종가'를 보여주기 위함.
        폴백 시 stale=True, asof=종가일자 를 달아 프론트가 '휴장·종가'로 표기한다.
        """
        src = self.source_for(symbol)
        q = src.get_quote(symbol)
        if q and q.get("price") is not None:
            # 시장이 닫혀 있으면 '종가(stale)' 표기 — 프론트가 '🕘 휴장·종가'로 표시.
            if src is self._us:
                # 미국: 선물=글로벡스, 그 외=정규장 기준.
                from app.core.market import us_futures_open, us_session
                open_now = us_futures_open() if symbol.endswith("=F") else us_session()["open"]
                if not open_now:
                    return {**q, "stale": True}
            elif src is self._kr:
                # 국내: 프리/본/에프터장 밖(주말·공휴일·장 종료)이면 KIS 가 마지막
                # 정규장 종가를 그대로 내려주므로, 실시간이 아님을 stale 로 표기한다.
                # (증권사 앱의 '시간외 단일가'와 값이 달라 보이는 혼란을 방지)
                from app.core.market import is_market_open
                if not is_market_open():
                    return {**q, "stale": True}
            return q
        try:
            rows = [r for r in (self.history(symbol, "1mo") or []) if r.get("close") is not None]
        except Exception:
            rows = []
        if rows:
            last = rows[-1]
            prev = rows[-2] if len(rows) >= 2 else last
            price = last["close"]
            prev_close = prev["close"]
            chg = ((price - prev_close) / prev_close * 100.0) if prev_close else 0.0
            return {**(q or {}), "symbol": symbol, "price": round(price, 2),
                    "prev_close": round(prev_close, 2), "change_pct": round(chg, 2),
                    "error": "", "stale": True, "asof": last.get("date")}
        return q

    def all_quotes(self) -> list[dict]:
        """워치리스트 전체 시세. 개별 실패는 해당 항목 error 로만 표시(전체 중단 없음)."""
        return [self.quote(sym) for sym in self.watchlist()]

    def history(self, symbol: str, period: str) -> list[dict]:
        return self.source_for(symbol).get_history(symbol, period)

    def fundamentals(self, symbol: str) -> dict:
        """재무 요약. 출처가 get_fundamentals 를 지원하면(=KIS) 그걸 우선 쓰고,
        실패/미지원이면 무료 소스(pykrx/FDR) 폴백으로 넘긴다."""
        src = self.source_for(symbol)
        fn = getattr(src, "get_fundamentals", None)
        if fn is not None:
            # KIS: 성공/실패 모두 즉시 반환. pykrx 폴백은 이 VPS(해외 IP)에서 KRX 차단으로
            # ~수십초 멈춤만 유발하므로 호출하지 않는다.
            return fn(symbol)
        from app.datasources.financials import get_fundamentals as _fallback
        return _fallback(symbol)

    def investor_flow(self, symbol: str) -> dict | None:
        """수급(외국인/기관 순매수). KIS 소스만 지원, 그 외 None."""
        fn = getattr(self.source_for(symbol), "get_investor_flow", None)
        return fn(symbol) if fn is not None else None

    def orderbook(self, symbol: str) -> dict | None:
        """호가(매도/매수 단계별 잔량). KIS 소스만 지원, 그 외 None."""
        fn = getattr(self.source_for(symbol), "get_orderbook", None)
        return fn(symbol) if fn is not None else None

    def clear_caches(self) -> None:
        """강제 동기화: 모든 소스의 TTL 캐시 비우기(다음 조회는 출처에서 새로 받음)."""
        for src in (self._kr, self._us):
            cache = getattr(src, "_cache", None)
            if cache is not None:
                cache.clear()
