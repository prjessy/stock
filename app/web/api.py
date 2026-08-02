"""대시보드 FastAPI 앱.

- GET /                         -> 대시보드 HTML (static/index.html)
- GET /api/quotes              -> 워치리스트 전체 시세 JSON
- GET /api/history/{symbol}    -> 차트용 OHLC 이력 JSON
- GET /api/fundamentals/{symbol} -> 재무 요약 JSON(국내 보통주 한정)

데이터소스 실패는 격리한다(부분 데이터 반환, 전체 500 금지).
출처 라우팅은 datasources/registry.py 의 SourceRegistry 에 위임한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.market import current_session
from app.datasources.intraday import get_intraday
from app.datasources.kr_price import KR_META, resolve_name as _kr_name
from app.datasources.registry import SourceRegistry
from app.datasources.us_market import US_META
from app.storage.db import Repository
from app.web.realtime import RealtimePoller

STATIC_DIR = Path(__file__).parent / "static"
# 배포 zip 은 정적폴더가 아닌 여기 둔다(=/static 직접다운 불가 → 비밀번호 라우트로만 내려감).
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

app = FastAPI(title="주식 대시보드", docs_url=None, redoc_url=None)

# GitHub Pages(정적 프론트)가 이 백엔드 /api 를 폴링할 수 있도록 CORS 허용.
# 키는 서버에만 있으므로 프론트 출처를 열어도 시크릿은 노출되지 않는다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# 관심종목은 사용자별로 DB(watchlist 테이블)에 저장한다. .env KR_SYMBOLS/US_SYMBOLS 는
# 신규 사용자 시드 + 비로그인 방문자에게 보여줄 기본 목록 역할만 한다(전역 watchlist.json 폐지).

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

# 자동매매 감시(등락률 밴드 매수/매도 + 손절·예약). 마스터 OFF면 아무것도 안 함(기본 OFF).
from app.trading.autotrade_watch import AutoTradeWatcher
_autotrade = AutoTradeWatcher(_registry, _poller)

# 손절 전용 자동 매도(손절가·손절%·예약시각). 매수만 없고 보유분 매도만. 마스터 OFF 기본.
from app.trading.autosell_watch import AutoSellWatcher
_autosell = AutoSellWatcher(_registry)

# 장 마감 AI 리포트(평일 15:40 자동 발송). 위험 0(주문 안 함).
from app.analysis.eod_report import EodScheduler
_eod = EodScheduler(_registry)

# 보유종목 데일리 케어(평일 09:30, AI 홀딩/손절/익절 추천). 추천만(주문 안 함).
from app.analysis.holdings_care import HoldingsCareScheduler
_care = HoldingsCareScheduler(_registry)

# 주간 복기 리포트(토요일 09:00, 이번주 매매·적중률 AI 복기). 복기만(주문 안 함).
from app.analysis.weekly_review import WeeklyReviewScheduler
_weekly = WeeklyReviewScheduler(_registry)


@app.on_event("startup")
def _start_poller() -> None:
    # 새 테이블(users/sessions/journal 등)을 idempotent 하게 보장. 웹앱은 데몬과 별개로 뜨므로
    # 여기서 init_db 를 호출하지 않으면 VPS DB 에 로그인/일지 테이블이 안 생긴다.
    try:
        from app.storage.db import init_db
        init_db(settings.db_path).close()
    except Exception:
        pass
    _poller.start()
    _alert_watch.start()
    _deudeumi.start()
    _marketing.start()
    _briefing.start()
    _autotrade.start()
    _autosell.start()
    _eod.start()
    _care.start()
    _weekly.start()


def _name_for(symbol: str) -> str:
    """심볼 -> 표시용 종목명. 메타에 없으면 심볼 자체를 사용."""
    meta = KR_META.get(symbol) or US_META.get(symbol)
    if meta:
        return meta["name"]
    # KIS 기본조회로 한글명(동적 추가 종목 · KRX 리스트 차단된 VPS 대비).
    try:
        src = _registry.source_for(symbol)
        nm = getattr(src, "get_name", lambda _s: None)(symbol)
        if nm:
            return nm
    except Exception:
        pass
    # 국내 코드면 KRX 리스트에서 이름 동적 조회(없으면 심볼 그대로).
    return _kr_name(symbol)


@app.get("/api/quotes")
def get_quotes(request: Request, fresh: bool = False, symbols: str = "") -> JSONResponse:
    """관심종목 시세 — 폴러 메모리 스냅샷에서 즉시 반환.

    - symbols=a,b,c 가 오면 그 목록을 쓴다(비로그인=브라우저 로컬저장 개인화용, 폴러에 임시 등록).
    - 없으면 로그인 사용자 DB 워치리스트(비로그인은 .env 기본)를 쓴다.
    스냅샷에 아직 없는 심볼(방금 추가 등)만 출처에서 직접 받아 폴백한다.
    """
    req = [s.strip() for s in symbols.split(",") if s.strip()][:60]  # 안전 상한 60
    if req:
        _poller.note_symbols(req)
        symbols = req
    else:
        symbols = [r["symbol"] for r in _user_watchlist(request)]
    quotes = _poller.quotes_for(symbols)
    if len(quotes) < len(symbols):
        have = {q.get("symbol") for q in quotes}
        for s in symbols:
            if s not in have:
                try:
                    quotes.append(_registry.quote(s))
                except Exception:
                    pass
        # 사용자 순서대로 정렬
        order = {s: i for i, s in enumerate(symbols)}
        quotes.sort(key=lambda q: order.get(q.get("symbol"), 1_000_000))
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
    """호가(매도/매수 10단계 + 잔량). 국내 종목만. 본장=J, 프리/애프터=NXT. 500 금지.

    레버리지 종목은 시간외(NXT)가 없어 본장만 조회한다(사용자 확인). 시간외에 본장 호가가
    비면 NXT 를 시도하고, 둘 다 없으면 available=False(+세션 안내).
    """
    from app.core.market import current_session
    sess = current_session()
    is_leverage = "레버리지" in (KR_META.get(symbol, {}).get("note", ""))
    # 프리/애프터 + 비레버리지면 NXT(시간외) 우선, 아니면 본장(J).
    want_nxt = sess["session"] in ("pre", "after") and not is_leverage
    primary = "NX" if want_nxt else "J"
    ob = None
    try:
        ob = _registry.orderbook(symbol, market_code=primary)
        if ob is None and primary == "NX":
            ob = _registry.orderbook(symbol, market_code="J")  # NXT 미운영 폴백
    except Exception:
        ob = None
    base = {"symbol": symbol, "name": _name_for(symbol),
            "session": sess["session"], "session_label": sess["label"]}
    if not ob:
        note = ""
        if sess["session"] in ("pre", "after") and is_leverage:
            note = "레버리지 종목은 시간외 호가가 없습니다(본장만)."
        elif sess["session"] == "closed":
            note = "휴장 — 호가 없음."
        return JSONResponse({**base, "available": False, "note": note})
    mc = ob.get("market_code", primary)
    ob["market_label"] = "시간외(NXT)" if mc == "NX" else "본장(KRX)"
    return JSONResponse({**base, "available": True, **ob})


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


@app.get("/api/alert-recipients")
def alert_recipients_api() -> JSONResponse:
    """시황 알람을 받는 지인 목록(id+이름). 표시·헷갈림 방지용. 500 금지."""
    try:
        from app.notify.dispatch import alert_recipients
        return JSONResponse({"ok": True, "recipients": alert_recipients()})
    except Exception:
        return JSONResponse({"ok": True, "recipients": []})


# 🔒 비밀번호 사전 검증 — 자동매매 탭 잠금 해제용. 실패 누적 시 잠시 차단(무차별 대입 방지).
_pw_fail: dict = {}  # ip -> {"n": 실패횟수, "until": 차단해제 epoch}


@app.post("/api/verify-password")
async def verify_password_api(request: Request) -> JSONResponse:
    """TRADE_PASSWORD 검증. 프론트 잠금은 눈속임이 되지 않도록 반드시 서버가 판정한다.
    미설정(.env 없음)이면 항상 거부(fail-closed). 5회 실패 시 60초 차단."""
    import time as _time
    from app.config import settings as _cfg
    ip = (request.client.host if request.client else "?")
    rec = _pw_fail.get(ip) or {"n": 0, "until": 0.0}
    now = _time.time()
    if now < rec["until"]:
        return JSONResponse({"ok": False, "error": f"시도 횟수 초과 — {int(rec['until'] - now) + 1}초 후 다시 시도하세요"})
    try:
        data = await request.json()
    except Exception:
        data = {}
    if _cfg.trade_password and (data.get("password") or "") == _cfg.trade_password:
        _pw_fail.pop(ip, None)
        return JSONResponse({"ok": True})
    rec["n"] += 1
    if rec["n"] >= 5:
        rec["until"] = now + 60
        rec["n"] = 0
    _pw_fail[ip] = rec
    return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다"})


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
    if not _cfg.trade_password or (data.get("password") or "") != _cfg.trade_password:
        return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다 — 주문 권한 없음(.env TRADE_PASSWORD 설정 필요)"})
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
def order_status_api(request: Request) -> JSONResponse:
    """주문 기능 사용 가능 여부(계좌 설정·모의/실전). 소유자만. 500 금지."""
    if not _is_owner(request):
        return JSONResponse({"configured": False, "owner_only": True})
    src = _registry.kr_source()
    from app.config import settings as _s
    return JSONResponse({
        "configured": bool(_s.kis_cano) and hasattr(src, "_ensure_token"),
        "paper": _s.kis_paper,
        "max_qty": _s.trade_max_qty,
        "auto_enabled": _s.trade_enabled,
    })


@app.get("/api/order/history")
def order_history_api(request: Request, days: int = 7) -> JSONResponse:
    """최근 N일 체결/미체결 주문 내역(자동매매봇 탭). 소유자(내 실계좌)만. 500 금지."""
    if not _is_owner(request):
        return JSONResponse({"ok": False, "owner_only": True, "error": "소유자만"})
    from app.trading.kis_order import OrderClient
    src = _registry.kr_source()
    if not hasattr(src, "_ensure_token"):
        return JSONResponse({"ok": False, "error": "KIS 주문 소스 없음(키 미설정)"})
    return JSONResponse(OrderClient(src).list_orders(days=days))


@app.get("/api/balance")
def balance_api(request: Request) -> JSONResponse:
    """현재 보유 종목(수량·평균단가·현재가·손익률). 소유자(내 실계좌)만 — 잔고 유출 방지. 500 금지."""
    if not _is_owner(request):
        return JSONResponse({"ok": False, "owner_only": True, "error": "소유자만 볼 수 있습니다"})
    from app.trading.kis_order import OrderClient
    src = _registry.kr_source()
    if not hasattr(src, "_ensure_token"):
        return JSONResponse({"ok": False, "error": "KIS 주문 소스 없음(키 미설정)"})
    return JSONResponse(OrderClient(src).get_balance())


@app.get("/api/autotrade/config")
def get_autotrade_config_api(request: Request) -> JSONResponse:
    """자동매매(등락률 밴드·손절·예약) 설정 조회. 소유자(내 실계좌)만. 500 금지."""
    if not _is_owner(request):
        return JSONResponse({"ok": False, "owner_only": True})
    from app.trading import autotrade_config
    return JSONResponse({"ok": True, **autotrade_config.load()})


@app.post("/api/autotrade/config")
async def set_autotrade_config_api(request: Request) -> JSONResponse:
    """자동매매 설정 저장. 실거래 자동화라 비밀번호 필수. 500 금지."""
    from app.config import settings as _cfg
    from app.trading import autotrade_config
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not _cfg.trade_password or (data.get("password") or "") != _cfg.trade_password:
        return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다(.env TRADE_PASSWORD 설정 필요)"})
    saved = autotrade_config.save({k: v for k, v in data.items() if k != "password"})
    return JSONResponse({"ok": True, **saved})


@app.get("/api/autosell/config")
def get_autosell_config_api(request: Request) -> JSONResponse:
    """손절 전용 자동 매도 설정 조회. 소유자만. 500 금지."""
    if not _is_owner(request):
        return JSONResponse({"ok": False, "owner_only": True})
    from app.trading import autosell_config
    return JSONResponse({"ok": True, **autosell_config.load()})


@app.post("/api/autosell/config")
async def set_autosell_config_api(request: Request) -> JSONResponse:
    """손절 전용 설정 저장. 실거래(자동 매도)라 비밀번호 필수. 500 금지."""
    from app.config import settings as _cfg
    from app.trading import autosell_config
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not _cfg.trade_password or (data.get("password") or "") != _cfg.trade_password:
        return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다(.env TRADE_PASSWORD 설정 필요)"})
    saved = autosell_config.save({k: v for k, v in data.items() if k != "password"})
    return JSONResponse({"ok": True, **saved})


@app.post("/api/autotrade/ai-judge")
async def autotrade_ai_judge_api(request: Request) -> JSONResponse:
    """기준값·예산 기반 매수 적정성 AI 판단(적정/위험/중립 + 권장 금액). 토큰비용·잠금탭이라 비번 필수. 500 금지."""
    from app.config import settings as _cfg
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not _cfg.trade_password or (data.get("password") or "") != _cfg.trade_password:
        return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다(.env TRADE_PASSWORD 설정 필요)"})
    symbol = (data.get("symbol") or "").strip()
    if not symbol:
        return JSONResponse({"ok": False, "error": "symbol 필요"})
    def _num(v):
        try:
            return float(v) if v not in (None, "") else None
        except Exception:
            return None
    base_price = _num(data.get("base_price"))
    budget = _num(data.get("budget"))
    try:
        from app.analysis.feed import compute_feed
        from app.trading.ai_advisor import judge
        rows = _registry.history(symbol, "1y")
        quote = _registry.quote(symbol)
        feed = compute_feed(symbol, rows, quote,
                            _registry.fundamentals(symbol), _registry.investor_flow(symbol))
        res = judge(symbol, feed, base_price, budget)
        if res.get("error"):
            return JSONResponse({"ok": False, **res})
        return JSONResponse({"ok": True, **res})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"판단 실패: {exc}"})


@app.get("/api/bottom-scan")
def bottom_scan_api(min_score: int = 2) -> JSONResponse:
    """발목(저점권) 감지 — 워치리스트 국내종목 스캔(온디맨드). 500 금지."""
    try:
        from app.analysis.bottom_detect import scan
        return JSONResponse(scan(_registry, min_score))
    except Exception as exc:
        return JSONResponse({"ok": False, "items": [], "error": str(exc)})


@app.get("/api/deudeumi4")
def deudeumi4_api(limit: int = 60, comment: int = 0) -> JSONResponse:
    """더듬이4 — 5개 테마 대표종목 중 외인·기관 신규/급증 매수(온디맨드). comment=1이면 AI 한줄. 500 금지."""
    try:
        from app.analysis.etf_flow import ai_comment, scan_inflow
        res = scan_inflow(_registry)
        if comment and res.get("ok") and res.get("items"):
            res["comment"] = ai_comment(res["items"])
        return JSONResponse(res)
    except Exception as exc:
        return JSONResponse({"ok": False, "items": [], "note": str(exc)})


# ===== 핀테크 탭 (금·원자재·환율 / 각국 금리·국채 / 공모주 청약 / 부동산) =====
@app.get("/api/fintech/pins")
def fintech_pins_api() -> JSONResponse:
    """대시보드 상단 핀 차트(지수·금·환율·BTC + 스파크라인). 500 금지."""
    try:
        from app.datasources import fintech
        return JSONResponse(fintech.pins())
    except Exception as exc:
        return JSONResponse({"ok": False, "items": [], "error": str(exc)})


@app.get("/api/fintech/markets")
def fintech_markets_api() -> JSONResponse:
    """금·은·유가·달러인덱스·환율. 500 금지."""
    try:
        from app.datasources import fintech
        return JSONResponse(fintech.markets())
    except Exception as exc:
        return JSONResponse({"ok": False, "items": [], "error": str(exc)})


@app.get("/api/fintech/btc")
def fintech_btc_api() -> JSONResponse:
    """비트코인 실시간(업비트 KRW-BTC). 프론트가 수초마다 폴링해 BTC만 실시간 갱신. 500 금지."""
    try:
        from app.datasources import fintech
        return JSONResponse(fintech.btc_live())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/fintech/rates")
def fintech_rates_api() -> JSONResponse:
    """미국 수익률 곡선 + 각국 10년 국채 금리. 500 금지."""
    try:
        from app.datasources import fintech
        return JSONResponse(fintech.rates())
    except Exception as exc:
        return JSONResponse({"ok": False, "us_curve": [], "global_10y": [], "error": str(exc)})


@app.get("/api/fintech/ipo")
def fintech_ipo_api(limit: int = 40, q: str = "") -> JSONResponse:
    """공모주 일정(종목명·청약일·공모가·주간사·경쟁률·상장일). q 주어지면 종목명 검색. 500 금지."""
    try:
        from app.datasources import fintech
        return JSONResponse(fintech.ipo_calendar(limit, q=q or ""))
    except Exception as exc:
        return JSONResponse({"ok": False, "items": [], "error": str(exc)})


@app.get("/api/fintech/realestate")
def fintech_realestate_api(mode: str = "top", sido: str = "", lawd: str = "", q: str = "") -> JSONResponse:
    """전국 아파트 실거래 — mode=top(시/도 거래량 TOP10) | search(시군구/단지명). 500 금지."""
    try:
        from app.datasources import fintech
        return JSONResponse(fintech.real_estate(mode=mode, sido=sido or None, lawd=lawd or None, q=q or None))
    except Exception as exc:
        return JSONResponse({"ok": False, "enabled": False, "message": str(exc)})


@app.get("/api/eod-report")
def get_eod_report_api() -> JSONResponse:
    """저장된 최신 장 마감 AI 리포트. 500 금지."""
    from app.analysis.eod_report import load
    return JSONResponse(load())


@app.post("/api/eod-report/run")
def run_eod_report_api() -> JSONResponse:
    """장 마감 AI 리포트 지금 생성(수동 테스트). 카톡·텔레그램으로도 발송. 500 금지."""
    try:
        from app.analysis.eod_report import generate
        return JSONResponse(generate(_registry, send=True))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


def _norm_symbol(raw: str) -> tuple[str, str] | None:
    """입력 종목코드를 (정규화심볼, market) 으로. 6자리 숫자→('005930','KR'),
    그 외 영문 티커→대문자('AAPL','US'). 형식이 아니면 None."""
    s = (raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        return (s, "KR") if len(s) == 6 else None
    su = s.upper()
    # 미국 티커/지수/선물(예: AAPL, ^SOX, NQ=F, BRK.B). 공백/한글 등은 거부.
    import re
    if re.fullmatch(r"[A-Z0-9.\-^=]{1,15}", su):
        return su, "US"
    return None


def _resolve_symbol_by_name(raw: str) -> tuple[str, str] | None:
    """종목명 입력 → (코드, market). 정확일치 1건 또는 후보가 딱 1건이면 해석, 아니면 None."""
    q = (raw or "").strip()
    if not q:
        return None
    qn = q.replace(" ", "").lower()
    try:
        from app.datasources.kr_price import search_names
        hits = search_names(q, limit=5)
    except Exception:
        hits = []
    exact = [h for h in hits if h["name"].replace(" ", "").lower() == qn]
    if len(exact) == 1:
        return exact[0]["symbol"], "KR"
    if len(hits) == 1:
        return hits[0]["symbol"], "KR"
    # 미국: 한글 별칭(애플→AAPL) 정확일치 > 이름 검색 결과가 딱 1건이면 채택
    try:
        from app.datasources import us_market as usm
        for alias, tick in usm.US_KR_ALIASES.items():
            if alias.lower() == qn:
                return tick, "US"
        us_hits = usm.search_names(q, limit=2)
        if not hits and len(us_hits) == 1:
            return us_hits[0]["symbol"], "US"
    except Exception:
        pass
    return None


@app.get("/api/symbol-search")
def symbol_search_api(q: str = "") -> JSONResponse:
    """종목 추가용 자동완성 검색 — 국내(KRX 전종목)+미국(내장 메타). 500 금지."""
    try:
        from app.datasources.kr_price import search_names
        items = [dict(h, market="KR") for h in search_names(q, limit=8)]
        from app.datasources.us_market import search_names as us_search
        items += [dict(h, market="US") for h in us_search(q, limit=10 - min(len(items), 5))]
        return JSONResponse({"ok": True, "items": items[:12]})
    except Exception as exc:
        return JSONResponse({"ok": True, "items": [], "error": str(exc)})


@app.get("/api/watchlist")
def get_watchlist_api(request: Request) -> JSONResponse:
    """현재 사용자 관심종목(국내+미국). 로그인 시 removable=True(자기 종목이라 삭제 가능).
    비로그인은 .env 기본종목을 읽기전용으로 보여준다. 500 금지."""
    user = _feature_user(request)
    rows = _user_watchlist(request)
    items = []
    for r in rows:
        code = r["symbol"]
        snap = _poller._snapshot.get(code) or {}
        items.append({"symbol": code, "market": r.get("market") or "KR",
                      "name": snap.get("name") or _name_for(code),
                      "removable": bool(user)})
    return JSONResponse({"ok": True, "authenticated": bool(user), "items": items})


@app.post("/api/watchlist")
async def set_watchlist_api(request: Request) -> JSONResponse:
    """관심종목 추가/삭제(런타임). action: add|remove, symbol: 국내 6자리 또는 미국 티커.
    로그인+소유자 승인 필요(사용자별 DB 저장). 500 금지."""
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "로그인·승인이 필요합니다"})
    try:
        data = await request.json()
    except Exception:
        data = {}
    action = data.get("action")
    norm = _norm_symbol(data.get("symbol") or "")
    if not norm and action == "add":
        # 종목명 입력 허용: "삼성전자" → ("005930","KR"). 후보가 여럿이면 프론트 자동완성에서 고르게 함.
        norm = _resolve_symbol_by_name(str(data.get("symbol") or ""))
    if not norm:
        return JSONResponse({"ok": False, "error": "종목명·국내 6자리 코드·미국 티커 중 하나를 입력하세요"})
    code, market = norm
    repo = Repository()
    try:
        if action == "add":
            existing = {x["symbol"] for x in repo.list_watchlist(user["id"])}
            if code in existing:
                return JSONResponse({"ok": False, "error": "이미 있는 종목입니다"})
            try:
                q = _registry.quote(code)
            except Exception:
                q = None
            if not q or q.get("price") in (None, 0):
                return JSONResponse({"ok": False, "error": "시세 조회 실패 — 종목코드/티커를 확인하세요"})
            repo.add_watchlist(user["id"], code, market)
            _poller.refresh_symbols()  # 새 종목 시세를 곧바로 받아두도록
            return JSONResponse({"ok": True, "added": code, "market": market,
                                 "name": q.get("name"), "price": q.get("price")})
        if action == "remove":
            repo.remove_watchlist(user["id"], code)
            return JSONResponse({"ok": True, "removed": code})
        return JSONResponse({"ok": False, "error": "action=add|remove 필요"})
    finally:
        repo.close()


@app.get("/api/weekly-review")
def get_weekly_review_api() -> JSONResponse:
    """저장된 최신 주간 복기 리포트. 500 금지."""
    from app.analysis.weekly_review import load
    return JSONResponse(load())


@app.post("/api/weekly-review/run")
def run_weekly_review_api() -> JSONResponse:
    """주간 복기 리포트 지금 생성(수동). 카톡·텔레그램 발송. 500 금지."""
    try:
        from app.analysis.weekly_review import generate
        return JSONResponse(generate(_registry, send=True))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/holdings-care")
def get_holdings_care_api() -> JSONResponse:
    """저장된 최신 보유종목 케어 리포트. 500 금지."""
    from app.analysis.holdings_care import load
    return JSONResponse(load())


@app.post("/api/holdings-care/run")
def run_holdings_care_api() -> JSONResponse:
    """보유종목 케어 리포트 지금 생성(수동). 카톡·텔레그램 발송. 500 금지."""
    try:
        from app.analysis.holdings_care import generate
        return JSONResponse(generate(_registry, send=True))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/token-usage")
def token_usage_api() -> JSONResponse:
    """앱(API)의 Claude 토큰 사용량·추정비용. Claude Code/채팅은 미포함. 500 금지."""
    try:
        from app.analysis.token_usage import summary
        return JSONResponse({"ok": True, **summary()})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


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
    """알림 설정 저장(부분 갱신). 서버 푸시(카톡/텔레그램)는 소유자 단일채널이라 소유자만 변경. 500 금지."""
    if not _is_owner(request):
        return JSONResponse({"ok": False, "owner_only": True, "error": "서버 알림 설정은 소유자만 변경"})
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


# ===================== 구글 로그인 + 사용자별 매매일지 =====================
# 세션은 랜덤 sid 를 httponly 쿠키로 내려 DB sessions 테이블과 연결한다.
# 키(GOOGLE_CLIENT_ID/SECRET)는 .env 에만(공개 레포라 커밋 금지).
import secrets as _secrets
import time as _time
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

_SESSION_DAYS = 30

# OAuth state(CSRF 방지) 서버측 보관. iOS Safari(ITP)는 교차 사이트 리디렉션 직전에 심은
# 쿠키를 콜백에 안 실어줘서(cookie_present=False), 쿠키만으론 검증 불가 → 아이폰 로그인 실패.
# 앱은 단일 프로세스(uvicorn 1 worker)라 로그인→콜백 사이 프로세스 메모리 보관이 유효하다.
# state -> 만료(epoch). 콜백서 1회 검증 후 삭제. 재시작 시 비지만(드묾) 재시도로 해결.
# state 는 '파일'에 보관한다(앱 재시작에도 생존). 프로세스 메모리면 재배포·재시작 시
# 진행 중이던 로그인이 STATE MISMATCH 로 실패했다(특히 iOS: 쿠키 미전송이라 서버 state 가
# 유일한 검증 수단). 단일 프로세스라 파일 보관으로 충분하다.
from pathlib import Path as _Path2
_OAUTH_STATE_FILE = _Path2(settings.db_path).resolve().parent / "oauth_states.json"
_OAUTH_STATE_TTL = 3600.0  # 1시간(모바일 동의·재인증·앱 재시작 여유)


def _load_oauth_states() -> dict:
    try:
        if _OAUTH_STATE_FILE.exists():
            return _json.loads(_OAUTH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_oauth_states(d: dict) -> None:
    try:
        _OAUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OAUTH_STATE_FILE.write_text(_json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _remember_state(state: str) -> None:
    now = _time.time()
    d = {k: v for k, v in _load_oauth_states().items() if v > now}  # 만료 정리
    if len(d) > 500:
        d = {}
    d[state] = now + _OAUTH_STATE_TTL
    _save_oauth_states(d)


def _check_state(state: str, cookie_state: str) -> bool:
    """state 1회용 검증: 서버 파일 보관에 존재(아이폰=쿠키 유실 대비) 또는 쿠키 일치(PC)면 통과."""
    if not state:
        return False
    d = _load_oauth_states()
    exp = d.pop(state, None)
    if exp is not None:
        _save_oauth_states(d)  # 1회용: 검증 후 제거
    server_ok = exp is not None and exp >= _time.time()
    cookie_ok = bool(cookie_state) and state == cookie_state
    return server_ok or cookie_ok

# 구글 인증 적용 모드(사용자가 ⚙️설정에서 토글). 서버 파일에 저장해 재시작에도 유지.
#   off     = 미사용(로그인 버튼·매매일지 탭 숨김)
#   journal = 매매일지 부분 사용(일지만 로그인 필요, 나머지는 공개)
#   full    = 전체 메뉴 사용(사이트 전체 로그인 필요)
_AUTH_MODES = ("off", "journal", "full")
import json as _json
from pathlib import Path as _Path
_AUTH_CFG_FILE = _Path(settings.db_path).resolve().parent / "auth_config.json"


def _auth_mode() -> str:
    """현재 인증 모드. 파일 우선, 없으면 .env(REQUIRE_LOGIN/기본) 폴백."""
    try:
        if _AUTH_CFG_FILE.exists():
            m = _json.loads(_AUTH_CFG_FILE.read_text(encoding="utf-8")).get("mode")
            if m in _AUTH_MODES:
                return m
    except Exception:
        pass
    # 설정 파일이 없으면(예: 재배포로 data/ 초기화) 항상 journal 로 시작한다.
    # full(사이트 전체 로그인)은 ⚙️설정에서 명시적으로 토글했을 때만 — 의도치 않게
    # 전체 화면 로그인 게이트가 '갑자기' 뜨는 것을 방지(REQUIRE_LOGIN env 폴백 제거).
    return "journal"


def _save_auth_mode(mode: str) -> None:
    try:
        _AUTH_CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _AUTH_CFG_FILE.write_text(_json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _auth_bypass() -> bool:
    """임시 인증 우회. data/auth_config.json 의 bypass=true 면 로그인 없이 '소유자(첫 사용자)'로
    동작시킨다. ⚠️ 공개 사이트라 켜는 동안 누구나 매매일지 접근 — 정식 로그인 복구 전 임시 전용."""
    try:
        if _AUTH_CFG_FILE.exists():
            return bool(_json.loads(_AUTH_CFG_FILE.read_text(encoding="utf-8")).get("bypass"))
    except Exception:
        pass
    return False


def _owner_user() -> dict | None:
    """가입된 첫 사용자(소유자) — bypass 시 이 사용자로 동작."""
    repo = Repository()
    try:
        row = repo.conn.execute(
            "SELECT id, email, name, picture FROM users ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["approved"] = True  # 소유자는 정의상 항상 승인 상태
        return d
    except Exception:
        return None
    finally:
        repo.close()


def _current_user(request: Request) -> dict | None:
    """요청 쿠키(sid)로 로그인 사용자를 찾는다. 비로그인/실패 시 None.
    단, bypass 가 켜져 있으면 세션이 없어도 소유자로 동작(임시)."""
    sid = request.cookies.get("sid", "")
    if sid:
        repo = Repository()
        try:
            u = repo.user_by_session(sid)
        except Exception:
            u = None
        finally:
            repo.close()
        if u:
            return u
    if _auth_bypass():
        return _owner_user()
    return None


def _is_owner(request: Request) -> bool:
    """현재 사용자가 소유자(첫 가입자=내 계좌 주인)인가. 포트폴리오·자동매매 등 실계좌 기능 보호용."""
    u = _current_user(request)
    if not u:
        return False
    owner = _owner_user()
    return bool(owner and owner.get("id") == u.get("id"))


def _feature_user(request: Request) -> dict | None:
    """로그인 승인제 게이트 — 로그인했고 '소유자 승인'까지 끝난 사용자만 돌려준다.

    구글/카톡 로그인 자체는 누구나 할 수 있으므로, 매매일지·워치리스트 등 기능성
    엔드포인트는 반드시 이 함수를 써야 한다(미승인=비로그인과 동일 취급).
    소유자(첫 가입자)는 항상 통과.
    """
    u = _current_user(request)
    if not u:
        return None
    if u.get("approved"):
        return u
    owner = _owner_user()
    if owner and owner.get("id") == u.get("id"):
        return u
    return None


def _user_watchlist(request: Request) -> list[dict]:
    """요청자의 관심종목 [{symbol, market}].

    로그인 사용자: 자기 DB 워치리스트. 소유자(첫 가입자)만 비어 있을 때 .env 기본종목으로
    1회 시드 — 다른 사용자는 빈 목록에서 시작한다(.env는 소유자 개인 설정이라 남에게 보이면 안 됨).
    비로그인 방문자: .env 기본종목(KR→US)을 읽기전용으로 보여준다. 미승인 사용자도
    비로그인과 동일하게 기본종목만 본다(_feature_user).
    """
    user = _feature_user(request)
    if user:
        repo = Repository()
        try:
            rows = repo.list_watchlist(user["id"])
            if not rows and _is_owner(request):
                repo.seed_watchlist_if_empty(user["id"], list(settings.kr_symbols),
                                             list(settings.us_symbols))
                rows = repo.list_watchlist(user["id"])
        finally:
            repo.close()
        return rows
    return [{"symbol": s, "market": "KR"} for s in settings.kr_symbols] + \
           [{"symbol": s, "market": "US"} for s in settings.us_symbols]


@app.get("/api/auth/me")
def auth_me(request: Request) -> JSONResponse:
    """현재 로그인 상태 + 인증 적용 모드(off/journal/full) + 키 설정 여부."""
    from app.auth.google_auth import configured
    from app.auth import kakao_auth
    mode = _auth_mode()
    user = _current_user(request)
    base = {"configured": configured(), "kakao_configured": kakao_auth.configured(), "mode": mode}
    if user:
        owner = _owner_user()
        is_owner = bool(owner and owner.get("id") == user.get("id"))
        approved = bool(user.get("approved")) or is_owner  # 승인제: 대기 사용자는 False
        return JSONResponse({**base, "authenticated": True, "is_owner": is_owner,
                             "approved": approved,
                             "user": {"name": user["name"], "email": user["email"], "picture": user["picture"]}})
    return JSONResponse({**base, "authenticated": False, "is_owner": False, "approved": False})


@app.get("/api/auth-config")
def auth_config_get() -> JSONResponse:
    """현재 인증 모드 + 키 설정 여부(설정 화면 표시용). 500 금지."""
    from app.auth.google_auth import configured
    return JSONResponse({"mode": _auth_mode(), "configured": configured()})


@app.post("/api/auth-config")
async def auth_config_set(request: Request) -> JSONResponse:
    """인증 모드 변경 — 자동매매와 동일한 비밀번호(TRADE_PASSWORD)로 보호.

    공개 대시보드라 아무나 사이트 전체를 잠그지 못하도록 비번을 검증한다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = str(body.get("mode") or "").strip()
    if mode not in _AUTH_MODES:
        return JSONResponse({"ok": False, "error": "mode 는 off/journal/full 중 하나"})
    pw = str(body.get("password") or "")
    if not settings.trade_password or pw != settings.trade_password:
        return JSONResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다."}, status_code=403)
    _save_auth_mode(mode)
    return JSONResponse({"ok": True, "mode": mode})


