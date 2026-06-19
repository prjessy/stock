"""재무 요약 — pykrx + FinanceDataReader 기반(국내 종목 한정, API 키 불필요).

대상: 삼성전자(005930), SK하이닉스(000660).

조회 전략(둘 다 무료·키 불필요):
  1) pykrx 로 PER/PBR/EPS/BPS/DIV(배당수익률)/DPS 와 시가총액을 시도.
     - get_market_fundamental_by_date / get_market_cap_by_date
  2) pykrx 의 KRX 통계 엔드포인트가 비어 있을 때(상황에 따라 빈 응답을 줌)는
     FinanceDataReader 의 KRX 상장목록 스냅샷에서 시가총액/발행주식수를 보강한다.

레버리지 ETF(0193W0/0193T0)·미국 선물(NQ=F)은 {available: false} 로 응답.
어떤 경우에도 예외를 올리지 않는다(대시보드가 항상 렌더되도록). 약 5분 TTL 캐시.
"""
from __future__ import annotations

import datetime as _dt

from app.datasources.base import TTLCache

# 재무 요약을 제공하는 국내 보통주 (이름 표시용)
_FUNDAMENTAL_SYMBOLS: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
}

# pykrx / FDR 는 import 비용이 있어 지연 로딩한다.
_stock = None
_stock_err = ""
_fdr = None
_fdr_err = ""
_cache = TTLCache(300.0)  # 5분


def _krx():
    """pykrx.stock 모듈 지연 로딩. 실패 시 None."""
    global _stock, _stock_err
    if _stock is None and not _stock_err:
        try:
            from pykrx import stock  # type: ignore
            _stock = stock
        except Exception as exc:
            _stock_err = f"pykrx 불가: {exc}"
    return _stock


def _reader():
    """FinanceDataReader 지연 로딩. 실패 시 None."""
    global _fdr, _fdr_err
    if _fdr is None and not _fdr_err:
        try:
            import FinanceDataReader as fdr  # type: ignore
            _fdr = fdr
        except Exception as exc:
            _fdr_err = f"FinanceDataReader 불가: {exc}"
    return _fdr


def _window() -> tuple[str, str]:
    """최근 약 10일 범위(YYYYMMDD). 마지막 영업일 스냅샷을 잡기 위함."""
    today = _dt.date.today()
    start = today - _dt.timedelta(days=10)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _num(value):
    """안전 변환: NaN/None -> None, 그 외 float. 0 도 None 취급(KRX 미집계)."""
    try:
        if value is None:
            return None
        f = float(value)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _last_row(df):
    if df is None or getattr(df, "empty", True):
        return None
    return df.iloc[-1]


def _from_pykrx(symbol: str) -> dict:
    """pykrx 로 재무지표/시총 시도. 비어 있으면 빈 값들로 채워 반환."""
    out = {"per": None, "pbr": None, "eps": None, "bps": None,
           "div": None, "dps": None, "market_cap": None, "asof": None}
    stock = _krx()
    if stock is None:
        return out
    start, end = _window()

    def g(row, key):
        try:
            if row is not None and key in row.index:
                v = _num(row[key])
                # KRX 미집계 항목은 0 으로 내려오므로 0 은 결측 처리.
                return None if v == 0 else v
        except Exception:
            return None
        return None

    try:
        fdf = stock.get_market_fundamental_by_date(start, end, symbol)
        frow = _last_row(fdf)
        if frow is not None:
            out.update(
                per=g(frow, "PER"), pbr=g(frow, "PBR"), eps=g(frow, "EPS"),
                bps=g(frow, "BPS"), div=g(frow, "DIV"), dps=g(frow, "DPS"),
            )
            out["asof"] = fdf.index[-1].strftime("%Y-%m-%d")
    except Exception:
        pass

    try:
        cdf = stock.get_market_cap_by_date(start, end, symbol)
        crow = _last_row(cdf)
        if crow is not None:
            out["market_cap"] = g(crow, "시가총액")
            out["asof"] = out["asof"] or cdf.index[-1].strftime("%Y-%m-%d")
    except Exception:
        pass

    return out


def _fill_marcap_from_fdr(symbol: str, data: dict) -> None:
    """시가총액이 비어 있으면 FDR KRX 상장목록 스냅샷에서 보강(실시간 종가 기준)."""
    if data.get("market_cap"):
        return
    fdr = _reader()
    if fdr is None:
        return
    try:
        listing = fdr.StockListing("KRX")
        row = listing[listing["Code"] == symbol]
        if not row.empty:
            data["market_cap"] = _num(row.iloc[0].get("Marcap"))
            if not data.get("asof"):
                data["asof"] = _dt.date.today().strftime("%Y-%m-%d")
    except Exception:
        pass


def get_fundamentals(symbol: str) -> dict:
    """단일 심볼 재무 요약.

    반환(성공): {available: True, symbol, name, per, pbr, eps, bps, div, dps,
                 market_cap, currency, asof}
    반환(미지원/실패): {available: False, symbol, reason}
    절대 예외를 올리지 않는다. 일부 지표가 비어도 시총만 있으면 available=True.
    """
    if symbol not in _FUNDAMENTAL_SYMBOLS:
        return {"available": False, "symbol": symbol, "reason": "ETF/선물 미지원"}

    cache_key = f"fund:{symbol}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    data = _from_pykrx(symbol)
    _fill_marcap_from_fdr(symbol, data)

    # 표시할 수치가 하나도 없으면 미지원 처리.
    has_any = any(data.get(k) for k in ("per", "pbr", "eps", "bps", "market_cap"))
    if not has_any:
        reason = _stock_err or _fdr_err or "재무 데이터를 가져오지 못했습니다"
        return {"available": False, "symbol": symbol, "reason": reason}

    result = {
        "available": True,
        "symbol": symbol,
        "name": _FUNDAMENTAL_SYMBOLS[symbol],
        "per": data["per"],
        "pbr": data["pbr"],
        "eps": data["eps"],
        "bps": data["bps"],
        "div": data["div"],
        "dps": data["dps"],
        "market_cap": data["market_cap"],
        "currency": "KRW",
        "asof": data["asof"],
    }
    _cache.set(cache_key, result)
    return result
