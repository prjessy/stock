"""docs/(GitHub Pages 미리보기)용 시세 스냅샷 생성기.

GitHub Actions 가 주기적으로 실행해 docs/data/*.json 을 갱신·커밋한다.
정적 호스팅(Pages)은 백엔드가 없으므로, 이 스크립트가 라이브 데이터소스를
대신 호출해 스냅샷 JSON 을 떨궈 두는 방식이다.

산출물:
  docs/data/quotes.json              {"generated_at": iso, "quotes": [...]}
  docs/data/history_<key>.json       {"symbol", "period", "history": [...]}
      key: 심볼의 '=' -> 'EQ_', '^' -> 'CARET_' (docs/index.html 의 histFile 과 동일)

개별 종목 조회가 실패해도 전체를 중단하지 않는다(레지스트리가 error 필드로 처리).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.datasources.registry import SourceRegistry

# docs/index.html 의 최대 기간 버튼이 6mo(=전체) 이므로 6개월치면 충분하다.
HISTORY_PERIOD = "6mo"

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def _hist_key(symbol: str) -> str:
    """docs/index.html histFile() 과 동일한 파일명 키 매핑."""
    return symbol.replace("^", "CARET_").replace("=", "EQ_")


def _write_json(path: Path, payload: dict) -> None:
    # 한글을 그대로 저장(기존 스냅샷과 동일하게 ensure_ascii=False).
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    registry = SourceRegistry()

    quotes = registry.all_quotes()
    _write_json(
        DATA_DIR / "quotes.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "quotes": quotes,
        },
    )
    print(f"[snapshot] quotes.json: {len(quotes)} symbols")

    for symbol in registry.watchlist():
        try:
            history = registry.history(symbol, HISTORY_PERIOD)
        except Exception as exc:  # 개별 실패는 건너뛰고 계속
            print(f"[snapshot] history {symbol} 실패: {exc}")
            continue
        _write_json(
            DATA_DIR / f"history_{_hist_key(symbol)}.json",
            {"symbol": symbol, "period": HISTORY_PERIOD, "history": history},
        )
        print(f"[snapshot] history_{_hist_key(symbol)}.json: {len(history)} rows")


if __name__ == "__main__":
    main()
