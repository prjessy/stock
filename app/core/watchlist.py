"""사용자 추가 워치리스트(data/watchlist.json) — .env 기본 종목 위에 동적으로 더하는 국내 종목.

설정창 '종목 관리'에서 추가/삭제. 기본 종목(.env KR_SYMBOLS)은 여기서 안 건드린다(추가분만 관리).
예외는 올리지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings

_FILE = Path(settings.db_path).resolve().parent / "watchlist.json"


def load_extra() -> list[str]:
    """사용자가 추가한 국내 종목코드 목록."""
    try:
        if _FILE.exists():
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            return [str(c).strip() for c in (d.get("kr") or []) if str(c).strip()]
    except Exception:
        pass
    return []


def _save(codes: list[str]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps({"kr": codes}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def add(code: str) -> list[str]:
    code = str(code).strip()
    extra = load_extra()
    if code and code not in extra:
        extra.append(code)
        _save(extra)
    return extra


def remove(code: str) -> list[str]:
    code = str(code).strip()
    extra = [c for c in load_extra() if c != code]
    _save(extra)
    return extra
