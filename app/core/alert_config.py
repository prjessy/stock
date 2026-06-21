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
    "pct_step": 3.0,                     # 증감 단계(%). ±step,±2step,±3step… 배수마다 알림(기본 ±3)
    "pct_count": 3,                      # 증감 알림 최대 횟수(단계 수). 예: 3 → step·2step·3step 까지
    "targets": {},                       # {symbol: {"price": float, "dir": "up"|"down"}}
}


def load() -> dict:
    try:
        if _FILE.exists():
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            # 구버전(pct_thresholds) 마이그레이션 → pct_step(첫 양수).
            if "pct_step" not in d and isinstance(d.get("pct_thresholds"), list):
                pos = next((abs(float(x)) for x in d["pct_thresholds"] if float(x) > 0), None)
                if pos:
                    d["pct_step"] = pos
            return {**_DEFAULT, **{k: v for k, v in d.items() if k in _DEFAULT}}
    except Exception:
        pass
    return {**_DEFAULT}


def save(cfg: dict) -> dict:
    """부분 갱신(들어온 키만 반영) 후 저장. 저장된 전체 설정을 반환한다."""
    cur = load()
    if isinstance(cfg.get("types"), list):
        cur["types"] = [t for t in cfg["types"] if t in _VALID_TYPES]
    if cfg.get("pct_step") is not None:
        try:
            step = round(float(cfg["pct_step"]), 4)
            if step > 0:
                cur["pct_step"] = step
        except Exception:
            pass
    if cfg.get("pct_count") is not None:
        try:
            cur["pct_count"] = max(1, min(20, int(cfg["pct_count"])))
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