@app.get("/api/auth/google/login")
def auth_google_login():
    """구글 로그인 동의 페이지로 리다이렉트. state 를 httponly 쿠키에 심어 CSRF 방지."""
    from app.auth.google_auth import authorize_url, configured
    if not configured():
        return JSONResponse({"ok": False, "error": "GOOGLE_CLIENT_ID/SECRET 미설정"})
    state = _secrets.token_urlsafe(24)
    _remember_state(state)  # 서버측 보관(아이폰 쿠키 유실 대비 1차 검증 수단)
    resp = RedirectResponse(authorize_url(state))
    # 쿠키도 함께 심는다(PC 등 정상 브라우저의 추가 방어). SameSite=None+Secure.
    resp.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="none", secure=True)
    print(f"[oauth] login: issued state len={len(state)}", flush=True)
    return resp


@app.get("/api/auth/google/callback")
def auth_google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """구글 OAuth 콜백 → code 교환 → 사용자 생성/갱신 → 세션 쿠키 발급 후 / 로 이동."""
    from app.auth.google_auth import exchange_code
    if error or not code:
        return HTMLResponse(f"<h3>구글 로그인 실패</h3><pre>{error or '코드 없음'}</pre><p>창을 닫고 다시 시도하세요.</p>")
    # state 검증(CSRF): 서버 보관(아이폰) 또는 httponly 쿠키 일치(PC). iOS Safari 는
    # 콜백에 쿠키를 안 실어줘 쿠키만으론 검증 불가 → 서버측 1회용 state 로 통과시킨다.
    cookie_state = request.cookies.get("oauth_state", "")
    if not _check_state(state, cookie_state):
        print(f"[oauth] STATE MISMATCH q_len={len(state)} cookie_present={bool(cookie_state)}", flush=True)
        return HTMLResponse("<h3>로그인 실패</h3><p>state 불일치(보안). 다시 시도하세요.</p>")
    info = exchange_code(code)
    if not info.get("ok"):
        print(f"[oauth] EXCHANGE FAIL: {info.get('error')}", flush=True)
        return HTMLResponse(f"<h3>로그인 실패</h3><pre>{info.get('error')}</pre>")
    print("[oauth] LOGIN OK", flush=True)
    repo = Repository()
    try:
        uid = repo.upsert_user(info["sub"], info["email"], info["name"], info["picture"])
        row = repo.conn.execute("SELECT approved FROM users WHERE id=?", (uid,)).fetchone()
        approved = bool(row and row["approved"])
        sid = _secrets.token_urlsafe(32)
        expires = (_dt.now(_tz.utc) + _td(days=_SESSION_DAYS)).isoformat()
        repo.create_session(sid, uid, expires)
    finally:
        repo.close()
    # iOS Safari/ITP 는 교차 사이트 콜백의 '307 리다이렉트'에 실린 Set-Cookie 를 종종
    # 버린다('계속 로그인' 증상). 그래서 RedirectResponse 대신 먼저 1st-party 200 HTML
    # 문서에 쿠키를 확실히 심은 뒤, 그 문서에서 클라이언트측으로 / 로 이동시킨다(영속성↑).
    resp = HTMLResponse(_login_success_html(approved))
    _set_sid_cookie(resp, sid)
    resp.delete_cookie("oauth_state")
    return resp


