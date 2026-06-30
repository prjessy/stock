"""국내 실시간 시세 — 한국투자증권(KIS) OpenAPI 기반.

현재가(주식현재가 시세, tr_id=FHKST01010100)를 실시간으로 조회한다. 무료 소스(FDR,
~15분 지연)와 달리 거의 실시간이라 프리장/본장/에프터장 내내 초단위 갱신에 쓸 수 있다.

- 액세스 토큰은 data/kis_token.json 에 캐시한다(유효기간 24h, KIS 가 토큰 발급을
  분당 1회로 제한하므로 캐시 필수). 만료 임박 시에만 재발급.
- 이력(OHLC)은 KIS 일봉 대신 기존 FinanceDataReader 에 위임한다(차트는 일봉이면 충분).
- 네트워크/장외/휴장 등 실패 시 예외를 올리지 않고 empty_quote 를 반환한다(부분 실패 격리).

키(appkey/appsecret)는 settings(.env)에서만 읽으며 절대 커밋되지 않는다.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

from app.config import settings
from app.datasources.base import PriceSource, TTLCache, empty_quote
from app.datasources.kr_price import KR_META, KrPriceSource, resolve_name as _kr_resolve

_TOKEN_PATH = Path(settings.db_path).resolve().parent / "kis_token.json"
# 만료 이만큼 전이면 미리 재발급(초).
_TOKEN_REFRESH_BUFFER = 600
# 토큰 발급 실패 후 이만큼은 재시도하지 않는다(KIS 발급 제한 = 1분당 1회).
_TOKEN_FAIL_COOLDOWN = 60
_TIMEOUT = 8.0


def _meta(symbol: str) -> dict[str, str]:
    if symbol in KR_META:
        return KR_META[symbol]
    # 하드코딩에 없으면 KRX 전종목 리스트에서 이름 동적 조회(케이뱅크 등).
    return {"name": _kr_resolve(symbol), "note": "코스피/코스닥"}


# 종목코드 → 한글명 캐시(프로세스 전역, 이름은 거의 안 바뀜). KRX 리스트가 막힌 VPS 대비.
_NAME_CACHE: dict[str, str] = {}


class KisPriceSource(PriceSource):
    """KIS OpenAPI 로 국내 실시간 현재가를 조회한다(이력은 FDR 위임)."""

    def __init__(self, cache_ttl: float = 1.0) -> None:
        # 초단위 폴링에 맞춰 짧은 TTL: 같은 1초 내 중복 호출만 캐시.
        self._cache = TTLCache(cache_ttl)
        self._token = ""
        self._token_expiry = 0.0
        self._token_cooldown_until = 0.0  # 발급 실패 후 재시도 억제 시각
        self._lock = threading.Lock()
        self._history = KrPriceSource()  # 이력 위임용
        # 커넥션 재사용(keep-alive): 매 호출 TLS 핸드셰이크를 없애 ~2s → 수백ms.
        self._session = requests.Session()
        self._load_cached_token()

    # ---------------- 토큰 ----------------
    def _load_cached_token(self) -> None:
        try:
            data = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
            self._token = data.get("access_token", "")
            self._token_expiry = float(data.get("expires_at", 0))
        except Exception:
            self._token, self._token_expiry = "", 0.0

    def _save_cached_token(self) -> None:
        try:
            _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_PATH.write_text(
                json.dumps({"access_token": self._token, "expires_at": self._token_expiry}),
                encoding="utf-8",
            )
        except Exception:
            pass  # 캐시 실패는 치명적이지 않음(다음에 재발급)

    def _ensure_token(self) -> str:
        """유효한 액세스 토큰 반환. 만료 임박/없음일 때만 재발급.

        발급 실패 시 _TOKEN_FAIL_COOLDOWN 동안 재시도를 건너뛴다 — KIS 토큰 발급은
        1분당 1회로 제한되므로, 폴러(1초 주기)가 매초 두드려 403이 고착되는 것을 막는다.
        """
        now = time.time()
        if self._token and now < self._token_expiry - _TOKEN_REFRESH_BUFFER:
            return self._token
        with self._lock:
            now = time.time()
            # 락 획득 사이 다른 스레드가 갱신했을 수 있음 — 재확인.
            if self._token and now < self._token_expiry - _TOKEN_REFRESH_BUFFER:
                return self._token
            # 직전 발급 실패 쿨다운 중이면 재시도하지 않는다(하머링 방지).
            if now < self._token_cooldown_until:
                if self._token:
                    return self._token
                raise RuntimeError("KIS 토큰 발급 쿨다운 중 — 잠시 후 재시도")
            try:
                resp = self._session.post(
                    f"{settings.kis_domain}/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": settings.kis_app_key,
                        "appsecret": settings.kis_app_secret,
                    },
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                self._token = data["access_token"]
                self._token_expiry = time.time() + int(data.get("expires_in", 86400))
                self._token_cooldown_until = 0.0
                self._save_cached_token()
                return self._token
            except Exception:
                # 발급 실패 → 쿨다운 설정 후 재-raise(get_quote 가 empty_quote 로 처리).
                self._token_cooldown_until = time.time() + _TOKEN_FAIL_COOLDOWN
                raise

    # ---------------- 시세 ----------------
    def _inquire(self, symbol: str, market_code: str) -> dict:
        """KIS 현재가 조회(시장코드별). 성공 시 {price, prev_close, change_pct}, 실패 시 예외.

        market_code: "J"=KRX 정규장, "NX"=넥스트레이드(NXT, 증권사 '시간외/애프터마켓'), "UN"=통합.
        """
        token = self._ensure_token()
        resp = self._session.get(
            f"{settings.kis_domain}/uapi/domestic-stock/v1/quotations/inquire-price",
            params={"FID_COND_MRKT_DIV_CODE": market_code, "FID_INPUT_ISCD": symbol},
            headers={
                "authorization": f"Bearer {token}",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
                "tr_id": "FHKST01010100",
                "custtype": "P",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("rt_cd") != "0":
            raise ValueError(body.get("msg1", "KIS 오류"))
        out = body.get("output", {}) or {}
        price = _f(out.get("stck_prpr"))
        if price is None:
            raise ValueError("현재가 없음")
        prev_close = _f(out.get("stck_sdpr"))  # 기준가 = 전일 종가
        change_pct = round((price - prev_close) / prev_close * 100.0, 2) if prev_close else 0.0
        return {
            "price": round(price, 2),
            "prev_close": round(prev_close, 2) if prev_close is not None else None,
            "change_pct": change_pct,
        }

    def get_name(self, symbol: str) -> str | None:
        """종목코드 → 한글 약식명(KIS 주식기본조회 CTPF1604R). 프로세스 캐시. 실패 시 None.

        KRX 전종목 리스트가 해외 VPS IP에서 차단돼 이름이 코드로만 나오는 문제 해결용.
        """
        if symbol in _NAME_CACHE:
            return _NAME_CACHE[symbol] or None
        if not settings.kis_app_key or not settings.kis_app_secret:
            return None
        try:
            token = self._ensure_token()
            resp = self._session.get(
                f"{settings.kis_domain}/uapi/domestic-stock/v1/quotations/search-stock-info",
                params={"PRDT_TYPE_CD": "300", "PDNO": symbol},
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": "CTPF1604R",
                    "custtype": "P",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            out = resp.json().get("output", {}) or {}
            name = (out.get("prdt_abrv_name") or out.get("prdt_name") or "").strip()
            _NAME_CACHE[symbol] = name           # 빈값도 캐시(반복 호출 방지)
            return name or None
        except Exception:
            return None

    def get_quote(self, symbol: str) -> dict:
        meta = _meta(symbol)
        # 메타·KRX리스트로 이름을 못 풀어 코드 그대로면 KIS 기본조회로 한글명 보강.
        if meta.get("name") == symbol:
            nm = self.get_name(symbol)
            if nm:
                meta = {**meta, "name": nm}
        cache_key = f"quote:{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if not settings.kis_app_key or not settings.kis_app_secret:
            return empty_quote(symbol, meta["name"], "KRW", meta["note"], "KIS 키 미설정")

        try:
            base = self._inquire(symbol, "J")  # KRX 정규장(대표 가격)
            quote = {
                "symbol": symbol,
                "name": meta["name"],
                "price": base["price"],
                "prev_close": base["prev_close"],
                "change_pct": base["change_pct"],
                "currency": "KRW",
                "note": meta["note"],
                "ts": int(time.time()),
                "source": "KIS",
                "error": "",
            }
            # NXT(넥스트레이드) — 증권사·네이버가 보여주는 '시간외/애프터마켓' 체결가.
            # 레버리지 ETF 등은 NXT 시간외가 없으므로(정규장만) 조회 자체를 건너뛴다(0원 표시 방지).
            # 그 외에도 가격이 0/없음이면 부착하지 않는다(graceful).
            if "레버리지" not in meta["note"]:
                try:
                    nx = self._inquire(symbol, "NX")
                    if nx["price"] and nx["price"] > 0:
                        quote["nxt"] = nx
                except Exception:
                    pass
            self._cache.set(cache_key, quote)
            return quote
        except Exception as exc:
            return empty_quote(symbol, meta["name"], "KRW", meta["note"], f"KIS 조회 실패: {exc}")

    def get_history(self, symbol: str, period: str) -> list[dict]:
        # 일봉 이력은 무료 소스(FDR)로 충분 — KIS 호출을 아낀다.
        return self._history.get_history(symbol, period)

    # ---------------- ETF 구성종목 (더듬이4용) ----------------
    def get_etf_constituents(self, etf_code: str, limit: int = 80) -> list[tuple[str, str]]:
        """ETF 구성종목 [(종목코드, 종목명)] (KIS ETF 구성종목시세 FHKST121600C0).

        실패/휴장(주말엔 output2 빈값)/미지원 시 []. 응답 필드명은 방어적으로 여러 키를 시도한다.
        """
        if not settings.kis_app_key or not settings.kis_app_secret:
            return []
        try:
            token = self._ensure_token()
            resp = self._session.get(
                f"{settings.kis_domain}/uapi/etfetn/v1/quotations/inquire-component-stock-price",
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": etf_code,
                        "FID_COND_SCR_DIV_CODE": "11216"},
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": "FHKST121600C0",
                    "custtype": "P",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("rt_cd") != "0":
                return []
            out = body.get("output2") or []
            res: list[tuple[str, str]] = []
            for r in out[:limit]:
                code = (r.get("stck_shrn_iscd") or r.get("mksc_shrn_iscd")
                        or r.get("stck_cd") or r.get("shrn_iscd"))
                name = (r.get("hts_kor_isnm") or r.get("prdt_abrv_name")
                        or r.get("prdt_name") or code)
                if code:
                    res.append((code, name))
            return res
        except Exception:
            return []

    # ---------------- 재무 요약 ----------------
    def get_fundamentals(self, symbol: str) -> dict:
        """현재가 시세(FHKST01010100) 응답에 포함된 재무지표로 요약을 만든다.

        같은 엔드포인트가 PER/PBR/EPS/BPS/시총/상장주식수/52주고저/거래량회전율을
        함께 내려주므로 별도 소스(pykrx 등) 없이 거의 실시간 재무 요약이 된다.
        실패 시 {available: False, reason}. 절대 예외를 올리지 않는다.
        """
        meta = _meta(symbol)
        if not settings.kis_app_key or not settings.kis_app_secret:
            return {"available": False, "symbol": symbol, "reason": "KIS 키 미설정"}
        try:
            token = self._ensure_token()
            resp = self._session.get(
                f"{settings.kis_domain}/uapi/domestic-stock/v1/quotations/inquire-price",
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": "FHKST01010100",
                    "custtype": "P",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("rt_cd") != "0":
                raise ValueError(body.get("msg1", "KIS 오류"))
            out = body.get("output", {}) or {}
            marcap_eok = _f(out.get("hts_avls"))  # 억원 단위
            result = {
                "available": True,
                "symbol": symbol,
                "name": meta["name"],
                "per": _f(out.get("per")),
                "pbr": _f(out.get("pbr")),
                "eps": _f(out.get("eps")),
                "bps": _f(out.get("bps")),
                "market_cap": marcap_eok * 1e8 if marcap_eok is not None else None,  # 억원→원
                "shares": _f(out.get("lstn_stcn")),
                "w52_high": _f(out.get("w52_hgpr")),
                "w52_low": _f(out.get("w52_lwpr")),
                "turnover": _f(out.get("vol_tnrt")),
                "settle_month": out.get("stac_month") or None,
                "currency": "KRW",
                "source": "KIS",
                "asof": time.strftime("%Y-%m-%d"),
            }
            # 핵심 지표가 하나도 없으면 미지원 처리.
            if not any(result[k] is not None for k in ("per", "pbr", "eps", "bps", "market_cap")):
                return {"available": False, "symbol": symbol, "reason": "재무 데이터 없음"}
            return result
        except Exception as exc:
            return {"available": False, "symbol": symbol, "reason": f"KIS 재무 조회 실패: {exc}"}

    # ---------------- 수급(외국인/기관 매매동향) ----------------
    def get_investor_flow(self, symbol: str, days: int = 5) -> dict | None:
        """종목별 투자자 매매동향(FHKST01010900) — 외국인/기관 순매수(당일·N일합).
        실패 시 None. 단위: 주식 수(순매수량)."""
        if not settings.kis_app_key or not settings.kis_app_secret:
            return None
        try:
            token = self._ensure_token()
            resp = self._session.get(
                f"{settings.kis_domain}/uapi/domestic-stock/v1/quotations/inquire-investor",
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": "FHKST01010900",
                    "custtype": "P",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("rt_cd") != "0":
                return None
            out = body.get("output") or []
            if not out:
                return None
            recent = out[:days]
            frgn_sum = sum(_f(r.get("frgn_ntby_qty")) or 0 for r in recent)
            orgn_sum = sum(_f(r.get("orgn_ntby_qty")) or 0 for r in recent)
            prsn_sum = sum(_f(r.get("prsn_ntby_qty")) or 0 for r in recent)
            latest = out[0]
            # KIS 수급은 '확정된 거래일' 기준이라 out[0] 이 항상 오늘은 아니다.
            #  - 장전(프리장): out[0] = 전일  → is_today=False (UI 가 '전일 기준' 표기)
            #  - 본장 진행 중: 당일 수치가 부분치/미확정 → confirmed=False
            #  - 장마감 후:    당일 수치 확정    → confirmed=True
            # 그래서 asof(실제 거래일)·is_today·confirmed 를 함께 내려 '장중/장전 값 이상'을 막는다.
            asof = latest.get("stck_bsop_date")
            from datetime import datetime, timedelta, timezone
            from app.core.market import current_session
            today_kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
            sess = current_session()
            is_today = bool(asof) and asof == today_kst
            confirmed = not (is_today and sess.get("session") == "regular")
            return {
                "date": asof,
                "asof": asof,
                "is_today": is_today,
                "confirmed": confirmed,
                "session": sess.get("session"),
                "session_label": sess.get("label"),
                "frgn_ntby_qty": _f(latest.get("frgn_ntby_qty")),
                "orgn_ntby_qty": _f(latest.get("orgn_ntby_qty")),
                "prsn_ntby_qty": _f(latest.get("prsn_ntby_qty")),
                "frgn_ntby_sum": round(frgn_sum),
                "orgn_ntby_sum": round(orgn_sum),
                "prsn_ntby_sum": round(prsn_sum),
                "days": days,
            }
        except Exception:
            return None

    # ---------------- 호가 (매도/매수 10단계 + 잔량) ----------------
    def get_orderbook(self, symbol: str, levels: int = 10, market_code: str = "J") -> dict | None:
        """주식 호가(FHKST01010200) — 매도/매수 N단계 가격·잔량. 실패 시 None.

        market_code: "J"=KRX 정규장(본장), "NX"=넥스트레이드(시간외/프리·애프터).
        호가는 해당 세션에 실시간만 의미. 휴장엔 0/빈 값일 수 있다."""
        if not settings.kis_app_key or not settings.kis_app_secret:
            return None
        try:
            token = self._ensure_token()
            resp = self._session.get(
                f"{settings.kis_domain}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                params={"FID_COND_MRKT_DIV_CODE": market_code, "FID_INPUT_ISCD": symbol},
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": "FHKST01010200",
                    "custtype": "P",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("rt_cd") != "0":
                return None
            o = body.get("output1") or {}
            asks, bids = [], []
            for i in range(1, levels + 1):
                ap, aq = _f(o.get(f"askp{i}")), _f(o.get(f"askp_rsqn{i}"))
                bp, bq = _f(o.get(f"bidp{i}")), _f(o.get(f"bidp_rsqn{i}"))
                if ap:
                    asks.append({"price": ap, "qty": aq or 0})
                if bp:
                    bids.append({"price": bp, "qty": bq or 0})
            if not asks and not bids:
                return None  # 빈 호가(해당 세션 미운영)면 None → 폴백/안내 가능
            return {
                "symbol": symbol,
                "asks": asks,  # 1=최우선 매도호가(가장 낮음) → 위로 갈수록 높음
                "bids": bids,  # 1=최우선 매수호가(가장 높음) → 아래로 갈수록 낮음
                "total_ask_qty": _f(o.get("total_askp_rsqn")),
                "total_bid_qty": _f(o.get("total_bidp_rsqn")),
                "time": o.get("aspr_acpt_hour"),
                "market_code": market_code,
            }
        except Exception:
            return None


def _f(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
