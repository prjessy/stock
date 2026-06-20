"""서버측 알림 설정 저장(data/alert_config.json).

브라우저(localStorage)가 아니라 서버에 저장해야 '페이지를 안 켜도' 서버가 감시·발송할
수 있다. 알림 종류(types)는 멀티 선택: pct(증감)·target(목표금액)·deudeumi1(피보나치)
·deudeumi4(ETF 수급). 예외는 올리지 않는다(실패 시 기본값/직전값 유지).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "alert_config.json"

_VALID_TYPES = ("pct", "target", "deudeumi1", "deudeumi4")
_DEFAULT = {
    "types": ["pct", "target"],          # 활성 알림 종류(멀티)
    "pct_thresholds": [3.0, -3.0],       # 증감 임계값(기본 +3/-3)
    "targets": {},                       # {symbol: {"price": float, "dir": "up"|"down"}}
}


def load() -> dict:
    try:
        if _FILE.exists():
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            return {**_DEFAULT, **d}
    except Exception:
        pass
    return {**_DEFAULT}


def save(cfg: dict) -> dict:
    """부분 갱신(들어온 키만 반영) 후 저장. 저장된 전체 설정을 반환한다."""
    cur = load()
    if isinstance(cfg.get("types"), list):
        cur["types"] = [t for t in cfg["types"] if t in _VALID_TYPES]
    if isinstance(cfg.get("pct_thresholds"), list):
        try:
            cur["pct_thresholds"] = [round(float(x), 4) for x in cfg["pct_thresholds"]][:8]
        except Exception:
            pass
    if isinstance(cfg.get("targets"), dict):
        clean = {}
        for sym, t in cfg["targets"].items():
            try:
                price = float(t["price"])
                d = "up" if t.get("dir") == "up" else "down"
                if price > 0:
                    clean[sym] = {"price": price, "dir": d}
            except Exception:
                continue
        cur["targets"] = clean
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return cur