def _set_sid_cookie(resp, sid: str) -> None:
    """세션 쿠키(sid) 일관 설정.
    - Max-Age + **Expires 둘 다**: Safari 가 Max-Age 만 있으면 '세션 쿠키'로 취급해 창 닫으면 삭제.
    - **SameSite=Lax**: 콜백을 1st-party HTML 로 심으므로 Lax 로 충분하고, iOS Safari ITP 가
      SameSite=None 1st-party 쿠키를 앱 종료 시 공격적으로 지우는 문제(=아이폰 재로그인)를 피한다."""
    exp = _dt.now(_tz.utc) + _td(days=_SESSION_DAYS)
    resp.set_cookie("sid", sid, max_age=_SESSION_DAYS * 86400, expires=exp,
                    httponly=True, samesite="lax", secure=True, path="/")


def _login_success_html(approved: bool = True) -> str:
    """콜백 후 1st-party 쿠키를 심는 200 HTML. 미승인 사용자는 '승인 대기' 안내로 이동."""
    if approved:
        msg, dest = "✅ 로그인 완료 — 이동 중…", "/?login=ok"
    else:
        msg, dest = "⏳ 가입 완료 — 관리자 승인 후 이용할 수 있습니다. 이동 중…", "/?login=pending"
    return (
        "<!doctype html><meta charset='utf-8'><title>로그인 완료</title>"
        "<body style='font-family:sans-serif;background:#0e0e12;color:#eee;text-align:center;padding-top:40px'>"
        f"<p>{msg}</p>"
        f"<script>setTimeout(function(){{location.replace('{dest}');}},600);</script>"
        f"<noscript><a href='{dest}' style='color:#6cf'>여기를 눌러 계속</a></noscript></body>"
    )


