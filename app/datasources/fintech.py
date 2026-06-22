"""핀테크 탭 데이터 — 금/원자재·환율, 각국 금리·국채, 공모주 청약 달력, 부동산.

전부 무료·무키 공개데이터:
  - 금/유가/달러인덱스/환율: FinanceDataReader(야후)
  - 각국 금리·장기국채(10Y): FRED(미 연준 공개데이터, OECD 시리즈)
  - 공모주 청약 달력: 38커뮤니케이션(38.co.kr) HTTP 파싱
  - 부동산 거래량(아파트 3000세대+): 국토부 실거래가 API 키 필요 → 키 있으면 활성

모든 함수는 예외를 올리지 않는다(부분 실패 시 ok=False/빈 값). 느린 외부호출은 TTL 캐시.
"""
from __future__ import annotations

import re
import time

# ---- 아주 단순한 모듈 레벨 TTL 캐시 ---------------------------------------
_CACHE: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, producer):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = producer()
    _CACHE[key] = (now, val)
    return val


def _fdr():
    import FinanceDataReader as fdr  # type: ignore
    return fdr


def _last_two(symbol: str):
    """(현재값, 직전값, 날짜) — FDR Close 마지막 2개. 실패 시 (None,None,None)."""
    try:
        df = _fdr().DataReader(symbol).dropna()
        if df is None or df.empty:
            return None, None, None
        col = "Close" if "Close" in df.columns else df.columns[0]
        s = df[col].dropna()
        cur = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) >= 2 else cur
        date = df.index[-1].strftime("%Y-%m-%d")
        return cur, prev, date
    except Exception:
        return None, None, None


# ---- 금·원자재·환율 --------------------------------------------------------
_MARKET_DEFS = [
    {"key": "gold", "name": "금 (현물 USD/oz)", "symbol": "GC=F", "unit": "$", "emoji": "🥇", "pct": True},
    {"key": "silver", "name": "은 (USD/oz)", "symbol": "SI=F", "unit": "$", "emoji": "🥈", "pct": True},
    {"key": "wti", "name": "WTI 원유 (USD/bbl)", "symbol": "CL=F", "unit": "$", "emoji": "🛢️", "pct": True},
    {"key": "dxy", "name": "달러 인덱스 (DXY)", "symbol": "DX-Y.NYB", "unit": "", "emoji": "💵", "pct": True},
    {"key": "usdkrw", "name": "원/달러 환율", "symbol": "USD/KRW", "unit": "₩", "emoji": "💱", "pct": True},
]


def markets() -> dict:
    """금·은·유가·달러인덱스·환율 스냅샷."""
    def build():
        out = []
        for d in _MARKET_DEFS:
            cur, prev, date = _last_two(d["symbol"])
            chg = None
            if cur is not None and prev:
                chg = round((cur - prev) / prev * 100, 2)
            out.append({
                "key": d["key"], "name": d["name"], "emoji": d["emoji"], "unit": d["unit"],
                "value": round(cur, 2) if cur is not None else None,
                "change_pct": chg, "date": date,
            })
        return {"ok": True, "items": out}
    return _cached("markets", 300, build)  # 5분


# ---- 각국 금리·국채 --------------------------------------------------------
# 미국 국채 수익률 곡선(일별, 매우 신뢰) + 각국 장기국채(10Y, OECD 월별).
_US_CURVE = [
    {"key": "us3m", "name": "미국 3개월", "fred": "DGS3MO"},
    {"key": "us2y", "name": "미국 2년", "fred": "DGS2"},
    {"key": "us10y", "name": "미국 10년", "fred": "DGS10"},
    {"key": "us30y", "name": "미국 30년", "fred": "DGS30"},
]
_GLOBAL_10Y = [
    {"key": "kr", "name": "🇰🇷 한국 10년", "fred": "IRLTLT01KRM156N"},
    {"key": "us", "name": "🇺🇸 미국 10년", "fred": "DGS10"},
    {"key": "jp", "name": "🇯🇵 일본 10년", "fred": "IRLTLT01JPM156N"},
    {"key": "eu", "name": "🇪🇺 유럽(독일) 10년", "fred": "IRLTLT01DEM156N"},
    {"key": "za", "name": "🌍 아프리카(남아공) 10년", "fred": "IRLTLT01ZAM156N"},
]
def _fred_last_two(series: str):
    """FRED 시리즈 마지막 2개 값 + 날짜. 실패 시 (None,None,None)."""
    return _last_two(f"FRED:{series}")


