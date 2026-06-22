"""자동매매 설정 저장(data/autotrade_config.json).

등락률(전일대비 ±%) 밴드 자동매매 + 손절 + 예약 매도. 서버에 저장해야 브라우저를 안 켜도
서버가 장중 자동 실행한다. 안전: enabled 기본 False(마스터 OFF).
예외는 올리지 않는다(실패 시 기본값/직전값 유지).

rules = { 종목코드: {
    "buy_pct":    등락률 ≤ 이 값(보통 음수, 예 -2)이면 매수. null=미사용  ★실거래 매수★
    "sell_pct":   등락률 ≥ 이 값(보통 양수, 예 +2)이면 매도. null=미사용
    "stop_price": 절대 손절가(원). 현재가 ≤ 이 값이면 매도. null=미사용
    "stop_pct":   평균단가 대비 손절 %(음수, 예 -3)면 매도. null=미사용
    "sell_time":  "HH:MM"(KST) 이 시각 지나면 매도. null=미사용
    "qty":        1회 매수/매도 수량(안전 상한). 기본 1
}}
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "autotrade_config.json"

_SELL_KEYS = ("sell_pct", "stop_price", "stop_pct", "sell_time")


_BALMOK_DEFAULT = {
    "alert": False,      # 발목 감지 시 텔레그램·카톡 알람 (master 무관)
    "auto_buy": False,   # 발목 감지 시 자동 매수 (옵션 · master ON 도 필요)
    "ai_judge": False,   # 자동 매수 전 AI(더듬이2·3) 판단 — 매수 신호일 때만 매수
    "min_score": 2,      # 발목 판정: 신호 N개 이상 겹침
    "qty": 1,            # 자동 매수 수량
}


def _clean_balmok(b) -> dict:
    if not isinstance(b, dict):
        return dict(_BALMOK_DEFAULT)
    ms = _pos(b.get("min_score"))
    q = _pos(b.get("qty"))
    return {
        "alert": bool(b.get("alert", False)),
        "auto_buy": bool(b.get("auto_buy", False)),
        "ai_judge": bool(b.get("ai_judge", False)),
        "min_score": int(ms) if ms and ms >= 1 else 2,
        "qty": int(q) if q and q >= 1 else 1,
    }


def load() -> dict:
    try:
        if _FILE.exists():
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            return {
                "enabled": bool(d.get("enabled", False)),
                "rules": _clean_rules(d.get("rules") or {}),
                "balmok": _clean_balmok(d.get("balmok")),
            }
    except Exception:
        pass
    return {"enabled": False, "rules": {}, "balmok": dict(_BALMOK_DEFAULT)}


def _clean_rules(rules: dict) -> dict:
    out = {}
    if not isinstance(rules, dict):
        return out
    for sym, r in rules.items():
        if not isinstance(r, dict):
            continue
        clean = {
            "buy_pct": _num(r.get("buy_pct")),
            "sell_pct": _num(r.get("sell_pct")),
            "stop_price": _pos(r.get("stop_price")),
            "stop_pct": _num(r.get("stop_pct")),
            "sell_time": _hhmm(r.get("sell_time")),
        }
        q = _pos(r.get("qty"))
        clean["qty"] = int(q) if q and q >= 1 else 1
        clean["on"] = bool(r.get("on", True))   # 종목별 ON/OFF(전체 ON과 별개, 둘 다 켜야 동작)
        # 반복 회수(N): 매수→매도 사이클 최대 N회. 기본 1(1회).
        rp = _pos(r.get("repeat"))
        clean["repeat"] = min(int(rp), 20) if rp and rp >= 1 else 1
        # 반복 그리드(완전자동): 증감율%(매수/매도 간격) + 회수. 1차 진입 기준은 두 옵션:
        #   ① base_price(사용자 지정가) ② buy_pct(전일종가 대비 등락률, 자동·기준가 불필요)
        #   매도 후 2차~는 항상 '직전 매도가' 기준으로 ±step% 반복.
        clean["base_price"] = _pos(r.get("base_price"))
        clean["step_pct"] = _pos(r.get("step_pct"))
        clean["ai_judge"] = bool(r.get("ai_judge", False))  # 1차 매수 전 AI 적정/위험 보조
        # step_pct가 있고(반복), 1차 기준(base_price 또는 buy_pct)이 있으면 그리드 모드
        is_grid = clean["step_pct"] is not None and (clean["base_price"] is not None or clean["buy_pct"] is not None)
        if not is_grid and clean["buy_pct"] is None and all(clean[k] is None for k in _SELL_KEYS):
            continue
        clean["mode"] = "grid" if is_grid else "band"
        out[str(sym)] = clean
    return out


def _num(v):
    try:
        return round(float(v), 4) if v is not None and str(v) != "" else None
    except Exception:
        return None


def _pos(v):
    n = _num(v)
    return n if n is not None and n > 0 else None


def _hhmm(v):
    if not v or not isinstance(v, str):
        return None
    parts = v.strip().split(":")
    try:
        if len(parts) == 2 and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    except Exception:
        pass
    return None


def save(cfg: dict) -> dict:
    """부분 갱신 후 저장. 저장된 전체 설정 반환."""
    cur = load()
    if "enabled" in cfg:
        cur["enabled"] = bool(cfg.get("enabled"))
    if isinstance(cfg.get("rules"), dict):
        cur["rules"] = _clean_rules(cfg["rules"])
    if isinstance(cfg.get("balmok"), dict):
        cur["balmok"] = _clean_balmok(cfg["balmok"])
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return cur