@app.get("/api/auth/kakao/login")
def auth_kakao_login():
    """카카오 로그인 동의 페이지로 리다이렉트(구글과 병행). state 쿠키+서버보관(아이폰 대비)."""
    from app.auth import kakao_auth
    if not kakao_auth.configured():
        return JSONResponse({"ok": False, "error": "KAKAO_REST_API_KEY 미설정"})
    state = _secrets.token_urlsafe(24)
    _remember_state(state)
    resp = RedirectResponse(kakao_auth.authorize_url(state))
    resp.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="none", secure=True)
    print("[oauth-kakao] login: issued state", flush=True)
    return resp


@app.get("/api/auth/kakao/callback")
def auth_kakao_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """카카오 OAuth 콜백 → code 교환 → 사용자 생성/갱신('kakao:<id>') → 세션 쿠키 발급."""
    from app.auth import kakao_auth
    if error or not code:
        return HTMLResponse(f"<h3>카카오 로그인 실패</h3><pre>{error or '코드 없음'}</pre><p>창을 닫고 다시 시도하세요.</p>")
    cookie_state = request.cookies.get("oauth_state", "")
    if not _check_state(state, cookie_state):
        return HTMLResponse("<h3>로그인 실패</h3><p>state 불일치(보안). 다시 시도하세요.</p>")
    info = kakao_auth.exchange_code(code)
    if not info.get("ok"):
        print(f"[oauth-kakao] EXCHANGE FAIL: {info.get('error')}", flush=True)
        return HTMLResponse(f"<h3>로그인 실패</h3><pre>{info.get('error')}</pre>")
    repo = Repository()
    try:
        uid = repo.upsert_user(info["sub"], info["email"], info["name"], info["picture"])
        row = repo.conn.execute("SELECT approved FROM users WHERE id=?", (uid,)).fetchone()
        approved = bool(row and row["approved"])
        sid = _secrets.token_urlsafe(32)
        expires = (_dt.now(_tz.utc) + _td(days=_SESSION_DAYS)).isoformat()
        repo.create_session(sid, uid, expires)
    finally:
        repo.close()
    resp = HTMLResponse(_login_success_html(approved))
    _set_sid_cookie(resp, sid)
    resp.delete_cookie("oauth_state")
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    """로그아웃 — 서버 세션 삭제 + 쿠키 제거."""
    sid = request.cookies.get("sid", "")
    if sid:
        repo = Repository()
        try:
            repo.delete_session(sid)
        finally:
            repo.close()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sid")
    return resp