def rates() -> dict:
    """미국 수익률 곡선 + 각국 10년 국채 + 정책금리. 변화는 직전대비 bp(0.01%p)."""
    def one(defn):
        cur, prev, date = _fred_last_two(defn["fred"])
        bp = None
        if cur is not None and prev is not None:
            bp = round((cur - prev) * 100, 1)  # %p 차이를 bp로
        return {"key": defn["key"], "name": defn["name"],
                "value": round(cur, 3) if cur is not None else None,
                "change_bp": bp, "date": date}

    def build():
        return {
            "ok": True,
            "us_curve": [one(d) for d in _US_CURVE],   # 일별(신뢰도 높음)
            "global_10y": [one(d) for d in _GLOBAL_10Y],  # OECD 월별
            # 중국 장기국채는 FRED 공개시리즈에 없음(데이터 제한).
            "notes": ["미국 곡선(3M·2Y·10Y·30Y)은 일별 갱신, 각국 10년(OECD)은 월별이라 1~2개월 지연될 수 있습니다.",
                      "중국 장기국채 금리는 무료 공개시리즈(FRED)에 없어 제외했습니다."],
        }
    return _cached("rates", 6 * 3600, build)  # 6시간


# ---- 공모주 청약 달력 (38커뮤니케이션) ------------------------------------
def ipo_calendar(limit: int = 30) -> dict:
    """38.co.kr 공모주 청약일정 — 종목명·청약일·공모가·주간사(증권사)."""
    def build():
        import requests
        url = "http://www.38.co.kr/html/fund/index.htm?o=k"
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            r.encoding = "euc-kr"
            html = r.text
        except Exception as exc:
            return {"ok": False, "items": [], "error": f"청약 일정 조회 실패: {exc}", "source": "38.co.kr"}

        import html as H
        # 날짜 행이 가장 많은 테이블을 선택(메뉴 등과 구분).
        tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S)
        best, best_n = None, 0
        for tab in tables:
            n = len(re.findall(r"\d{4}\.\d{2}\.\d{2}", tab))
            if n > best_n:
                best_n, best = n, tab
        items = []
        if best:
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", best, re.S):
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
                txt = [re.sub(r"\s+", " ", H.unescape(re.sub("<[^>]+>", " ", c))).strip() for c in cells]
                # 컬럼: 종목명 | 공모주일정 | 확정공모가 | 희망공모가 | 청약경쟁률 | 주간사 | 분석
                if len(txt) < 6 or not re.search(r"\d{4}\.\d{2}\.\d{2}", txt[1] if len(txt) > 1 else ""):
                    continue
                fixed = txt[2].strip()
                band = txt[3].strip()
                price = fixed if fixed and fixed != "-" else band
                items.append({
                    "name": txt[0],
                    "schedule": txt[1],
                    "price": price or "-",
                    "underwriter": txt[5] if len(txt) > 5 else "-",
                })
        items = items[:limit]
        return {"ok": bool(items), "items": items, "source": "38.co.kr",
                "error": "" if items else "파싱된 일정이 없습니다(사이트 구조 변경 가능)."}
    return _cached(f"ipo:{limit}", 3 * 3600, build)  # 3시간


# ---- 부동산 거래량 (국토부 실거래가, 키 필요) -----------------------------
def real_estate() -> dict:
    """아파트(3000세대+ 대단지) 거래량. 국토부 실거래가 API 키가 있어야 활성.

    무료지만 data.go.kr 키 발급 + 단지별 세대수 매칭이 필요해 키 미설정 시 안내만 반환.
    """
    import os
    from app.config import settings
    key = getattr(settings, "molit_api_key", None) or os.environ.get("MOLIT_API_KEY")
    if not key:
        return {
            "ok": False,
            "enabled": False,
            "message": "부동산 거래량(아파트 3000세대+ 대단지)은 국토교통부 실거래가 OpenAPI 키가 필요합니다.",
            "howto": "data.go.kr에서 '아파트매매 실거래가' 활용신청(무료) → 받은 키를 .env MOLIT_API_KEY 에 넣으면 활성화됩니다.",
            "link": "https://www.data.go.kr/data/15058747/openapi.do",
        }
    # 키가 있으면 여기서 국토부 API 호출 + 3000세대+ 단지 필터링(후속 구현).
    return {"ok": False, "enabled": True, "message": "키 감지됨 — 거래량 조회 구현 예정.", "items": []}
