"""KIS 국내주식 현금주문 — 자동매매용(실거래 위험 큼, 안전장치 필수).

KisPriceSource 의 토큰·세션을 재사용한다(같은 appkey). 주문 API 는 hashkey 헤더가 필요하다.
- 실전 매수 TTTC0802U / 매도 TTTC0801U, 모의 VTTC0802U / VTTC0801U (settings.kis_paper).
- 안전장치: 계좌(KIS_CANO) 미설정이면 거부, 수량은 settings.trade_max_qty(기본 1) 로 하드캡.
- 절대 예외를 밖으로 던지지 않고 {ok, ...} dict 로 반환한다.

⚠️ 시장가(ORD_DVSN=01)는 장중에만 체결된다. 휴장/장외엔 KIS 가 거부 메시지를 돌려준다.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from app.config import settings

logger = logging.getLogger(__name__)

_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_HASH_PATH = "/uapi/hashkey"
_TIMEOUT = 8.0


def _get_retry(session, url, params, headers, tries: int = 3):
    """GET with retry on 5xx — KIS는 폴러와 동시호출 시 일시적 500(유량초과)을 냄. 마지막 응답 반환."""
    resp = None
    for i in range(tries):
        resp = session.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        if resp.status_code < 500:
            return resp
        time.sleep(0.4 * (i + 1))
    return resp


class OrderClient:
    """KIS 현금주문 클라이언트. price_source = KisPriceSource(토큰·세션 재사용)."""

    def __init__(self, price_source) -> None:
        self._ps = price_source  # _ensure_token(), _session 보유

    def configured(self) -> bool:
        return bool(settings.kis_cano and settings.kis_app_key and settings.kis_app_secret)

    def _hashkey(self, body: dict) -> str:
        resp = self._ps._session.post(
            f"{settings.kis_domain}{_HASH_PATH}",
            json=body,
            headers={"appkey": settings.kis_app_key, "appsecret": settings.kis_app_secret},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["HASH"]

    def place_order(self, symbol: str, side: str, qty: int, price: float = 0,
                    cap: int | None = None) -> dict:
        """현금주문. side='buy'|'sell'. price=0 이면 시장가(ORD_DVSN=01).

        cap: 1회 주문 수량 상한. None이면 settings.trade_max_qty(수동 테스트=1).
             자동 매도는 보유분 이상 못 팔기에 규칙별 max_qty 를 cap 으로 넘긴다.
        성공 {ok:True, order_no, msg}, 실패 {ok:False, error}. 절대 raise 안 함.
        """
        if not self.configured():
            return {"ok": False, "error": "주문 계좌 미설정(.env KIS_CANO/KIS_APP_KEY)"}
        if side not in ("buy", "sell"):
            return {"ok": False, "error": f"잘못된 side: {side}"}
        # 안전 하드캡: 지정한 cap(없으면 설정 최대 수량)을 넘지 못한다.
        cap = int(settings.trade_max_qty) if cap is None else int(cap)
        qty = min(int(qty), cap)
        if qty < 1:
            return {"ok": False, "error": "수량이 0"}
        try:
            token = self._ps._ensure_token()
            tr = ("VTTC" if settings.kis_paper else "TTTC") + ("0802U" if side == "buy" else "0801U")
            body = {
                "CANO": settings.kis_cano,
                "ACNT_PRDT_CD": settings.kis_acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_DVSN": "01" if not price else "00",  # 01=시장가, 00=지정가
                "ORD_QTY": str(qty),
                "ORD_UNPR": str(int(price)) if price else "0",
            }
            hashkey = self._hashkey(body)
            resp = self._ps._session.post(
                f"{settings.kis_domain}{_ORDER_PATH}",
                json=body,
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": tr,
                    "custtype": "P",
                    "hashkey": hashkey,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            j = resp.json()
            if j.get("rt_cd") == "0":
                out = j.get("output") or {}
                return {"ok": True, "order_no": out.get("ODNO"), "time": out.get("ORD_TMD"),
                        "msg": j.get("msg1"), "paper": settings.kis_paper,
                        "side": side, "symbol": symbol, "qty": qty}
            return {"ok": False, "error": j.get("msg1", "주문 실패"), "rt_cd": j.get("rt_cd"),
                    "side": side, "symbol": symbol, "qty": qty}
        except Exception as exc:
            logger.exception("주문 실패")
            return {"ok": False, "error": f"주문 예외: {exc}"}

    def list_orders(self, days: int = 7) -> dict:
        """주식 일별 주문체결 조회(TTTC8001R/VTTC8001R) — 최근 N일 체결/미체결 내역.

        성공 {ok:True, orders:[...]}, 실패 {ok:False, error}. 절대 raise 안 함.
        각 주문: 주문번호·종목·매수매도·주문수량·주문가·체결수량·미체결잔량·상태.
        """
        if not self.configured():
            return {"ok": False, "error": "주문 계좌 미설정(.env KIS_CANO/KIS_APP_KEY)"}
        try:
            token = self._ps._ensure_token()
            today = datetime.now()
            start = today - timedelta(days=max(0, int(days)))
            tr = ("VTTC" if settings.kis_paper else "TTTC") + "8001R"
            resp = _get_retry(
                self._ps._session, f"{settings.kis_domain}{_CCLD_PATH}",
                params={
                    "CANO": settings.kis_cano,
                    "ACNT_PRDT_CD": settings.kis_acnt_prdt_cd,
                    "INQR_STRT_DT": start.strftime("%Y%m%d"),
                    "INQR_END_DT": today.strftime("%Y%m%d"),
                    "SLL_BUY_DVSN_CD": "00",   # 00=전체
                    "INQR_DVSN": "00",          # 00=역순(최신 먼저)
                    "PDNO": "",                  # 전체 종목
                    "CCLD_DVSN": "00",          # 00=전체(체결+미체결)
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_3": "00",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": tr,
                    "custtype": "P",
                },
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"KIS {resp.status_code}(재시도 후): {resp.text[:120]}"}
            j = resp.json()
            if j.get("rt_cd") != "0":
                return {"ok": False, "error": j.get("msg1", "조회 실패")}
            rows = j.get("output1") or []
            orders = []
            for r in rows:
                ord_qty = _i(r.get("ord_qty"))
                ccld_qty = _i(r.get("tot_ccld_qty"))
                rmn = _i(r.get("rmn_qty"))
                if rmn > 0 and ccld_qty > 0:
                    status = "부분체결"
                elif rmn > 0:
                    status = "미체결"
                else:
                    status = "체결"
                orders.append({
                    "order_no": r.get("odno"),
                    "date": r.get("ord_dt"),
                    "time": r.get("ord_tmd"),
                    "symbol": r.get("pdno"),
                    "name": r.get("prdt_name"),
                    "side": r.get("sll_buy_dvsn_cd_name"),  # '매수'/'매도'
                    "ord_qty": ord_qty,
                    "ord_price": _i(r.get("ord_unpr")),
                    "ccld_qty": ccld_qty,
                    "ccld_price": _i(r.get("avg_prvs")),
                    "rmn_qty": rmn,
                    "ord_dvsn": r.get("ord_dvsn_name"),     # 지정가/시장가 등
                    "status": status,
                })
            return {"ok": True, "orders": orders, "paper": settings.kis_paper}
        except Exception as exc:
            logger.exception("주문 내역 조회 실패")
            return {"ok": False, "error": f"조회 예외: {exc}"}

    def get_balance(self) -> dict:
        """주식 잔고조회(TTTC8434R/VTTC8434R) — 보유 종목·수량·평균단가·현재가·손익률.

        성공 {ok:True, holdings:[{symbol,name,qty,avg_price,cur_price,pnl_pct}]}.
        자동 매도(손절) 판정과 화면 표시 양쪽에서 쓴다. 절대 raise 안 함.
        """
        if not self.configured():
            return {"ok": False, "error": "주문 계좌 미설정(.env KIS_CANO/KIS_APP_KEY)"}
        try:
            token = self._ps._ensure_token()
            tr = ("VTTC" if settings.kis_paper else "TTTC") + "8434R"
            resp = _get_retry(
                self._ps._session, f"{settings.kis_domain}{_BALANCE_PATH}",
                params={
                    "CANO": settings.kis_cano,
                    "ACNT_PRDT_CD": settings.kis_acnt_prdt_cd,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",         # 02=종목별
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": settings.kis_app_key,
                    "appsecret": settings.kis_app_secret,
                    "tr_id": tr,
                    "custtype": "P",
                },
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"KIS {resp.status_code}(재시도 후): {resp.text[:120]}"}
            j = resp.json()
            if j.get("rt_cd") != "0":
                return {"ok": False, "error": j.get("msg1", "잔고 조회 실패")}
            holdings = []
            for r in (j.get("output1") or []):
                qty = _i(r.get("hldg_qty"))
                if qty <= 0:
                    continue
                holdings.append({
                    "symbol": r.get("pdno"),
                    "name": r.get("prdt_name"),
                    "qty": qty,
                    "avg_price": _f(r.get("pchs_avg_pric")),
                    "cur_price": _i(r.get("prpr")),
                    "pnl_pct": _f(r.get("evlu_pfls_rt")),
                    "pnl_amt": _i(r.get("evlu_pfls_amt")),   # 평가손익금액(원)
                    "eval_amt": _i(r.get("evlu_amt")),        # 평가금액(원)
                    "buy_amt": _i(r.get("pchs_amt")),         # 매입금액(원)
                })
            # output2[0] = 계좌 합계(수익현황).
            summary = {}
            o2 = j.get("output2") or []
            if o2:
                s = o2[0]
                buy = _i(s.get("pchs_amt_smtl_amt"))
                pnl = _i(s.get("evlu_pfls_smtl_amt"))
                summary = {
                    "deposit": _i(s.get("dnca_tot_amt")),          # 예수금총액
                    "deposit_d2": _i(s.get("prvs_rcdl_excc_amt")),  # D+2 예수금
                    "securities_eval": _i(s.get("scts_evlu_amt")),  # 유가증권 평가금액
                    "total_eval": _i(s.get("tot_evlu_amt")),        # 총평가(유가+예수금)
                    "buy_total": buy,                                # 매입금액 합계
                    "pnl_total": pnl,                                # 평가손익 합계
                    "pnl_total_pct": round(pnl / buy * 100, 2) if buy else None,
                    "net_asset": _i(s.get("nass_amt")),             # 순자산
                }
            return {"ok": True, "holdings": holdings, "summary": summary, "paper": settings.kis_paper}
        except Exception as exc:
            logger.exception("잔고 조회 실패")
            return {"ok": False, "error": f"조회 예외: {exc}"}


def _f(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return 0.0


def _i(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return 0