# ===== 👥 사용자 승인 관리 (소유자 전용) =====
# 구글/카톡 로그인은 누구나 시도할 수 있으므로, 소유자가 여기서 승인한 계정만
# 매매일지·워치리스트 등 기능을 쓸 수 있다(_feature_user 게이트).

@app.get("/api/admin/users")
def admin_users_list(request: Request) -> JSONResponse:
    """전체 사용자 + 승인 상태(설정 화면용). 소유자만 403 없이 통과."""
    if not _is_owner(request):
        return JSONResponse({"ok": False, "error": "권한 없음(소유자 전용)"}, status_code=403)
    owner = _owner_user() or {}
    repo = Repository()
    try:
        users = repo.list_users()
    finally:
        repo.close()
    items = []
    for u in users:
        provider = "카카오" if str(u.get("google_sub") or "").startswith("kakao:") else "구글"
        items.append({"id": u["id"], "email": u.get("email"), "name": u.get("name"),
                      "picture": u.get("picture"), "provider": provider,
                      "created_at": u.get("created_at"), "last_login": u.get("last_login"),
                      "approved": bool(u.get("approved")),
                      "is_owner": u["id"] == owner.get("id")})
    return JSONResponse({"ok": True, "users": items})


@app.post("/api/admin/users")
async def admin_users_act(request: Request) -> JSONResponse:
    """사용자 승인/차단/삭제. body: {user_id, action: approve|revoke|delete}. 소유자 전용.

    소유자 본인은 어떤 조작도 불가(스스로 잠그는 사고 방지). 차단·삭제 시 해당
    사용자의 세션을 전부 끊어 즉시 로그아웃된다.
    """
    if not _is_owner(request):
        return JSONResponse({"ok": False, "error": "권한 없음(소유자 전용)"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        uid = int(body.get("user_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "user_id 필요"})
    action = str(body.get("action") or "")
    owner = _owner_user() or {}
    if uid == owner.get("id"):
        return JSONResponse({"ok": False, "error": "소유자 계정은 변경할 수 없습니다"})
    repo = Repository()
    try:
        if action == "approve":
            ok = repo.set_user_approved(uid, True)
        elif action == "revoke":
            ok = repo.set_user_approved(uid, False)
        elif action == "delete":
            ok = repo.delete_user(uid)
        else:
            return JSONResponse({"ok": False, "error": "action=approve|revoke|delete 필요"})
    finally:
        repo.close()
    return JSONResponse({"ok": ok, "user_id": uid, "action": action})


def _journal_fields(body: dict) -> dict:
    """클라이언트 입력에서 일지 필드만 추려 안전하게 정규화."""
    def _num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    cur = (str(body.get("currency") or "KRW")).strip().upper()
    # 빈 문자열은 NULL 로(공백만 입력해도 종목 정보 없는 것으로 취급)
    if cur not in ("KRW", "USD"):
        cur = "KRW"
    cat = (str(body.get("category") or "일반주")).strip()
    if cat not in ("일반주", "공모주"):
        cat = "일반주"
    fx = _num(body.get("fx_rate"))
    # KRW=1.0 고정. USD는 입력 환율 보존(없으면 None → 검증에서 거부). 1.0 기본대입 금지(환산 누락 원인).
    fx_rate = 1.0 if cur == "KRW" else (fx if (fx and fx > 0) else None)
    return {
        "trade_date": (str(body.get("trade_date") or "")).strip() or _dt.now().strftime("%Y-%m-%d"),
        "symbol": ((str(body.get("symbol") or "")).strip()[:32] or None),
        "name": ((str(body.get("name") or "")).strip()[:64] or None),
        "side": (str(body.get("side") or "")).strip()[:16],   # 매수/매도/배당/메모
        "price": _num(body.get("price")),
        "qty": _num(body.get("qty")),
        "category": cat,                                      # 일반주/공모주
        "currency": cur,
        "fx_rate": fx_rate,                                   # KRW=1, USD=환율(원/달러, 필수)
        "tax": _num(body.get("tax")) or 0.0,                  # 세금(원)
        "reason": (str(body.get("reason") or "")).strip()[:2000],
        "memo": (str(body.get("memo") or "")).strip()[:2000],
        # 첨부 사진 파일명 — 경로 문자 제거(본인 uploads 폴더 안 파일명만 허용)
        "photo": (_PHOTO_NAME_RE.sub("", os.path.basename(str(body.get("photo") or "")))[:128] or None),
    }


def _ensure_fx(fields: dict) -> None:
    """USD 거래에 환율이 없으면 현재 원/달러 환율을 자동 조회해 채운다(원화 통일).

    사용자가 환율을 비워도 서버가 환율을 구해 amount 를 원화로 환산하도록 보장.
    조회 실패 시 fx_rate 는 None 으로 남고 _journal_validate 가 거부한다.
    """
    if fields.get("currency") != "USD":
        return
    fx = fields.get("fx_rate")
    if fx and fx > 1:
        return
    try:
        from app.datasources.fintech import usdkrw_rate
        rate = usdkrw_rate()
        if rate and rate > 1:
            fields["fx_rate"] = round(float(rate), 2)
    except Exception:
        pass


def _journal_validate(fields: dict) -> str | None:
    """저장 전 검증. 통과면 None, 막아야 하면 오류 메시지 반환.

    종목 정보(종목명·코드)가 비어 있으면 저장하지 않는다. 단, 순수 '메모'는
    특정 종목이 없어도 되므로 예외.
    """
    if fields.get("side") != "메모" and not (fields.get("symbol") or fields.get("name")):
        return "종목 정보(종목명)를 입력하세요. 비어 있으면 저장되지 않습니다."
    # USD 거래(단가·수량 있는)는 환율 필수 — 없으면 원화 환산이 안 돼 합계가 깨진다.
    if (fields.get("currency") == "USD" and fields.get("price") is not None
            and fields.get("qty") is not None):
        fx = fields.get("fx_rate")
        if not fx or fx <= 1:
            return "USD 거래는 환율(원/$)을 입력하세요 (예: 1380). 비우면 원화 환산이 안 됩니다."
    return None


@app.get("/api/journal")
def journal_list(request: Request) -> JSONResponse:
    """내 매매일지 목록. 비로그인·미승인은 401(절대 남의 일지 노출 안 함)."""
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "login_required"}, status_code=401)
    repo = Repository()
    try:
        return JSONResponse({"ok": True, "entries": repo.list_journal(user["id"])})
    finally:
        repo.close()


@app.post("/api/journal")
async def journal_create(request: Request) -> JSONResponse:
    """매매일지 추가(본인). 비로그인·미승인 401."""
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "login_required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    fields = _journal_fields(body or {})
    _ensure_fx(fields)
    err = _journal_validate(fields)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    repo = Repository()
    try:
        eid = repo.add_journal(user["id"], fields)
        return JSONResponse({"ok": True, "id": eid})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    finally:
        repo.close()


@app.put("/api/journal/{entry_id}")
async def journal_update(entry_id: int, request: Request) -> JSONResponse:
    """매매일지 수정 — 본인 소유만(user_id 일치 강제). 비로그인·미승인 401."""
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "login_required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    fields = _journal_fields(body or {})
    _ensure_fx(fields)
    err = _journal_validate(fields)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    repo = Repository()
    try:
        if "photo" not in (body or {}):          # photo 를 안 보낸 수정이면 기존 첨부 유지
            fields["photo"] = repo.get_journal_photo(user["id"], entry_id)
        ok = repo.update_journal(user["id"], entry_id, fields)
        return JSONResponse({"ok": ok})
    finally:
        repo.close()


@app.delete("/api/journal/{entry_id}")
def journal_delete(entry_id: int, request: Request) -> JSONResponse:
    """매매일지 삭제 — 본인 소유만. 비로그인·미승인 401."""
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "login_required"}, status_code=401)
    repo = Repository()
    try:
        return JSONResponse({"ok": repo.delete_journal(user["id"], entry_id)})
    finally:
        repo.close()


