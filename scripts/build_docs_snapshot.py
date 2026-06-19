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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.datasources.registry import SourceRegistry

# 과거 차트는 '한 달 일봉(종가)' 기준만 제공한다.
HISTORY_PERIOD = "1mo"

# 오늘치 15분 인트라데이 포인트 보관 한도(15분 × 60 = 15시간치면 충분).
INTRADAY_MAX_POINTS = 60

# 무료 소스 KST(한국시간) 기준으로 '오늘'을 판단한다.
KST = timezone(timedelta(hours=9))

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


def _append_intraday(quote: dict, now_kst: datetime) -> None:
    """현재 시세를 종목별 오늘치 15분 포인트 파일에 누적한다.

    - 날짜(KST)가 바뀌면 포인트를 초기화한다(당일치만 보관).
    - 같은 분(hh:mm)에 두 번 실행되면 마지막 포인트를 덮어쓴다(중복 방지).
    - 가격이 없는(에러) 시세는 건너뛴다.
    """
    price = quote.get("price")
    if price is None:
        return
    symbol = quote.get("symbol", "")
    path = DATA_DIR / f"intraday_{_hist_key(symbol)}.json"
    today = now_kst.strftime("%Y-%m-%d")
    # 시각을 15분 격자(:00/:15/:30/:45)로 내림 → 시간당 정확히 4개.
    slot = (now_kst.minute // 15) * 15
    hm = f"{now_kst.hour:02d}:{slot:02d}"

    data = {"symbol": symbol, "date": today, "points": []}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if prev.get("date") == today and isinstance(prev.get("points"), list):
                data = prev
        except Exception:
            pass  # 손상 시 새로 시작

    point = {"t": hm, "price": price, "change_pct": quote.get("change_pct")}
    pts = data["points"]
    if pts and pts[-1].get("t") == hm:
        pts[-1] = point          # 같은 분 재실행 → 덮어쓰기
    else:
        pts.append(point)
    data["points"] = pts[-INTRADAY_MAX_POINTS:]
    _write_json(path, data)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    registry = SourceRegistry()

    now_kst = datetime.now(KST)
    quotes = registry.all_quotes()
    _write_json(
        DATA_DIR / "quotes.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "quotes": quotes,
        },
    )
    print(f"[snapshot] quotes.json: {len(quotes)} symbols")

    # 종목별 오늘치 15분 인트라데이 포인트 누적
    for q in quotes:
        _append_intraday(q, now_kst)
    print(f"[snapshot] intraday @ {now_kst.strftime('%Y-%m-%d %H:%M')} KST")

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
