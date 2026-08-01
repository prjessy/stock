"""미국 시세/지수/선물 — yfinance 기반.

대상: 마이크론(MU), 필라델피아 반도체지수(^SOX), 나스닥 선물(NQ=F).
출처/네트워크 실패 시 예외를 올리지 않고 placeholder quote 를 반환한다.
"""
from __future__ import annotations

import time

from app.datasources.base import PriceSource, TTLCache, empty_quote

# 심볼 메타 (이름/통화/표시 비고).
US_META: dict[str, dict[str, str]] = {
    "MU": {"name": "마이크론 테크놀로지", "currency": "USD", "note": "나스닥"},
    "^SOX": {"name": "필라델피아 반도체지수", "currency": "USD", "note": "지수"},
    "^IXIC": {"name": "나스닥 종합지수", "currency": "USD", "note": "지수"},
    "^DJI": {"name": "다우존스 산업평균", "currency": "USD", "note": "지수"},
    "NQ=F": {"name": "나스닥 선물", "currency": "USD", "note": "선물"},
}

# 대시보드 약식 period -> yfinance period 그대로 사용 가능
_VALID_PERIODS = {"1mo", "3mo", "6mo", "1y"}

# 한글 별칭 → 티커 (서학개미 인기 종목; 이름으로 추가/자동완성용)
US_KR_ALIASES: dict[str, str] = {
    "애플": "AAPL", "마이크로소프트": "MSFT", "엔비디아": "NVDA", "테슬라": "TSLA",
    "아마존": "AMZN", "알파벳": "GOOGL", "구글": "GOOGL", "메타": "META",
    "넷플릭스": "NFLX", "브로드컴": "AVGO", "인텔": "INTC", "마이크론": "MU",
    "퀄컴": "QCOM", "팔란티어": "PLTR", "코인베이스": "COIN", "스트래티지": "MSTR",
    "마이크로스트래티지": "MSTR", "버크셔해서웨이": "BRK-B", "일라이릴리": "LLY",
    "존슨앤존슨": "JNJ", "제이피모건": "JPM", "JP모건": "JPM", "비자": "V",
    "마스터카드": "MA", "코카콜라": "KO", "펩시": "PEP", "맥도날드": "MCD",
    "스타벅스": "SBUX", "나이키": "NKE", "월마트": "WMT", "코스트코": "COST",
    "디즈니": "DIS", "보잉": "BA", "록히드마틴": "LMT", "엑슨모빌": "XOM",
    "셰브론": "CVX", "화이자": "PFE", "오라클": "ORCL", "세일즈포스": "CRM",
    "어도비": "ADBE", "페이팔": "PYPL", "우버": "UBER", "에어비앤비": "ABNB",
    "스노우플레이크": "SNOW", "크라우드스트라이크": "CRWD", "슈퍼마이크로": "SMCI",
    "아이온큐": "IONQ", "리게티": "RGTI", "조비": "JOBY", "리비안": "RIVN",
    "루시드": "LCID", "소파이": "SOFI", "로빈후드": "HOOD", "델": "DELL",
    "텍사스인스트루먼트": "TXN", "티에스엠씨": "TSM", "대만반도체": "TSM",
    "알리바바": "BABA", "쿠팡": "CPNG", "홈디포": "HD", "프록터앤갬블": "PG",
    "애브비": "ABBV", "머크": "MRK", "골드만삭스": "GS", "모건스탠리": "MS",
    "뱅크오브아메리카": "BAC", "블랙록": "BLK", "노보노디스크": "NVO",
    "존슨콘트롤즈": "JCI", "포드": "F", "제너럴모터스": "GM", "지엠": "GM",
    "아이비엠": "IBM", "에이엠디": "AMD",
}

# 미국 전 종목 티커→영문명 스냅샷(us_names.json, NASDAQ+NYSE+S&P500). 1회 적재.
_US_NAMES: dict[str, str] = {}
_US_LOADED = False


def _load_us_names() -> None:
    global _US_LOADED
    if _US_LOADED:
        return
    _US_LOADED = True
    try:
        import json
        from pathlib import Path
        snap = Path(__file__).with_name("us_names.json")
        if snap.exists():
            _US_NAMES.update(json.loads(snap.read_text(encoding="utf-8")))
    except Exception:
        pass


def search_names(query: str, limit: int = 10) -> list[dict]:
    """미국 종목 이름/티커 검색 → [{symbol, name}]. 한글 별칭 > 티커 일치 > 영문명 순."""
    q = (query or "").strip().replace(" ", "").lower()
    if not q:
        return []
    _load_us_names()
    out: list[dict] = []
    seen: set[str] = set()

    def _push(tick: str, name: str) -> None:
        if tick not in seen:
            seen.add(tick)
            out.append({"symbol": tick, "name": name})

    # 1) 한글 별칭 + 내장 메타의 한글 이름
    for alias, tick in US_KR_ALIASES.items():
        if q in alias.lower():
            eng = _US_NAMES.get(tick) or US_META.get(tick, {}).get("name") or tick
            _push(tick, f"{alias} ({eng})" if eng != alias else alias)
    for tick, meta in US_META.items():
        name = str(meta.get("name") or tick)
        if q in name.replace(" ", "").lower():
            _push(tick, name)
    # 2) 티커 자체 일치(정확 > 접두)
    qu = q.upper()
    if qu in _US_NAMES:
        _push(qu, _US_NAMES[qu])
    # 3) 영문명 부분일치(짧은 이름 우선)
    starts: list[tuple[str, str]] = []
    contains: list[tuple[str, str]] = []
    for tick, name in _US_NAMES.items():
        n = name.replace(" ", "").lower()
        if n.startswith(q):
            starts.append((tick, name))
        elif q in n:
            contains.append((tick, name))
    for tick, name in sorted(starts, key=lambda x: (len(x[1]), x[1])) + sorted(contains, key=lambda x: (len(x[1]), x[1])):
        if len(out) >= limit:
            break
        _push(tick, name)
    return out[:limit]


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
            # 최근 5일 일봉으로 전일 종가 확보. timeout 으로 무한 대기 방지.
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="5d", interval="1d", timeout=8)
            if df is None or df.empty:
                raise ValueError("빈 데이터")
            closes = df["Close"].dropna().tolist()
            if not closes:
                raise ValueError("종가 없음")
            price = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) >= 2 else price
            # 장중 실시간성: fast_info 의 최신가가 있으면 그것으로 교체(일봉은 잘 안 움직임).
            # 전일종가도 fast_info 값이 더 정확하면 사용. 실패해도 일봉값 유지(graceful).
            try:
                fi = ticker.fast_info
                lp = float(fi.get("last_price") or fi.get("lastPrice") or 0)
                pc = float(fi.get("previous_close") or fi.get("previousClose") or 0)
                if lp > 0:
                    price = lp
                if pc > 0:
                    prev_close = pc
            except Exception:
                pass
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