# ===== 매매일지 사진 (촬영/임포트 + OCR 자동입력) =====
# zikimi 패턴: 원본은 항상 보존(data/uploads/<user_id>/), OCR 은 LLM 비전으로 추출.
# 추출 결과는 확정값이 아니라 needs_review — 프론트가 입력폼에 채우고 사용자가 확인 후 저장한다.
import re as _re

_UPLOAD_DIR = Path(settings.db_path).resolve().parent / "uploads"
_PHOTO_MAX_BODY = 12 * 1024 * 1024          # base64 포함 요청 상한 12MB(원본 약 9MB)
_PHOTO_NAME_RE = _re.compile(r"[^0-9A-Za-z._-]")
_PHOTO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

_JOURNAL_OCR_SCHEMA = {
    "type": "object",
    "properties": {"entries": {"type": "array", "items": {"type": "object", "properties": {
        "trade_date": {"type": "string", "description": "거래일 YYYY-MM-DD"},
        "name": {"type": "string", "description": "종목명"},
        "symbol": {"type": "string", "description": "종목코드 6자리 또는 미국 티커(이미지에 보일 때만)"},
        "side": {"type": "string", "enum": ["매수", "매도", "배당", "메모"]},
        "price": {"type": "number", "description": "체결 단가(쉼표 제거한 숫자)"},
        "qty": {"type": "number", "description": "체결 수량"},
        "currency": {"type": "string", "enum": ["KRW", "USD"]},
        "tax": {"type": "number", "description": "세금·수수료(보일 때만)"},
        "memo": {"type": "string", "description": "그 외 참고 정보 한 줄"},
    }, "required": ["side"]}}},
    "required": ["entries"],
}


