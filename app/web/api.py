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

from fastapi import FastAPI, Request
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

# 더듬이 2·3 자동 감시(본장 중, 트리거 시 Claude 판단→사이렌). interval=0 이면 비활성.
from app.analysis.deudeumi_scheduler import DeudeumiScheduler
_deudeumi = DeudeumiScheduler(_registry, _poller, settings.deudeumi_interval_min)

# 마케팅 자료 자동 최신화(하루 1회, 장마감 후). 뉴스 수집→Claude 요약/카피→data/marketing.json.
from app.analysis.marketing_scheduler import MarketingScheduler
_marketing = MarketingScheduler()

# 미국 브리핑 자동 생성(하루 1회, 새벽 6시). 미국 지수/뉴스→Claude 브리핑→data/briefing.json.
from app.analysis.briefing_scheduler import BriefingScheduler
_briefing = BriefingScheduler(_registry)


@app.on_event("startup")
def _start_poller() -> None:
    _poller.start()
    _deudeumi.start()
    _marketing.start()
    _briefing.start()


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


@app.get("/api/investor/{symbol}")
def get_investor_api(symbol: str) -> JSONResponse:
    """투자자별 수급(외국인/기관/개인 순매수 수량, 당일·5일합). 국내 종목만. 500 금지."""
    try:
        flow = _registry.investor_flow(symbol)
    except Exception:
        flow = None
    if not flow:
        return JSONResponse({"symbol": symbol, "name": _name_for(symbol), "available": False})
    return JSONResponse({"symbol": symbol, "name": _name_for(symbol), "available": True, **flow})


@app.get("/api/briefing")
def get_briefing_api() -> JSONResponse:
    """저장된 미국 브리핑(개장 전 참고). 500 금지."""
    from app.analysis.briefing import load
    try:
        return JSONResponse(load())
    except Exception:
        return JSONResponse({"available": False})


@app.post("/api/briefing/refresh")
def refresh_briefing_api() -> JSONResponse:
    """미국 브리핑 즉시 재생성(수동). Claude 호출 발생. 500 금지."""
    from app.analysis.briefing import generate
    try:
        return JSONResponse({"ok": True, **generate(_registry)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/marketing")
def get_marketing_api() -> JSONResponse:
    """저장된 마케팅 자료(종목별 뉴스 요약·카피·헤드라인). 500 금지."""
    from app.analysis.marketing import load
    try:
        return JSONResponse(load())
    except Exception:
        return JSONResponse({"available": False})


@app.post("/api/marketing/refresh")
def refresh_marketing_api() -> JSONResponse:
    """마케팅 자료 즉시 재생성(수동 새로고침 버튼). Claude 호출 발생. 500 금지."""
    from app.analysis.marketing import generate
    try:
        data = generate()
        return JSONResponse({"ok": True, **data})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/orderbook/{symbol}")
def get_orderbook_api(symbol: str) -> JSONResponse:
    """호가(매도/매수 10단계 + 잔량). 국내 종목만. 장중 실시간. 500 금지."""
    try:
        ob = _registry.orderbook(symbol)
    except Exception:
        ob = None
    if not ob:
        return JSONResponse({"symbol": symbol, "name": _name_for(symbol), "available": False})
    return JSONResponse({"name": _name_for(symbol), "available": True, **ob})


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
        supply = _registry.investor_flow(symbol)
    except Exception as exc:
        return JSONResponse({"symbol": symbol, "error": f"데이터 조회 실패: {exc}"})
    return JSONResponse(compute_feed(symbol, rows, quote, fund, supply))


@app.get("/api/deudeumi-ai/{symbol}")
def get_deudeumi_ai_api(symbol: str) -> JSONResponse:
    """더듬이 2·3 — Claude 기반 매수/매도 시점 판단(판단 보조). 절대 500 금지."""
    from app.analysis.deudeumi_ai import analyze, evaluate_signals, recent_signals
    from app.analysis.feed import compute_feed
    try:
        rows = _registry.history(symbol, "1y")
        quote = next((x for x in _poller.quotes() if x.get("symbol") == symbol), None)
        if quote is None:
            quote = _registry.source_for(symbol).get_quote(symbol)
        fund = _registry.fundamentals(symbol)
        supply = _registry.investor_flow(symbol)
        feed = compute_feed(symbol, rows, quote, fund, supply)
        accuracy = evaluate_signals(symbol, _registry)
    except Exception as exc:
        return JSONResponse({"symbol": symbol, "error": f"데이터 조회 실패: {exc}"})
    return JSONResponse(analyze(symbol, feed, recent_signals(symbol), accuracy))


@app.get("/api/deudeumi-signals/{symbol}")
def get_deudeumi_signals_api(symbol: str) -> JSONResponse:
    """더듬이 신호 기록 + 정확도(진화 로그). 절대 500 금지."""
    from app.analysis.deudeumi_ai import evaluate_signals
    try:
        return JSONResponse(evaluate_signals(symbol, _registry))
    except Exception as exc:
        return JSONResponse({"symbol": symbol, "error": str(exc)})


@app.post("/api/notify")
async def notify_api(request: Request) -> JSONResponse:
    """브라우저 알림 → 텔레그램 전달(hermes send). 절대 500 금지."""
    import os
    import subprocess
    try:
        data = await request.json()
    except Exception:
        data = {}
    msg = (data.get("message") or "").strip()
    subject = data.get("subject") or "🔔 Stock Watchdog"
    if not msg:
        return JSONResponse({"ok": False, "error": "empty"})
    try:
        subprocess.run(
            ["/usr/local/bin/hermes", "send", "--to", "telegram", "--subject", subject, msg],
            env={**os.environ, "HERMES_HOME": os.environ.get("HERMES_HOME", "/root/.hermes")},
            timeout=30, capture_output=True,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


_BOT_LINK: dict = {}


@app.get("/api/bot-link")
def bot_link_api() -> JSONResponse:
    """텔레그램 봇 t.me 링크(받는 사람이 /start 누를 곳). getMe 결과 캐시. 절대 500 금지."""
    import os
    if _BOT_LINK.get("link"):
        return JSONResponse(_BOT_LINK)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        try:
            hpath = os.path.join(os.environ.get("HERMES_HOME", "/root/.hermes"), ".env")
            with open(hpath, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    if not token:
        return JSONResponse({"link": None, "error": "봇 토큰 미설정"})
    try:
        import requests
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8).json()
        u = (r.get("result") or {}).get("username")
        if u:
            _BOT_LINK.update({"link": f"https://t.me/{u}", "username": u})
            return JSONResponse(_BOT_LINK)
        return JSONResponse({"link": None, "error": "getMe 실패"})
    except Exception as exc:
        return JSONResponse({"link": None, "error": str(exc)})


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
        from app.core.market import us_session
        data = current_session()
        data["us"] = us_session()
        return JSONResponse(data)
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
