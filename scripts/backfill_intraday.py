"""오늘치 15분 인트라데이 포인트를 yfinance 15m 봉으로 일괄 백필한다(1회성).

평소엔 GitHub Actions 가 15분마다 1개씩 누적하지만, 처음 켰을 때는 표가 비어
보인다. 이 스크립트로 '오늘(KST)' 15분 격자를 실제 데이터로 한 번에 채운다.

- KR 종목은 yfinance 의 '<코드>.KS' 티커로 조회, 미국은 심볼 그대로.
- prev_close 는 docs/data/quotes.json 값을 사용해 등락%를 계산(대시보드와 일치).
- yfinance 에 없는 티커(일부 레버리지 ETF)는 건너뛴다(기존 씨앗 유지).

산출 형식은 build_docs_snapshot.py 와 동일:
  docs/data/intraday_<key>.json  {"symbol","date","points":[{"t","price","change_pct"}]}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

from app.config import settings

KST = timezone(timedelta(hours=9))
DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def _hist_key(symbol: str) -> str:
    return symbol.replace("^", "CARET_").replace("=", "EQ_")


def _yf_ticker(symbol: str) -> str:
    # 국내 6자리 코드는 야후 '.KS' 접미사로 조회.
    return f"{symbol}.KS" if symbol in set(settings.kr_symbols) else symbol


def _prev_closes() -> dict[str, float]:
    try:
        data = json.loads((DATA_DIR / "quotes.json").read_text(encoding="utf-8"))
        return {q["symbol"]: q.get("prev_close") for q in data.get("quotes", [])}
    except Exception:
        return {}


def main() -> None:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = _prev_closes()
    symbols = list(settings.kr_symbols) + list(settings.us_symbols)

    for symbol in symbols:
        ticker = _yf_ticker(symbol)
        try:
            df = yf.Ticker(ticker).history(period="2d", interval="15m")
        except Exception as exc:
            print(f"[backfill] {symbol}({ticker}) 조회 실패: {exc}")
            continue
        if df is None or df.empty:
            print(f"[backfill] {symbol}({ticker}) 데이터 없음 — 건너뜀")
            continue

        pc = prev.get(symbol)
        points = []
        for idx, row in df.iterrows():
            ts_kst = idx.tz_convert(KST)
            if ts_kst.strftime("%Y-%m-%d") != today:
                continue  # 오늘(KST) 봉만
            close = float(row["Close"])
            change_pct = round((close / pc - 1) * 100, 2) if pc else None
            points.append({"t": ts_kst.strftime("%H:%M"), "price": round(close, 2), "change_pct": change_pct})

        if not points:
            print(f"[backfill] {symbol} 오늘 봉 없음 — 건너뜀")
            continue

        path = DATA_DIR / f"intraday_{_hist_key(symbol)}.json"
        path.write_text(
            json.dumps({"symbol": symbol, "date": today, "points": points},
                       ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(f"[backfill] {symbol}: {len(points)} points ({points[0]['t']}~{points[-1]['t']})")


if __name__ == "__main__":
    main()
