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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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

# 서버측 알림 감시 — 브라우저 없이도(폰 꺼도) 증감·목표금액 도달 시 텔레그램+카카오 발송.
from app.core.alert_watch import AlertWatcher
_alert_watch = AlertWatcher(_poller)

# 더듬이 2·3 자동 감시(본장 중, 트리거 시 Claude 판단→사이렌). interval=0 이면 비활성.
from app.analysis.deudeumi_scheduler import DeudeumiScheduler
_deudeumi = DeudeumiScheduler(_registry, _poller, settings.deudeumi_interval_min)

# 찰떡 써머리 자동 최신화(하루 1회, 오전 9시 장 시작). 뉴스 수집→Claude 요약→data/marketing.json.
from app.analysis.marketing_scheduler import MarketingScheduler
_marketing = MarketingScheduler()

# 미국 브리핑 자동 생성(하루 1회, 새벽 6시). 미국 지수/뉴스→Claude 브리핑→data/briefing.json.
from app.analysis.briefing_scheduler import BriefingScheduler
_briefing = BriefingScheduler(_registry)


@app.on_event("startup")
def _start_poller() -> None:
    _poller.start()
    _alert_watch.start()
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
    """브라우저 알림 → 텔레그램(hermes) + 카카오 나에게(직접). 절대 500 금지."""
    from app.notify.dispatch import notify_all
    try:
        data = await request.json()
    except Exception:
        data = {}
    msg = (data.get("message") or "").strip()
    subject = data.get("subject") or "🔔 Stock Watchdog"
    if not msg:
        return JSONResponse({"ok": False, "error": "empty"})
    return JSONResponse({"ok": True, **notify_all(subject, msg)})


@app.post("/api/order")
async def order_api(request: Request) -> JSONResponse:
    """수동 테스트 주문(1주 시장가). 실거래 — 1주 하드캡(settings.trade_max_qty). 500 금지.

    side: 'buy'|'sell', symbol: 종목코드. 자동매매가 아니라 사용자가 직접 누르는 단발 주문.
    """
    from app.trading.kis_order import OrderClient
    try:
        data = await request.json()
    except Exception:
        data = {}
    # 🔒 주문 비밀번호 검증 — 사이트가 공개돼 있어도 비번 없으면 주문 거부.
    from app.config import settings as _cfg
    if (data.get("password") or "") != _cfg.trade_password:
        return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다 — 주문 권한 없음"})
    symbol = (data.get("symbol") or "").strip()
    side = data.get("side")
    try:
        price = float(data.get("price") or 0)  # >0=지정가, 0/없음=시장가
    except Exception:
        price = 0
    if not symbol or side not in ("buy", "sell"):
        return JSONResponse({"ok": False, "error": "symbol/side 필요"})
    if not (price > 0):  # 시장가 차단 — 수동 테스트는 지정가만 허용.
        return JSONResponse({"ok": False, "error": "지정가(price) 필수 — 시장가 주문은 막아두었습니다"})
    src = _registry.kr_source()
    if not hasattr(src, "_ensure_token"):
        return JSONResponse({"ok": False, "error": "KIS 주문 소스 없음(키 미설정)"})
    res = OrderClient(src).place_order(symbol, side, 1, price)  # 수동 테스트 1주(지정가)
    try:  # 주문 접수 결과를 텔레그램+카카오로도 통지
        from app.notify.dispatch import notify_all
        tag = "✅ 접수" if res.get("ok") else "❌ 실패"
        notify_all("🧪 수동 주문 테스트",
                   f"{tag} · {symbol} {side} 1주\n{res.get('msg') or res.get('error') or ''}")
    except Exception:
        pass
    return JSONResponse(res)


@app.get("/api/order/status")
def order_status_api() -> JSONResponse:
    """주문 기능 사용 가능 여부(계좌 설정·모의/실전). 500 금지."""
    src = _registry.kr_source()
    from app.config import settings as _s
    return JSONResponse({
        "configured": bool(_s.kis_cano) and hasattr(src, "_ensure_token"),
        "paper": _s.kis_paper,
        "max_qty": _s.trade_max_qty,
        "auto_enabled": _s.trade_enabled,
    })


@app.get("/api/order/history")
def order_history_api(days: int = 7) -> JSONResponse:
    """최근 N일 체결/미체결 주문 내역(자동매매봇 탭). 500 금지."""
    from app.trading.kis_order import OrderClient
    src = _registry.kr_source()
    if not hasattr(src, "_ensure_token"):
        return JSONResponse({"ok": False, "error": "KIS 주문 소스 없음(키 미설정)"})
    return JSONResponse(OrderClient(src).list_orders(days=days))


@app.get("/api/alert-config")
def get_alert_config_api() -> JSONResponse:
    """서버 저장 알림 설정(종류·증감 임계값·목표금액). 500 금지."""
    from app.core import alert_config
    try:
        return JSONResponse(alert_config.load())
    except Exception:
        return JSONResponse({"types": [], "pct_thresholds": [], "targets": {}})


@app.post("/api/alert-config")
async def set_alert_config_api(request: Request) -> JSONResponse:
    """알림 설정 저장(부분 갱신). 저장된 전체 설정 반환. 500 금지."""
    from app.core import alert_config
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        return JSONResponse(alert_config.save(data))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/kakao/status")
def kakao_status_api() -> JSONResponse:
    """카카오 연동 상태(키 설정 여부·로그인 완료 여부). 500 금지."""
    from app.kakao_notify import configured, linked
    try:
        return JSONResponse({"configured": configured(), "linked": linked()})
    except Exception:
        return JSONResponse({"configured": False, "linked": False})


@app.get("/api/kakao/login")
def kakao_login_api():
    """카카오 로그인 동의 페이지로 리다이렉트(최초 1회 연동)."""
    from app.kakao_notify import authorize_url, configured
    if not configured():
        return JSONResponse({"ok": False, "error": "KAKAO_REST_API_KEY 미설정"})
    return RedirectResponse(authorize_url())


@app.get("/api/kakao/test")
def kakao_test_api() -> JSONResponse:
    """카카오 나에게 보내기 테스트 1건. 500 금지."""
    from app.kakao_notify import linked, send
    try:
        if not linked():
            return JSONResponse({"ok": False, "error": "미연동"})
        return JSONResponse({"ok": bool(send("🔔 Stock Watchdog 카카오 테스트입니다. 연동 정상!"))})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/kakao/callback")
def kakao_callback_api(code: str = "", error: str = "", error_description: str = "") -> HTMLResponse:
    """카카오 OAuth 콜백 → code 를 토큰으로 교환·저장."""
    from app.kakao_notify import exchange_code
    if error or not code:
        # 실패 시 카카오가 준 사유를 화면에 노출(code 없으므로 민감정보 아님). 로그엔 code 안 남김.
        return HTMLResponse(
            f"<h3>카카오 연동 실패</h3>"
            f"<pre>error: {error or '(없음)'}\n"
            f"error_description: {error_description or '(없음)'}\n"
            f"code: 없음</pre>"
            f"<p>창을 닫고 다시 시도하세요. (Redirect URI·동의항목 설정 확인)</p>"
        )
    r = exchange_code(code)
    if r.get("ok"):
        return HTMLResponse("<h2>✅ 카카오 연동 완료</h2><p>이제 알림이 카톡(나와의 채팅)으로도 옵니다. 이 창을 닫으세요.</p>")
    return HTMLResponse(f"<h3>연동 실패</h3><pre>{r.get('error')}</pre><p>동의항목(카카오톡 메시지 전송)·Redirect URI 설정을 확인하세요.</p>")


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