def _sniff_image_mime(data: bytes) -> str | None:
    """확장자 대신 매직바이트로 이미지 형식 판별(위조 확장자 차단)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _journal_ocr(image_b64: str, mime: str) -> list[dict]:
    """사진에서 매매 거래 추출(LLM 비전). 실패는 예외로 올린다 — 호출부가 로그+안내."""
    from app import llm
    today = _dt.now().strftime("%Y-%m-%d")
    system = (
        "이 이미지는 증권사 앱의 체결/거래내역 화면 캡처, 매매 기록표 사진, 또는 손글씨 투자 노트다. "
        "이미지에 보이는 매매 거래를 빠짐없이 entries 배열로 추출하라.\n"
        "- 이미지에 실제로 보이는 값만 사용하고, 안 보이는 값은 필드를 아예 넣지 마라(지어내기 금지).\n"
        "- 종목명은 표 안에 없고 화면 최상단 제목에 있는 경우가 많다(예: 거래 상세 화면의 큰 제목). "
        "제목이 회사명이면 name 으로 써라. 단 증권사·은행·앱 이름(예: 'OO증권', 'OO뱅크'류)도 "
        "상장사일 수 있으니 제목에 보이는 이름을 그대로 name 에 넣어라.\n"
        "- 매수/매도 구분 글자가 잘리거나 안 보이면 다른 단서로 판별하라: 거래세(증권거래세)가 0보다 크면 매도, "
        "예수금 잔액이 늘고 유가잔고(보유수량)가 줄었으면 매도, 반대면 매수.\n"
        "- 숫자의 쉼표(,)는 제거하고 숫자 타입으로. 달러($)·미국 종목이면 currency=USD, 아니면 KRW.\n"
        f"- 날짜에 연도가 없으면 오늘({today}) 기준 가장 가까운 과거 날짜로 해석해 YYYY-MM-DD 로.\n"
        "- 같은 종목이라도 체결 건이 다르면 별도 entry 로. 거래가 하나도 없으면 entries=[]."
    )
    out = llm.chat_vision_json(system, f"오늘은 {today}. 이미지에서 매매 거래 내역을 추출해 줘.",
                               image_b64, mime, _JOURNAL_OCR_SCHEMA, 2048, "journal_ocr")
    entries = out.get("entries") or []
    clean: list[dict] = []
    for e in entries[:20]:                       # 폭주 방지 상한
        if not isinstance(e, dict):
            continue
        def _n(v):
            try:
                return float(str(v).replace(",", "")) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None
        side = str(e.get("side") or "").strip()
        if side not in ("매수", "매도", "배당", "메모"):
            continue
        clean.append({
            "trade_date": str(e.get("trade_date") or "").strip()[:10] or None,
            "name": str(e.get("name") or "").strip()[:64] or None,
            "symbol": str(e.get("symbol") or "").strip()[:32] or None,
            "side": side,
            "price": _n(e.get("price")),
            "qty": _n(e.get("qty")),
            "currency": "USD" if str(e.get("currency") or "").upper() == "USD" else "KRW",
            "tax": _n(e.get("tax")),
            "memo": str(e.get("memo") or "").strip()[:2000] or None,
        })
    return clean


@app.post("/api/journal/photo")
async def journal_photo_upload(request: Request) -> JSONResponse:
    """매매일지 사진 업로드(+OCR). body: {filename, data: base64}. 비로그인 401.

    원본을 data/uploads/<user_id>/ 에 저장하고, LLM 비전으로 거래 내역을 추출해 돌려준다.
    OCR 이 실패해도 사진 저장은 유지(entries=[] + 실패 사유) — 조용한 null 반환 금지.
    """
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "login_required"}, status_code=401)
    try:
        if int(request.headers.get("content-length") or 0) > _PHOTO_MAX_BODY:
            return JSONResponse({"ok": False, "error": "사진이 너무 큽니다(최대 12MB)"}, status_code=413)
    except ValueError:
        pass
    try:
        body = await request.json()
    except Exception:
        body = {}
    import base64 as _b64
    b64 = str(body.get("data") or "")
    if "," in b64[:80]:                          # "data:image/...;base64," 접두 허용
        b64 = b64.split(",", 1)[1]
    try:
        raw = _b64.b64decode(b64)
    except Exception:
        return JSONResponse({"ok": False, "error": "이미지 데이터를 읽을 수 없습니다"}, status_code=400)
    mime = _sniff_image_mime(raw)
    if not mime:
        return JSONResponse({"ok": False, "error": "JPG/PNG/WEBP 사진만 올릴 수 있습니다"}, status_code=400)
    udir = _UPLOAD_DIR / str(user["id"])
    udir.mkdir(parents=True, exist_ok=True)
    stem = "j-" + _dt.now().strftime("%Y%m%d-%H%M%S")
    ext = _PHOTO_EXT[mime]
    fname, n = stem + ext, 0
    while (udir / fname).exists():               # 같은 초 다중 업로드 충돌 방지
        n += 1
        fname = f"{stem}-{n}{ext}"
    (udir / fname).write_bytes(raw)

    entries: list[dict] = []
    ocr_error: str | None = None
    try:
        import asyncio
        # LLM 호출은 blocking(requests) — 이벤트루프를 막지 않게 스레드로(최대 180초).
        entries = await asyncio.to_thread(_journal_ocr, _b64.b64encode(raw).decode(), mime)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("매매일지 사진 OCR 실패: %s", exc)
        ocr_error = f"OCR 인식 실패 — 사진은 첨부됩니다. 내용은 직접 입력하세요. ({type(exc).__name__})"
    return JSONResponse({"ok": True, "photo": fname, "entries": entries,
                         "source": "llm", "needs_review": True, "ocr_error": ocr_error})


@app.get("/api/journal/photo/{fname}")
def journal_photo_get(fname: str, request: Request):
    """내 매매일지 사진 원본 보기 — 본인 폴더만(경로 문자를 화이트리스트로 정리)."""
    user = _feature_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "login_required"}, status_code=401)
    clean = _PHOTO_NAME_RE.sub("", os.path.basename(fname))[:128]
    p = _UPLOAD_DIR / str(user["id"]) / clean
    if not clean or not p.is_file():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.get("/api/fxrate")
def fxrate() -> JSONResponse:
    """현재 원/달러 환율 — 매매일지 USD 입력 시 환율 칸 프리필용. 실패 시 rate=null."""
    try:
        from app.datasources.fintech import usdkrw_rate
        return JSONResponse({"ok": True, "rate": usdkrw_rate()})
    except Exception:
        return JSONResponse({"ok": True, "rate": None})


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


@app.get("/download")
def download_page() -> HTMLResponse:
    """클린 소스 배포 페이지 — 누구나 자기 KIS API 키로 셀프호스트할 수 있게 안내+다운로드.
    zip 은 scripts/build_clean_release.py 가 생성(비밀키·개인정보 자동 검사 후 패키징)."""
    def _mb(p: Path | None) -> str:
        return f"{p.stat().st_size / 1048576:.0f}" if p else "?"
    exe_zip, src_zip = _latest_dl("jessystock-windows"), _latest_dl("jessystock-src")
    exe_mb, src_mb = _mb(exe_zip), _mb(src_zip)
    exe_exists = exe_zip is not None
    src_label = src_zip.name if src_zip else "빌드 없음"
    gated = bool(_dl_password())  # 비밀번호가 설정돼 있으면 입력 후에만 받도록
    exe_block = (f"""
