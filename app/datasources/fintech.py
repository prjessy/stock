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


# ---- 비트코인 실시간 (업비트 공개 API · 무료·무키) ------------------------
def btc_live() -> dict:
    """업비트 BTC 실시간 — 원화(KRW) + 달러(USDT≈USD) 동시. 5초 캐시(거의 실시간).

    {ok, price(KRW), change_pct, usd, usd_change_pct}. 업비트 공개 API(키 불필요). 실패 시 {ok:False}.
    """
    def build():
        import requests
        try:
            r = requests.get("https://api.upbit.com/v1/ticker",
                             params={"markets": "KRW-BTC,USDT-BTC"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            r.raise_for_status()
            arr = {d["market"]: d for d in r.json()}
            out = {"ok": False}
            k = arr.get("KRW-BTC")
            if k:
                out["ok"] = True
                out["price"] = round(float(k["trade_price"]))
                out["change_pct"] = round(float(k.get("signed_change_rate") or 0) * 100, 2)
            u = arr.get("USDT-BTC")
            if u:
                out["usd"] = round(float(u["trade_price"]))
                out["usd_change_pct"] = round(float(u.get("signed_change_rate") or 0) * 100, 2)
            return out
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return _cached("btc_live", 5, build)


def btc_spark() -> list:
    """업비트 KRW-BTC 최근 20일 종가(오래된→최신, 원화). 실패 시 []. 1시간 캐시."""
    def build():
        import requests
        try:
            r = requests.get("https://api.upbit.com/v1/candles/days",
                             params={"market": "KRW-BTC", "count": 20},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            r.raise_for_status()
            data = r.json()  # 최신순 → 뒤집어 오래된→최신
            return [round(float(c["trade_price"])) for c in data][::-1]
        except Exception:
            return []
    return _cached("btc_spark", 3600, build)


# ---- 금·원자재·환율 --------------------------------------------------------
_MARKET_DEFS = [
    {"key": "gold", "name": "금 (현물 USD/oz)", "symbol": "GC=F", "unit": "$", "emoji": "🥇", "pct": True},
    {"key": "silver", "name": "은 (USD/oz)", "symbol": "SI=F", "unit": "$", "emoji": "🥈", "pct": True},
    {"key": "wti", "name": "WTI 원유 (USD/bbl)", "symbol": "CL=F", "unit": "$", "emoji": "🛢️", "pct": True},
    {"key": "dxy", "name": "달러 인덱스 (DXY)", "symbol": "DX-Y.NYB", "unit": "", "emoji": "💵", "pct": True},
    {"key": "usdkrw", "name": "원/달러 환율", "symbol": "USD/KRW", "unit": "₩", "emoji": "💱", "pct": True},
    {"key": "btc", "name": "비트코인 (실시간·KRW)", "symbol": "BTC/USD", "unit": "₩", "emoji": "₿", "pct": True},
]


# 핀 차트 스트립(대시보드 상단) — 지수·원자재·환율·코인 + 미니 스파크라인.
_PIN_DEFS = [
    {"key": "kospi", "name": "KOSPI", "symbol": "KS11", "unit": ""},
    {"key": "sp500", "name": "S&P 500", "symbol": "US500", "unit": ""},
    {"key": "nasdaq", "name": "NASDAQ", "symbol": "IXIC", "unit": ""},
    {"key": "usdkrw", "name": "USD/KRW", "symbol": "USD/KRW", "unit": "₩"},
    {"key": "dxy", "name": "달러인덱스", "symbol": "DX-Y.NYB", "unit": ""},
    {"key": "btc", "name": "비트코인", "symbol": "BTC/USD", "unit": "₩"},
    {"key": "wti", "name": "WTI", "symbol": "CL=F", "unit": "$"},
    {"key": "gold", "name": "금", "symbol": "GC=F", "unit": "$"},
]


def pins() -> dict:
    """대시보드 상단 핀 차트 — 값·등락·스파크라인(최근 20영업일 종가)."""
    def build():
        out = []
        for d in _PIN_DEFS:
            try:
                # 비트코인: 값·등락·스파크라인 모두 업비트(KRW) 실시간/원화 일봉으로.
                if d["key"] == "btc":
                    sp = btc_spark()
                    if sp:
                        cur = sp[-1]
                        prev = sp[-2] if len(sp) >= 2 else cur
                        chg = round((cur - prev) / prev * 100, 2) if prev else 0.0
                    else:
                        cur, chg = None, 0.0
                    live = btc_live()
                    if live.get("ok"):
                        cur, chg = live["price"], live["change_pct"]
                    if cur is None:
                        continue
                    out.append({"key": "btc", "name": d["name"], "unit": d["unit"],
                                "value": cur, "change_pct": chg, "spark": sp,
                                "usd": live.get("usd"), "usd_change_pct": live.get("usd_change_pct")})
                    continue
                df = _fdr().DataReader(d["symbol"]).dropna()
                closes = [round(float(x), 2) for x in df["Close"].dropna().tolist()[-20:]]
                if not closes:
                    continue
                cur = closes[-1]
                prev = closes[-2] if len(closes) >= 2 else cur
                chg = round((cur - prev) / prev * 100, 2) if prev else 0.0
                out.append({"key": d["key"], "name": d["name"], "unit": d["unit"],
                            "value": cur, "change_pct": chg, "spark": closes})
            except Exception:
                continue
        return {"ok": True, "items": out}
    return _cached("pins", 600, build)  # 10분


def markets() -> dict:
    """금·은·유가·달러인덱스·환율 스냅샷."""
    def build():
        out = []
        for d in _MARKET_DEFS:
            # 비트코인은 업비트 실시간(KRW). 실패 시 FDR(USD)로 폴백.
            if d["key"] == "btc":
                live = btc_live()
                if live.get("ok"):
                    out.append({"key": "btc", "name": d["name"], "emoji": d["emoji"],
                                "unit": d["unit"], "value": live["price"],
                                "change_pct": live["change_pct"], "date": "실시간",
                                "usd": live.get("usd"), "usd_change_pct": live.get("usd_change_pct")})
                    continue
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


# ---- 공모주 일정: 38커뮤니케이션(청약일정+신규상장) 우선, 네이버 폴백 ----------
# 38커뮤니케이션이 청약일(공모청약일정)과 상장일(신규상장)을 모두·완전하게 제공한다.
# 단, 38.co.kr 은 구형 TLS 서버라 일반 requests 로는 SSL 핸드셰이크가 실패 → 레거시 SSL 어댑터로 접속.
def _legacy_session():
    """구형 TLS(38.co.kr 등) 접속용 requests 세션 — 보안레벨 1·레거시 재협상 허용."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context

    class _LegacyTLS(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            ctx = create_urllib3_context()
            try:
                ctx.set_ciphers("DEFAULT@SECLEVEL=1")
                ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
            except Exception:
                pass
            k["ssl_context"] = ctx
            super().init_poolmanager(*a, **k)

    s = requests.Session()
    s.mount("https://", _LegacyTLS())
    return s


_DNS_LOCK = __import__("threading").Lock()


def _doh_resolve(host: str):
    """공개 DoH(1.1.1.1, IP리터럴이라 시스템 DNS 불필요)로 A레코드 조회. 1시간 캐시. 실패 시 None.

    일부 VPS의 로컬 리졸버(systemd-resolved)가 38.co.kr 등 특정 도메인을 해석 못 해도
    이 경로로 직접 IP를 얻어 접속할 수 있게 한다(시스템 DNS 변경 불필요).
    """
    def build():
        import requests
        try:
            r = requests.get("https://1.1.1.1/dns-query",
                             params={"name": host, "type": "A"},
                             headers={"accept": "application/dns-json"}, timeout=8)
            for ans in r.json().get("Answer", []):
                if ans.get("type") == 1:
                    return ans["data"]
        except Exception:
            return None
        return None
    return _cached(f"doh:{host}", 3600, build)


def _pin_host(host: str, ip: str):
    """socket.getaddrinfo 에서 host→ip 만 강제하는 컨텍스트매니저(그 외 호스트는 원함수).
    URL/Host헤더/SNI/인증서검증은 그대로라 안전. ip 가 없으면 아무것도 안 함."""
    from contextlib import contextmanager, nullcontext
    if not ip:
        return nullcontext()

    @contextmanager
    def _cm():
        import socket as _s
        orig = _s.getaddrinfo

        def patched(h, port=None, *a, **k):
            if h == host:
                return [(_s.AF_INET, _s.SOCK_STREAM, 6, "", (ip, port))]
            return orig(h, port, *a, **k)

        with _DNS_LOCK:
            _s.getaddrinfo = patched
            try:
                yield
            finally:
                _s.getaddrinfo = orig
    return _cm()


def _38_rows(session, o: str, pages: int) -> list[list[str]]:
    """38커뮤니케이션 표 페이지(o=k 청약일정 / o=nw 신규상장)에서 <tr>별 셀 리스트.
    각 셀은 태그 제거·공백 정리한 문자열. 헤더/빈 행은 호출부가 날짜로 거른다."""
    import html as H
    out: list[list[str]] = []
    ip = _doh_resolve("www.38.co.kr")   # VPS 로컬 DNS가 38을 못 풀어도 접속되게
    with _pin_host("www.38.co.kr", ip):
      for pg in range(1, pages + 1):
        try:
            r = session.get("https://www.38.co.kr/html/fund/index.htm",
                            params={"o": o, "page": pg},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.encoding = "euc-kr"
        except Exception:
            break
        for tr in re.split(r"<tr\b", r.text, flags=re.I)[1:]:
            cells = []
            for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, flags=re.I | re.S):
                txt = H.unescape(re.sub("<[^>]+>", " ", cell)).replace("\xa0", " ")
                cells.append(re.sub(r"\s+", " ", txt).strip())
            if cells:
                out.append(cells)
    return out


def _ipo_from_38(limit: int, q: str = "") -> dict:
    """38커뮤니케이션 — 청약일정(o=k)+신규상장(o=nw) 병합. 종목명·청약일·공모가·주관사·경쟁률·상장일."""
    s = _legacy_session()
    pages = 6 if q else 1   # 검색이면 여러 페이지(과거 종목까지), 평소엔 최근+임박 1페이지

    # 1) 공모청약일정: [종목명, 청약일(YYYY.MM.DD~MM.DD), 확정공모가, 희망공모가, (경쟁률), 주간사, ...]
    sub: dict[str, dict] = {}
    for c in _38_rows(s, "k", pages):
        if len(c) < 6 or not re.match(r"\d{4}\.\d{2}\.\d{2}\s*~\s*\d{2}\.\d{2}", c[1]):
            continue
        name = c[0]
        fixed, hope, rate, under = c[2], c[3], c[4], c[5]
        price = fixed if (fixed and fixed != "-") else (hope or "-")
        sub[name] = {
            "schedule": c[1].replace(" ", ""),
            "price": (price + "원") if (price and price != "-") else "-",
            "rate": rate if (rate and ":" in rate) else "-",
            "underwriter": under or "-",
        }

    # 2) 신규상장: [기업명, 신규상장일(YYYY/MM/DD), 현재가, 전일비, 공모가, 등락률, ...]
    listed: dict[str, dict] = {}
    for c in _38_rows(s, "nw", pages):
        if len(c) < 5:
            continue
        m = re.match(r"(\d{4})/(\d{2})/(\d{2})", c[1])
        if not m:
            continue
        listed[c[0]] = {
            "listing": f"{m.group(1)}.{m.group(2)}.{m.group(3)}",
            "ipo_price": c[4],
            "cur_price": c[2] if (c[2] and "%" not in c[2] and c[2] != "-") else "",
            "cur_change": c[3] if (len(c) > 3 and "%" in c[3]) else "",
        }

    names = list(dict.fromkeys(list(sub.keys()) + list(listed.keys())))
    items = []
    for name in names:
        if q and q not in name:
            continue
        b = sub.get(name, {})
        L = listed.get(name, {})
        price = b.get("price", "-")
        if (not price or price == "-") and L.get("ipo_price"):
            price = L["ipo_price"] + "원"
        items.append({
            "name": name,
            "market": "",
            "schedule": b.get("schedule", "-"),
            "price": price or "-",
            "underwriter": b.get("underwriter", "-"),
            "rate": b.get("rate", "-"),
            "listing": L.get("listing", "-"),
            "listed_price": L.get("cur_price", ""),
            "listed_change": L.get("cur_change", ""),
        })

    def _key(it):  # 청약시작일(없으면 상장일) 기준 최신 우선
        s0 = it["schedule"][:10] if it["schedule"] not in ("", "-") else ""
        return s0 or (it["listing"] if it["listing"] != "-" else "")
    items.sort(key=_key, reverse=True)
    if not q:
        items = items[:limit]
    return {"ok": bool(items), "items": items, "source": "38커뮤니케이션",
            "error": "" if items else "일정을 가져오지 못했습니다(구조 변경 가능)."}


def _ipo_from_naver(limit: int) -> dict:
    """네이버 금융 IPO 폴백 — 임박 청약만(신규상장/당일상장 종목은 누락될 수 있음)."""
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
            "listed_price": "", "listed_change": "",
        })
    return {"ok": bool(items), "items": items, "source": "naver",
            "error": "" if items else "파싱된 일정이 없습니다(구조 변경 가능)."}


def ipo_calendar(limit: int = 40, q: str = "") -> dict:
    """공모주 일정 — 종목·청약일·공모가·주관사·경쟁률·상장일. 38커뮤니케이션 우선, 실패 시 네이버.

    q 주어지면 종목명 검색(38은 여러 페이지에서 과거 종목까지 탐색).
    """
    q = (q or "").strip()

    def build():
        try:
            res = _ipo_from_38(limit, q)
            if res.get("items"):
                return res
        except Exception:
            pass
        # 폴백: 네이버(임박 청약만)
        try:
            res = _ipo_from_naver(limit)
            if q:
                res["items"] = [it for it in res.get("items", []) if q in it["name"]]
                res["ok"] = bool(res["items"])
            if res.get("items"):
                return res
        except Exception as exc:
            return {"ok": False, "items": [], "error": f"공모주 일정 조회 실패: {exc}", "source": "38/naver"}
        return {"ok": False, "items": [], "error": "공모주 일정을 가져오지 못했습니다.", "source": "38/naver"}

    return _cached(f"ipo:{limit}:{q}", 3 * 3600, build)  # 3시간


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

# 전국 시/도 → {시군구명: LAWD_CD(5자리)}. API로 검증된 코드(강원=51·전북=52 특별자치도 반영).
_REGIONS: dict[str, dict[str, str]] = {
    "서울": {"종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
            "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
            "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
            "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
            "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
            "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710", "강동구": "11740"},
    "부산": {"중구": "26110", "서구": "26140", "동구": "26170", "영도구": "26200", "부산진구": "26230",
            "동래구": "26260", "남구": "26290", "북구": "26320", "해운대구": "26350", "사하구": "26380",
            "금정구": "26410", "강서구": "26440", "연제구": "26470", "수영구": "26500", "사상구": "26530", "기장군": "26710"},
    "대구": {"중구": "27110", "동구": "27140", "서구": "27170", "남구": "27200", "북구": "27230",
            "수성구": "27260", "달서구": "27290", "달성군": "27710"},
    "인천": {"중구": "28110", "동구": "28140", "미추홀구": "28177", "연수구": "28185", "남동구": "28200",
            "부평구": "28237", "계양구": "28245", "서구": "28260"},
    "광주": {"동구": "29110", "서구": "29140", "남구": "29155", "북구": "29170", "광산구": "29200"},
    "대전": {"동구": "30110", "중구": "30140", "서구": "30170", "유성구": "30200", "대덕구": "30230"},
    "울산": {"중구": "31110", "남구": "31140", "동구": "31170", "북구": "31200", "울주군": "31710"},
    "세종": {"세종시": "36110"},
    "경기": {"수원장안": "41111", "수원권선": "41113", "수원팔달": "41115", "수원영통": "41117",
            "성남수정": "41131", "성남중원": "41133", "성남분당": "41135", "고양덕양": "41281",
            "고양일산동": "41285", "고양일산서": "41287", "용인처인": "41461", "용인기흥": "41463",
            "용인수지": "41465", "부천원미": "41192", "부천소사": "41196", "안양만안": "41171",
            "안양동안": "41173", "안산상록": "41271", "안산단원": "41273", "남양주": "41360",
            "의정부": "41150", "평택시": "41220", "시흥시": "41390", "김포시": "41570",
            "광명시": "41210", "광주시": "41610", "군포시": "41410", "하남시": "41450",
            "오산시": "41370", "이천시": "41500"},
    "강원": {"춘천시": "51110", "원주시": "51130", "강릉시": "51150", "동해시": "51170", "속초시": "51210"},
    "충북": {"청주상당": "43111", "청주서원": "43112", "청주흥덕": "43113", "청주청원": "43114",
            "충주시": "43130", "제천시": "43150"},
    "충남": {"천안동남": "44131", "천안서북": "44133", "공주시": "44150", "아산시": "44200",
            "서산시": "44210", "논산시": "44230", "당진시": "44270"},
    "전북": {"전주완산": "52111", "전주덕진": "52113", "군산시": "52130", "익산시": "52140", "정읍시": "52180"},
    "전남": {"목포시": "46110", "여수시": "46130", "순천시": "46150", "나주시": "46170", "광양시": "46230"},
    "경북": {"포항남구": "47111", "포항북구": "47113", "경주시": "47130", "구미시": "47190",
            "경산시": "47290", "안동시": "47170"},
    "경남": {"창원의창": "48121", "창원성산": "48123", "창원마산합포": "48125", "창원마산회원": "48127",
            "창원진해": "48129", "진주시": "48170", "김해시": "48250", "양산시": "48330", "거제시": "48310"},
    "제주": {"제주시": "50110", "서귀포시": "50130"},
}
# code → (시도, 시군구명) 평탄화
_GU = {code: (sido, gu) for sido, d in _REGIONS.items() for gu, code in d.items()}

_MOLIT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


def _household_of(apt_name: str):
    """아는 대단지면 세대수 반환(배지용), 모르면 None."""
    for c in _BIG_COMPLEXES:
        if c["name"] in (apt_name or ""):
            return c["households"]
    return None


def _molit_key():
    """MOLIT 키: .env > env > 로컬 key3.txt(개발용) 순."""
    import os
    from app.config import settings
    key = getattr(settings, "molit_api_key", None) or os.environ.get("MOLIT_API_KEY")
    if key:
        return key
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "key3.txt"
        if p.exists():
            for ln in (x.strip() for x in p.read_text(encoding="utf-8").splitlines()):
                if len(ln) >= 32 and "://" not in ln and "/" not in ln:
                    return ln
    except Exception:
        pass
    return None


def _norm_trade(r: dict) -> dict:
    """실거래 1건 → 표시용 정규화."""
    try:
        amt = int((r.get("dealAmount") or "0").replace(",", ""))
    except Exception:
        amt = 0
    try:
        area = round(float(r.get("excluUseAr") or 0), 1)
    except Exception:
        area = None
    y, m, d = r.get("dealYear", ""), r.get("dealMonth") or 0, r.get("dealDay") or 0
    return {
        "apt": r.get("aptNm") or "",
        "dong": r.get("umdNm") or r.get("estateAgentSggNm") or "",
        "area": area,                         # 전용면적 ㎡
        "floor": r.get("floor") or "",
        "amount_eok": round(amt / 10000, 2) if amt else None,  # 만원→억
        "date": f"{y}.{int(m):02d}.{int(d):02d}" if y else "",
        "build_year": r.get("buildYear") or "",
        "households": _household_of(r.get("aptNm") or ""),
    }


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


def _gu_trades(key: str, lawd: str, months: list[str]) -> list[dict]:
    """한 자치구의 최근 months 실거래 raw(캐시). 구별 6시간."""
    def build():
        rows = []
        for ymd in months:
            rows += _molit_trades(key, lawd, ymd)
        return rows
    return _cached(f"re:{lawd}:{','.join(months)}", 6 * 3600, build)


_KEY_NEEDED = {
    "ok": False, "enabled": False,
    "message": "부동산 실거래는 국토교통부 실거래가 OpenAPI 키가 필요합니다.",
    "howto": "data.go.kr '아파트매매 실거래가 상세자료' 활용신청(무료) → 키를 .env MOLIT_API_KEY 에.",
    "link": "https://www.data.go.kr/data/15058747/openapi.do",
}


def real_estate(mode: str = "top", sido: str | None = None,
                lawd: str | None = None, q: str | None = None) -> dict:
    """전국 아파트 실거래(국토부).
    - mode=top: 선택 시/도(기본 서울)의 최근월 거래량 단지 TOP10.
    - mode=search: 시군구(lawd) 또는 시/도(sido) 범위에서 단지명(q) 실거래 목록.
    항상 regions(시/도→시군구) 동봉(캐스케이딩 드롭다운용).
    """
    key = _molit_key()
    regions = {sd: [{"code": c, "name": n} for n, c in d.items()] for sd, d in _REGIONS.items()}
    if not key:
        return {**_KEY_NEEDED, "regions": regions}

    q = (q or "").strip()
    sido = sido if (sido in _REGIONS) else "서울"

    if mode == "search":
        months = _recent_ymd(2)
        if lawd and lawd in _GU:
            targets, scope = [lawd], f"{_GU[lawd][0]} {_GU[lawd][1]}"
        else:
            targets, scope = list(_REGIONS[sido].values()), f"{sido} 전체"
        trades = []
        for gu in targets:
            for r in _gu_trades(key, gu, months):
                apt = r.get("aptNm") or ""
                if q and q not in apt:
                    continue
                t = _norm_trade(r)
                t["gu"] = _GU.get(gu, ("", ""))[1]
                trades.append(t)
        trades.sort(key=lambda x: x["date"], reverse=True)
        return {
            "ok": True, "enabled": True, "mode": "search",
            "items": trades[:200], "count": len(trades),
            "period": f"{months[-1]}~{months[0]}", "scope": scope,
            "query": q, "regions": regions, "source": "국토교통부 실거래가",
        }

    # mode=top — 선택 시/도 최근월 단지별 거래량 TOP10
    def build_top():
        months = _recent_ymd(1)
        agg: dict[str, dict] = {}
        for gu in _REGIONS[sido].values():
            for r in _gu_trades(key, gu, months):
                apt = r.get("aptNm") or ""
                if not apt:
                    continue
                k = f"{gu}|{apt}"
                a = agg.setdefault(k, {"apt": apt, "gu": _GU[gu][1], "trades": 0,
                                       "amts": [], "households": _household_of(apt)})
                a["trades"] += 1
                try:
                    a["amts"].append(int((r.get("dealAmount") or "0").replace(",", "")))
                except Exception:
                    pass
        items = []
        for a in agg.values():
            amts = a.pop("amts")
            a["avg_eok"] = round(sum(amts) / len(amts) / 10000, 1) if amts else None
            items.append(a)
        items.sort(key=lambda x: x["trades"], reverse=True)
        return {
            "ok": True, "enabled": True, "mode": "top", "items": items[:10],
            "period": months[0], "sido": sido, "source": "국토교통부 실거래가",
            "note": f"{sido} 최근월 거래량 상위 단지. 3000세대+ 대단지는 세대수 배지.",
        }
    res = _cached(f"re:top:{sido}", 6 * 3600, build_top)
    res["regions"] = regions
    return res
