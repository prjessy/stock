"""분석 피드 — hermes(Claude)가 읽기 좋은 지표 묶음.

서버에서 봉 데이터 + 재무로 기술지표(MA/RSI/볼린저/MACD/스토캐스틱/ATR/거래량),
지지·저항·피보나치, 밸류에이션을 계산해 한 JSON으로 제공한다(더듬이 2·3용).
순수 계산 모듈 — 데이터는 호출측(api.py)이 registry로 받아 넘긴다. 예외는 올리지 않는다.
"""
from __future__ import annotations

import datetime as _dt


def _sma(v, p):
    if len(v) < p:
        return None
    return sum(v[-p:]) / p


def _rsi(v, p=14):
    if len(v) <= p:
        return None
    gains = losses = 0.0
    for i in range(1, p + 1):
        d = v[i] - v[i - 1]
        gains += d if d > 0 else 0
        losses += -d if d < 0 else 0
    ag, al = gains / p, losses / p
    for i in range(p + 1, len(v)):
        d = v[i] - v[i - 1]
        ag = (ag * (p - 1) + (d if d > 0 else 0)) / p
        al = (al * (p - 1) + (-d if d < 0 else 0)) / p
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def _ema_series(v, p):
    out = []
    k = 2 / (p + 1)
    prev = None
    for x in v:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def _macd(v):
    if len(v) < 26:
        return None, None, None
    ef, es = _ema_series(v, 12), _ema_series(v, 26)
    line = [a - b for a, b in zip(ef, es)]
    sig = _ema_series(line, 9)
    return line[-1], sig[-1], line[-1] - sig[-1]


def _stoch(h, l, c, p=14):
    if len(c) < p:
        return None
    hh, ll = max(h[-p:]), min(l[-p:])
    if hh == ll:
        return 50.0
    return round((c[-1] - ll) / (hh - ll) * 100, 1)


def _atr(h, l, c, p=14):
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    if len(trs) < p:
        return None
    return round(sum(trs[-p:]) / p, 2)


def _r2(x):
    return round(x, 2) if x is not None else None


def compute_feed(symbol: str, rows: list[dict], quote: dict | None, fund: dict | None,
                 supply: dict | None = None) -> dict:
    rows = [r for r in (rows or []) if r and r.get("close") is not None]
    if len(rows) < 20:
        return {"symbol": symbol, "error": "데이터 부족(20영업일 이상 필요)", "bars": len(rows)}

    closes = [r["close"] for r in rows]
    highs = [r["high"] if r.get("high") is not None else r["close"] for r in rows]
    lows = [r["low"] if r.get("low") is not None else r["close"] for r in rows]
    vols = [r.get("volume") or 0 for r in rows]
    q = quote or {}
    name = q.get("name", symbol)
    cur = q.get("currency", "KRW")
    price = q.get("price") if q.get("price") is not None else closes[-1]

    ma5, ma20, ma60, ma120 = _sma(closes, 5), _sma(closes, 20), _sma(closes, 60), _sma(closes, 120)
    rsi = _rsi(closes, 14)
    l20 = closes[-20:]
    mid = sum(l20) / len(l20)
    std = (sum((x - mid) ** 2 for x in l20) / len(l20)) ** 0.5
    bbU, bbL = mid + 2 * std, mid - 2 * std
    mline, msig, mhist = _macd(closes)
    golden = (mline > msig) if (mline is not None and msig is not None) else None
    stoch = _stoch(highs, lows, closes, 14)
    atr = _atr(highs, lows, closes, 14)
    last_vol = vols[-1]
    avg_vol = sum(vols[-20:]) / min(20, len(vols))
    vol_ratio = round(last_vol / avg_vol, 2) if avg_vol else None

    w = min(60, len(rows))
    H, L = max(highs[-w:]), min(lows[-w:])
    R = (H - L) or 1
    fib = {f"{int(f*100)}%": _r2(H - f * R) for f in (0.236, 0.382, 0.5, 0.618, 0.786)}

    per = (fund or {}).get("per")
    pbr = (fund or {}).get("pbr")
    w52h = (fund or {}).get("w52_high")
    w52l = (fund or {}).get("w52_low")
    pos52 = round((price - w52l) / (w52h - w52l) * 100, 1) if (w52h and w52l and w52h > w52l) else None

    # 추세/배열
    trend = None
    if ma20 is not None and ma60 is not None:
        trend = "정배열(상승)" if ma20 > ma60 else "역배열(하락)"

    return {
        "symbol": symbol,
        "name": name,
        "currency": cur,
        "asof": _dt.date.today().strftime("%Y-%m-%d"),
        "bars": len(rows),
        "price": _r2(price),
        "change_pct": q.get("change_pct"),
        "prev_close": q.get("prev_close"),
        "trend": trend,
        "indicators": {
            "ma5": _r2(ma5), "ma20": _r2(ma20), "ma60": _r2(ma60), "ma120": _r2(ma120),
            "rsi14": rsi,
            "stochastic_k": stoch,
            "macd": {"line": _r2(mline), "signal": _r2(msig), "hist": _r2(mhist), "golden_cross": golden},
            "bollinger": {"upper": _r2(bbU), "mid": _r2(mid), "lower": _r2(bbL)},
            "atr14": atr,
            "volume_ratio_vs20d": vol_ratio,
        },
        "levels": {
            "recent_high": _r2(H), "recent_low": _r2(L),
            "fib_retracement": fib,
            "w52_high": w52h, "w52_low": w52l, "w52_position_pct": pos52,
        },
        "valuation": {"per": per, "pbr": pbr},
        "supply": _supply_section(supply),
        "hint": "지지=MA·볼린저하단·피보·최근저점, 저항=MA·볼린저상단·피보·최근고점. 매수=지지 합류 구간, 매도=저항 합류 구간. 수급=외인·기관 순매수. 판단 보조용.",
    }


def _supply_section(supply):
    if not supply:
        return None
    fs = supply.get("frgn_ntby_sum") or 0
    os_ = supply.get("orgn_ntby_sum") or 0
    if fs > 0 and os_ > 0:
        note = "외인·기관 동반 순매수(수급 우호)"
    elif fs < 0 and os_ < 0:
        note = "외인·기관 동반 순매도(수급 약세)"
    else:
        note = "수급 혼조"
    return {
        "date": supply.get("date"),
        "asof": supply.get("asof") or supply.get("date"),
        "is_today": supply.get("is_today"),
        "confirmed": supply.get("confirmed"),
        "session_label": supply.get("session_label"),
        "foreign_net_today": supply.get("frgn_ntby_qty"),
        "inst_net_today": supply.get("orgn_ntby_qty"),
        "foreign_net_5d": fs,
        "inst_net_5d": os_,
        "note": note,
    }