<div class="card">
  <h2 style="margin-top:0;">🪟 윈도우 — 설치 없이 바로 (권장)</h2>
  <p>파이썬·명령어 필요 없음. 압축 풀고 <code>.env</code>에 키만 넣은 뒤 <b>exe 더블클릭</b>이면 끝.</p>
  <a class="btn" href="#" onclick="return dl('win')">⬇ 윈도우 실행판 (zip · {exe_mb}MB)</a>
  <ol>
  <li><b>KIS API 키 발급</b> — <a href="https://apiportal.koreainvestment.com" target="_blank">KIS 개발자센터</a>에서 앱키/시크릿 (한국투자증권 계좌 필요)</li>
  <li>압축 풀고 <code>.env</code>를 메모장으로 열어 키 입력·저장</li>
  <li><b>jessystock.exe 더블클릭</b> → 브라우저가 자동으로 열림</li>
  </ol>
  <p style="font-size:12.5px;color:#8b97a8;">보안경고가 뜨면 "추가 정보 → 실행"(서명 안 된 개인 프로그램이라 정상). 자세한 건 zip 안 <b>실행방법.txt</b>.</p>
</div>""" if exe_exists else "")
    gate_block = (f"""
<div class="card" style="border-color:#3a4a6a;">
  <h2 style="margin-top:0;">🔒 다운로드 비밀번호</h2>
  <p style="font-size:13.5px;">받으려면 비밀번호를 입력하세요.</p>
  <input id="pw" type="password" inputmode="numeric" placeholder="비밀번호"
    style="background:#0c1018;color:#dbe4f0;border:1px solid #35507e;border-radius:8px;padding:9px 12px;font-size:15px;width:160px;">
  <span id="pwMsg" style="margin-left:8px;font-size:13px;color:#f0b73d;"></span>
</div>""" if gated else "")
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>jessystock 다운로드</title>
<style>
  body {{ background:#0c1018; color:#dbe4f0; font-family:'Noto Sans KR',-apple-system,sans-serif;
         max-width:720px; margin:0 auto; padding:32px 20px; line-height:1.7; }}
  h1 {{ font-size:22px; }} h2 {{ font-size:16px; color:#9fc1ff; }}
  a {{ color:#6ea8fe; }} code {{ background:#151b27; padding:2px 6px; border-radius:6px; font-size:13px; }}
  .btn {{ display:inline-block; background:#2ec26b; color:#08110b; font-weight:700; padding:12px 22px;
         border-radius:10px; text-decoration:none; font-size:16px; margin:10px 0; }}
  .btn.alt {{ background:transparent; color:#6ea8fe; border:1px solid #35507e; }}
  .card {{ background:#151b27; border:1px solid #26314a; border-radius:12px; padding:16px 18px; margin:14px 0; }}
  .warn {{ color:#f0b73d; font-size:13px; }}
  ol li {{ margin:6px 0; }}
</style></head><body>
<h1>📦 jessystock — 내 계좌로 돌리는 내 주식앱</h1>
<p><b>본인의 한국투자증권(KIS) API 키</b>만 있으면 이 앱을 내 컴퓨터/서버에서 직접 돌릴 수 있습니다
(실시간 시세·수급·알림·매매일지·자동매매). 비밀키·개인정보는 배포본에 들어있지 않습니다.</p>
{gate_block}
{exe_block}
<div class="card">
  <h2 style="margin-top:0;">🧑‍💻 소스 코드 (맥·리눅스·서버·개발자용)</h2>
  <p>직접 서버(VPS)에 올리거나 코드를 고치고 싶을 때. 파이썬 필요.</p>
  <a class="btn alt" href="#" onclick="return dl('src')">⬇ 소스 다운로드 (zip · {src_mb}MB)</a>
  <p style="font-size:12px;color:#8b97a8;margin:2px 0 0;">최신 빌드: <code>{src_label}</code></p>
  <p style="font-size:13px;">압축 안 <b>설치가이드_START_HERE.md</b> 참고 —
   <code>.env</code> 설정 후 리눅스: <code>bash deploy/deploy.sh</code> · PC: <code>pip install -r requirements.txt</code> → <code>python -m app.web</code></p>
</div>
<h2>주의</h2>
<p class="warn">⚠️ API 키/시크릿은 절대 타인과 공유하지 마세요(계좌 위험).
자동매매는 모의투자(<code>KIS_PAPER=true</code>)로 충분히 연습 후 사용하세요. 투자 결과는 본인 책임입니다.</p>
<p style="color:#8b97a8;font-size:12.5px;margin-top:24px;">jessystock.com</p>
<script>
  var GATED = {"true" if gated else "false"};
  function dl(kind) {{
    var url = "/dl/" + kind;
    if (GATED) {{
      var el = document.getElementById("pw");
      var pw = el ? (el.value || "").trim() : "";
      if (!pw) {{ var m=document.getElementById("pwMsg"); if(m) m.textContent="비밀번호를 입력하세요"; if(el) el.focus(); return false; }}
      url += "?pw=" + encodeURIComponent(pw);
    }}
    window.location.href = url;
    return false;
  }}
</script>
</body></html>""")


def _dl_password() -> str:
    """다운로드 비밀번호(.env DOWNLOAD_PASSWORD). 비어 있으면 게이트 없음(공개)."""
    return (os.getenv("DOWNLOAD_PASSWORD", "") or "").strip()


def _latest_dl(prefix: str) -> Path | None:
    """downloads/ 안 <prefix>*.zip 중 최신본(파일명 날짜 역순 → 같으면 mtime).
    빌드가 jessystock-src-YYYYMMDD.zip 처럼 날짜를 붙여 떨구므로 이름 정렬이 곧 최신순."""
    cands = sorted(DOWNLOAD_DIR.glob(prefix + "*.zip"),
                   key=lambda p: (p.name, p.stat().st_mtime), reverse=True)
    return cands[0] if cands else None


def _serve_download(prefix: str, pw: str) -> FileResponse | HTMLResponse:
    need = _dl_password()
    if need and (pw or "").strip() != need:
        return HTMLResponse(
            """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<style>body{background:#0c1018;color:#dbe4f0;font-family:sans-serif;text-align:center;padding:60px 20px;}
a{color:#6ea8fe;}</style></head><body>
<h2>🔒 비밀번호가 올바르지 않습니다</h2>
<p><a href="/download">← 다운로드 페이지로 돌아가기</a></p></body></html>""",
            status_code=403,
        )
    path = _latest_dl(prefix)
    if not path:
        return HTMLResponse("파일을 찾을 수 없습니다.", status_code=404)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.get("/dl/win", response_model=None)
def dl_windows(pw: str = "") -> FileResponse | HTMLResponse:
    """윈도우 실행판 zip — 비밀번호(설정 시) 확인 후 최신본 다운로드."""
    return _serve_download("jessystock-windows", pw)


@app.get("/dl/src", response_model=None)
def dl_source(pw: str = "") -> FileResponse | HTMLResponse:
    """소스 zip — 비밀번호(설정 시) 확인 후 최신본 다운로드."""
    return _serve_download("jessystock-src", pw)


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    # 브라우저가 루트 /favicon.ico 를 자동 요청 — 32px PNG 로 응답(캐시 강함).
    return FileResponse(STATIC_DIR / "icon-32.png")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    # 서비스워커는 루트(/)에서 서빙해야 사이트 전체 스코프를 가진다(PWA/홈화면 설치 요건).
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def web_manifest() -> FileResponse:
    # PWA 매니페스트 — 정확한 콘텐츠타입으로 서빙(설치형 웹앱 인식).
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


# 정적 파일(css/js) 마운트. index 는 위 라우트가 우선.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
