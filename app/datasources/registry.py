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

    def all_quotes(self) -> list[dict]:
        """워치리스트 전체 시세. 개별 실패는 해당 항목 error 로만 표시(전체 중단 없음)."""
        return [self.source_for(sym).get_quote(sym) for sym in self.watchlist()]

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

    def clear_caches(self) -> None:
        """강제 동기화: 모든 소스의 TTL 캐시 비우기(다음 조회는 출처에서 새로 받음)."""
        for src in (self._kr, self._us):
            cache = getattr(src, "_cache", None)
            if cache is not None:
                cache.clear()
