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


# ---- 공모주 청약 달력 (네이버 금융, VPS 접속 가능) -------------------------
def _ipo_from_naver(limit: int) -> dict:
    """네이버 금융 IPO 페이지 파싱 — 종목·청약일·공모가·주관사·상장일."""
    import requests
    import html as H
    r = requests.get("https://finance.naver.com/sise/ipo.naver",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    r.encoding = "euc-kr"
    full = re.sub(r"\s+", " ", H.unescape(re.sub("<[^>]+>", " ", r.text)))
    # (시장)(종목명) ... 공모가 X ... 주관사 Y ... 개인청약 YY.MM.DD~MM.DD [... 상장일 YY.MM.DD]
    blocks = re.findall(
        r"(코스닥|코스피|코넥스)\s+([^\s]+)\s+공모가\s*([\d,]+)"
        r".*?주관사\s*([가-힣A-Za-z0-9]+(?:증권|투자증권|금융투자)?)"
        r"(?:.*?개인청약경쟁률\s*([\d,.]+\s*:\s*1))?"
        r".*?개인청약\s*(\d{2}\.\d{2}\.\d{2}\s*~\s*\d{2}\.\d{2})"
        r"(?:.*?상장일\s*(\d{2}\.\d{2}\.\d{2}))?", full)
    items = []
    for mk, name, price, under, rate, sched, listing in blocks[:limit]:
        items.append({
            "name": name,
            "market": mk,
            "schedule": "20" + sched.replace(" ", ""),   # YY→YYYY
            "price": price + "원" if price else "-",
            "underwriter": under or "-",
            "rate": (rate.replace(" ", "") if rate else "-"),   # 개인청약 경쟁률
            "listing": ("20" + listing) if listing else "-",
        })
    return {"ok": bool(items), "items": items, "source": "naver",
            "error": "" if items else "파싱된 일정이 없습니다(구조 변경 가능)."}


def ipo_calendar(limit: int = 30) -> dict:
    """공모주 청약일정 — 종목·청약일·공모가·주관사·상장일. 네이버 우선, 실패 시 안내."""
    def build():
        try:
            res = _ipo_from_naver(limit)
            if res.get("items"):
                return res
        except Exception as exc:
            return {"ok": False, "items": [], "error": f"청약 일정 조회 실패: {exc}", "source": "naver"}
        return {"ok": False, "items": [], "error": "청약 일정을 가져오지 못했습니다.", "source": "naver"}
    return _cached(f"ipo:{limit}", 3 * 3600, build)  # 3시간


# ---- 부동산 거래량 (국토부 아파트매매 실거래가, 3000세대+ 대단지만) ----------
# 거래 API에는 세대수가 없어, 잘 알려진 3000세대 이상 대단지를 화이트리스트로 둔다.
# {name: aptNm 매칭 키워드, lawd: 법정동 시군구 5자리, region: 표시, households: 세대수}
_BIG_COMPLEXES = [
    {"name": "헬리오시티", "lawd": "11710", "region": "송파 가락", "households": 9510},
    {"name": "파크리오", "lawd": "11710", "region": "송파 신천", "households": 6864},
    {"name": "잠실엘스", "lawd": "11710", "region": "송파 잠실", "households": 5678},
    {"name": "리센츠", "lawd": "11710", "region": "송파 잠실", "households": 5563},
    {"name": "트리지움", "lawd": "11710", "region": "송파 잠실", "households": 3696},
    {"name": "올림픽선수기자촌", "lawd": "11710", "region": "송파 방이", "households": 5540},
    {"name": "올림픽파크포레온", "lawd": "11740", "region": "강동 둔촌", "households": 12032},
    {"name": "고덕그라시움", "lawd": "11740", "region": "강동 고덕", "households": 4932},
    {"name": "고덕아르테온", "lawd": "11740", "region": "강동 고덕", "households": 4066},
    {"name": "은마", "lawd": "11680", "region": "강남 대치", "households": 4424},
    {"name": "개포자이프레지던스", "lawd": "11680", "region": "강남 개포", "households": 3375},
    {"name": "마포래미안푸르지오", "lawd": "11440", "region": "마포 아현", "households": 3885},
]

_MOLIT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


def _recent_ymd(n: int = 2) -> list[str]:
    """최근 n개월 YYYYMM (당월 포함, 최신순). datetime 사용(서버 기준)."""
    import datetime as _dt
    today = _dt.date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def _molit_trades(key: str, lawd: str, ymd: str) -> list[dict]:
    """국토부 실거래 1지역·1개월 조회 → item dict 목록(XML 파싱). 실패 시 []."""
    import requests
    import xml.etree.ElementTree as ET
    try:
        r = requests.get(_MOLIT_URL, params={
            "serviceKey": key, "LAWD_CD": lawd, "DEAL_YMD": ymd,
            "numOfRows": "1000", "pageNo": "1",
        }, timeout=15)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        if (root.findtext(".//resultCode") or "") not in ("000", "00"):
            return []
        rows = []
        for it in root.iter("item"):
            rows.append({c.tag: (c.text or "").strip() for c in it})
        return rows
    except Exception:
        return []


def real_estate() -> dict:
    """아파트 3000세대+ 대단지 거래량(국토부 실거래가). 키 없으면 안내만."""
    import os
    from app.config import settings
    key = getattr(settings, "molit_api_key", None) or os.environ.get("MOLIT_API_KEY")
    # 키가 .env에 없으면 PC 로컬 key3.txt 폴백(개발 편의 · VPS엔 .env).
    if not key:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parents[2] / "key3.txt"
            if p.exists():
                lines = [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
                for ln in lines:
                    if len(ln) >= 32 and "://" not in ln and "/" not in ln:
                        key = ln
                        break
        except Exception:
            pass
    if not key:
        return {
            "ok": False, "enabled": False,
            "message": "부동산 거래량(아파트 3000세대+ 대단지)은 국토교통부 실거래가 OpenAPI 키가 필요합니다.",
            "howto": "data.go.kr '아파트매매 실거래가 상세자료' 활용신청(무료) → 키를 .env MOLIT_API_KEY 에.",
            "link": "https://www.data.go.kr/data/15058747/openapi.do",
        }

    def build():
        months = _recent_ymd(2)
        lawds = sorted({c["lawd"] for c in _BIG_COMPLEXES})
        # 지역·월별 실거래를 모아둔다(중복 호출 방지).
        cache: dict[str, list[dict]] = {}
        for lawd in lawds:
            rows = []
            for ymd in months:
                rows += _molit_trades(key, lawd, ymd)
            cache[lawd] = rows

        items = []
        for c in _BIG_COMPLEXES:
            rows = cache.get(c["lawd"], [])
            matched = [r for r in rows if c["name"] in (r.get("aptNm") or "")]
            if not matched:
                items.append({**c, "trades": 0, "avg_eok": None, "min_eok": None,
                              "max_eok": None, "last_deal": None})
                continue
            amts = []
            last = ""
            for r in matched:
                try:
                    amts.append(int((r.get("dealAmount") or "0").replace(",", "")))  # 만원
                except Exception:
                    pass
                d = f"{r.get('dealYear','')}-{int(r.get('dealMonth') or 0):02d}-{int(r.get('dealDay') or 0):02d}"
                last = max(last, d)
            eok = lambda v: round(v / 10000, 1) if v else None  # 만원→억
            items.append({
                **c, "trades": len(matched),
                "avg_eok": eok(sum(amts) // len(amts)) if amts else None,
                "min_eok": eok(min(amts)) if amts else None,
                "max_eok": eok(max(amts)) if amts else None,
                "last_deal": last or None,
            })
        items.sort(key=lambda x: x["trades"], reverse=True)
        return {
            "ok": True, "enabled": True, "items": items,
            "period": f"{months[-1]}~{months[0]}", "source": "국토교통부 실거래가",
            "note": "세대수 3000+ 주요 대단지(서울)만. 거래 API엔 세대수가 없어 대표 단지를 선별 집계.",
        }
    return _cached("realestate", 6 * 3600, build)  # 6시간
