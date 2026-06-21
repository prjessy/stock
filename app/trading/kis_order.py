"""KIS 국내주식 현금주문 — 자동매매용(실거래 위험 큼, 안전장치 필수).

KisPriceSource 의 토큰·세션을 재사용한다(같은 appkey). 주문 API 는 hashkey 헤더가 필요하다.
- 실전 매수 TTTC0802U / 매도 TTTC0801U, 모의 VTTC0802U / VTTC0801U (settings.kis_paper).
- 안전장치: 계좌(KIS_CANO) 미설정이면 거부, 수량은 settings.trade_max_qty(기본 1) 로 하드캡.
- 절대 예외를 밖으로 던지지 않고 {ok, ...} dict 로 반환한다.

⚠️ 시장가(ORD_DVSN=01)는 장중에만 체결된다. 휴장/장외엔 KIS 가 거부 메시지를 돌려준다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.config import settings

logger = logging.getLogger(__name__)

_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_HASH_PATH = "/uapi/hashkey"
_TIMEOUT = 8.0


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

    def place_order(self, symbol: str, side: str, qty: int, price: float = 0) -> dict:
        """현금주문. side='buy'|'sell'. price=0 이면 시장가(ORD_DVSN=01).

        성공 {ok:True, order_no, msg}, 실패 {ok:False, error}. 절대 raise 안 함.
        """
        if not self.configured():
            return {"ok": False, "error": "주문 계좌 미설정(.env KIS_CANO/KIS_APP_KEY)"}
        if side not in ("buy", "sell"):
            return {"ok": False, "error": f"잘못된 side: {side}"}
        # 안전 하드캡: 설정 최대 수량을 넘지 못한다.
        qty = min(int(qty), int(settings.trade_max_qty))
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
            token = self._ensure_token()
            today = datetime.now()
            start = today - timedelta(days=max(0, int(days)))
            tr = ("VTTC" if settings.kis_paper else "TTTC") + "8001R"
            resp = self._ps._session.get(
                f"{settings.kis_domain}{_CCLD_PATH}",
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
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
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


def _i(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return 0
