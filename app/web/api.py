"""대시보드 FastAPI 앱.

- GET /                         -> 대시보드 HTML (static/index.html)
- GET /api/quotes              -> 워치리스트 전체 시세 JSON
- GET /api/history/{symbol}    -> 차트용 OHLC 이력 JSON
- GET /api/fundamentals/{symbol} -> 재무 요약 JSON(국내 보통주 한정)

데이터소스 실패는 격리한다(부분 데이터 반환, 전체 500 금지).
출처 라우팅은 datasources/registry.py 의 SourceRegistry 에 위임한다.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.market import current_session
from app.datasources.intraday import get_intraday
from app.datasources.kr_price import KR_META
from app.datasources.registry import SourceRegistry
from app.datasources.us_market import US_META
from app.storage.db import Repository
from app.web.realtime import RealtimePoller

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="주식 대시보드", docs_url=None, redoc_url=None)

# GitHub Pages(정적 프론트)가 이 백엔드 /api 를 폴링할 수 있도록 CORS 허용.
# 키는 서버에만 있으므로 프론트 출처를 열어도 시크릿은 노출되지 않는다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# 출처 디스패처 (각 소스의 TTL 캐시 내장) — 앱 수명 동안 재사용.
_registry = SourceRegistry()

# 실시간 백그라운드 폴러 — 시세를 메모리에 미리 받아두어 /api/quotes 가 즉시 응답.
_poller = RealtimePoller(_registry)


@app.on_event("startup")
def _start_poller() -> None:
    _poller.start()


def _name_for(symbol: str) -> str:
    """심볼 -> 표시용 종목명. 메타에 없으면 심볼 자체를 사용."""
    meta = KR_META.get(symbol) or US_META.get(symbol)
    return meta["name"] if meta else symbol


@app.get("/api/quotes")
def get_quotes(fresh: bool = False) -> JSONResponse:
    """워치리스트 전체 시세 — 백그라운드 폴러의 메모리 스냅샷에서 즉시 반환.

    폴러가 아직 시드 전이거나 빈 경우에만 출처에서 직접 받아 폴백한다.
    """
    quotes = _poller.quotes()
    if not quotes:
        quotes = _registry.all_quotes()
    return JSONResponse({"quotes": quotes})


@app.get("/api/history/{symbol}")
def get_history(symbol: str, period: str = "3mo", fresh: bool = False) -> JSONResponse:
    """단일 심볼 OHLC 이력. 실패 시 빈 리스트. fresh=1 이면 캐시 우회."""
    if fresh:
        _registry.clear_caches()
    history = _registry.history(symbol, period)
    return JSONResponse({"symbol": symbol, "period": period, "history": history})


@app.get("/api/intraday/{symbol}")
def get_intraday_api(symbol: str, interval: str = "5m") -> JSONResponse:
    """오늘 분봉(기본 5분). 실패 시 빈 rows. 5분 흐름 표용."""
    return JSONResponse({"symbol": symbol, "interval": interval, "rows": get_intraday(symbol, interval)})


@app.get("/api/fundamentals/{symbol}")
def get_fundamentals_api(symbol: str) -> JSONResponse:
    """단일 심볼 재무 요약(PER/PBR/시총/배당 등). 국내 보통주만 지원.

    ETF/선물 등 미지원 심볼은 {available: false} 로 응답하며 절대 500 을 내지 않는다.
    국내 보통주는 KIS(실시간) 우선, 실패 시 무료 소스로 폴백한다.
    """
    return JSONResponse(_registry.fundamentals(symbol))


@app.get("/api/feed/{symbol}")
def get_feed_api(symbol: str) -> JSONResponse:
    """분석 피드 — hermes(Claude)용 지표 묶음(MA/RSI/볼린저/MACD/스토캐스틱/ATR/거래량/피보/52주/밸류).

    더듬이 2·3가 읽는 엔드포인트. 절대 500 을 내지 않는다.
    """
    from app.analysis.feed import compute_feed
    try:
        rows = _registry.history(symbol, "1y")
        quote = next((x for x in _poller.quotes() if x.get("symbol") == symbol), None)
        if quote is None:
            quote = _registry.source_for(symbol).get_quote(symbol)
        fund = _registry.fundamentals(symbol)
    except Exception as exc:
        return JSONResponse({"symbol": symbol, "error": f"데이터 조회 실패: {exc}"})
    return JSONResponse(compute_feed(symbol, rows, quote, fund))


@app.get("/api/alerts")
def get_alerts(limit: int = 50) -> JSONResponse:
    """최근 감지된 가격 알림(최신순). 종목명은 메타로 보강.

    DB 접근 실패 시에도 절대 500 을 내지 않고 빈 목록을 반환한다.
    """
    try:
        repo = Repository()
        try:
            rows = repo.recent_alerts(limit)
        finally:
            repo.close()
        alerts = [
            {
                "trade_date": r.get("trade_date"),
                "symbol": r.get("symbol"),
                "name": _name_for(r.get("symbol", "")),
                "threshold": r.get("threshold"),
                "fired_at": r.get("fired_at"),
            }
            for r in rows
        ]
        return JSONResponse({"alerts": alerts})
    except Exception:
        return JSONResponse({"alerts": []})


@app.get("/api/session")
def get_session() -> JSONResponse:
    """현재 장 세션(프리장/본장/에프터장/장마감). 대시보드 배지용.

    판정 실패 시에도 500 을 내지 않고 안전한 기본값을 반환한다.
    """
    try:
        return JSONResponse(current_session())
    except Exception:
        return JSONResponse({"session": "closed", "label": "—", "open": False, "now": ""})


@app.get("/api/settings")
def get_settings() -> JSONResponse:
    """현재 감시 설정(워치리스트/임계값/폴링 주기/장 시간). 읽기 전용 표시용.

    설정 로딩 실패 시에도 안전한 기본값을 반환한다(500 금지).
    """
    try:
        watchlist = [
            {"symbol": sym, "name": _name_for(sym)} for sym in _registry.watchlist()
        ]
        return JSONResponse(
            {
                "watchlist": watchlist,
                "thresholds": list(settings.thresholds),
                "poll_interval_seconds": settings.poll_interval_seconds,
                "market_open": settings.market_open,
                "market_close": settings.market_close,
                "session": current_session(),
                "realtime": bool(settings.kis_app_key and settings.kis_app_secret),
            }
        )
    except Exception:
        return JSONResponse(
            {
                "watchlist": [],
                "thresholds": [3.0, -3.0],
                "poll_interval_seconds": 60,
                "market_open": "08:00",
                "market_close": "20:00",
                "session": {"session": "closed", "label": "—", "open": False, "now": ""},
                "realtime": False,
            }
        )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    # 브라우저가 루트 /favicon.ico 를 자동 요청 — 32px PNG 로 응답(캐시 강함).
    return FileResponse(STATIC_DIR / "icon-32.png")


# 정적 파일(css/js) 마운트. index 는 위 라우트가 우선.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
