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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.datasources.financials import get_fundamentals
from app.datasources.registry import SourceRegistry

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="주식 대시보드", docs_url=None, redoc_url=None)

# 출처 디스패처 (각 소스의 TTL 캐시 내장) — 앱 수명 동안 재사용.
_registry = SourceRegistry()


@app.get("/api/quotes")
def get_quotes(fresh: bool = False) -> JSONResponse:
    """워치리스트 전체 시세. 개별 실패는 해당 항목 error 로만 표시.

    fresh=1 이면 캐시를 비우고 출처에서 새로 받아온다(강제 동기화).
    """
    if fresh:
        _registry.clear_caches()
    return JSONResponse({"quotes": _registry.all_quotes()})


@app.get("/api/history/{symbol}")
def get_history(symbol: str, period: str = "3mo", fresh: bool = False) -> JSONResponse:
    """단일 심볼 OHLC 이력. 실패 시 빈 리스트. fresh=1 이면 캐시 우회."""
    if fresh:
        _registry.clear_caches()
    history = _registry.history(symbol, period)
    return JSONResponse({"symbol": symbol, "period": period, "history": history})


@app.get("/api/fundamentals/{symbol}")
def get_fundamentals_api(symbol: str) -> JSONResponse:
    """단일 심볼 재무 요약(PER/PBR/시총/배당 등). 국내 보통주만 지원.

    ETF/선물 등 미지원 심볼은 {available: false} 로 응답하며 절대 500 을 내지 않는다.
    """
    return JSONResponse(get_fundamentals(symbol))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# 정적 파일(css/js) 마운트. index 는 위 라우트가 우선.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
