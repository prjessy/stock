"""자동 매도(손절 + 스케줄 매도) 설정 저장(data/autosell_config.json).

서버에 저장해야 브라우저를 안 켜도 서버가 장중 감시·자동 매도한다.
안전: enabled 기본 False(마스터 스위치 OFF). 매도 전용이라 보유분 이상은 못 판다.
예외는 올리지 않는다(실패 시 기본값/직전값 유지).

rules = { 종목코드: {
    "stop_pct":   평균단가 대비 손절 % (음수, 예 -3.0). null=미사용
    "stop_price": 절대 손절가(원). 현재가 ≤ 이 값이면 매도. null=미사용
    "sell_time":  "HH:MM"(KST) 이 시각 지나면 전량 매도. null=미사용
    "max_qty":    1회 매도 최대 수량(안전 상한). 기본 1
}}
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "autosell_config.json"

_DEFAULT = {
    "enabled": False,   # 마스터 스위치(기본 OFF — 안전)
    "rules": {},
}


def load() -> dict:
    try:
        if _FILE.exists():
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            return {
                "enabled": bool(d.get("enabled", False)),
                "rules": _clean_rules(d.get("rules") or {}),
            }
    except Exception:
        pass
    return {"enabled": False, "rules": {}}


def _clean_rules(rules: dict) -> dict:
    out = {}
    if not isinstance(rules, dict):
        return out
    for sym, r in rules.items():
        if not isinstance(r, dict):
            continue
        clean = {}
        clean["stop_pct"] = _num(r.get("stop_pct"))
        clean["stop_price"] = _pos(r.get("stop_price"))
        clean["sell_time"] = _hhmm(r.get("sell_time"))
        mq = _pos(r.get("max_qty"))
        clean["max_qty"] = int(mq) if mq and mq >= 1 else 1
        # 모든 조건이 비어있는 규칙은 저장 안 함
        if clean["stop_pct"] is None and clean["stop_price"] is None and clean["sell_time"] is None:
            continue
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
    s = v.strip()
    parts = s.split(":")
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
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return cur
