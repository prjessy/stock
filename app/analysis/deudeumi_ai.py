"""더듬이 2·3 — AI(Claude) 기반 매수/매도 시점 판단 (판단 보조).

앱이 /api/feed 지표 묶음을 Claude(Anthropic API)에 넘겨 변동장 시점(더듬이2)·종목 추이(더듬이3)를
판단한다. 모든 신호는 data/deudeumi_signals.jsonl 에 기록(진화 씨앗) — 과거 신호를 다음 판단의
맥락으로 다시 넣는다. 예측이 아니라 근거 기반 '판단 보조'. 예외는 올리지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from app.config import settings

_LOG = Path(settings.db_path).resolve().parent / "deudeumi_signals.jsonl"

_SIG = {"type": "string", "enum": ["strong_buy", "buy", "hold", "sell", "strong_sell"]}
_SUB = {
    "type": "object",
    "properties": {"signal": _SIG, "summary": {"type": "string", "description": "한국어 한 문장"}},
    "required": ["signal", "summary"],
    "additionalProperties": False,
}
_SCHEMA = {
    "type": "object",
    "properties": {
        "deudeumi2": _SUB,  # 변동장 시점(타이밍)
        "deudeumi3": _SUB,  # 종목 추이 특성
        "signal": _SIG,
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string", "description": "종합 한두 문장 판단 요약"},
        "buy_zone": {"type": "string", "description": "매수 적정 구간(가격), 없으면 '-'"},
        "sell_zone": {"type": "string", "description": "매도 목표 구간(가격), 없으면 '-'"},
        "stop": {"type": "string", "description": "손절 참고가, 없으면 '-'"},
        "key_factors": {"type": "array", "items": {"type": "string"}, "description": "판단 핵심 근거 3~5개"},
    },
    "required": ["deudeumi2", "deudeumi3", "signal", "confidence", "summary", "buy_zone", "sell_zone", "stop", "key_factors"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한국 주식 매매 '판단 보조' 분석가다. 미래를 단정·예측하지 않고, 주어진 지표 근거로만 "
    "현재 시점의 매수/매도 우호도를 판단한다. 과신 금지. 반드시 스키마 JSON으로만 답한다."
)


def recent_signals(symbol: str, limit: int = 5) -> list[dict]:
    """해당 종목의 최근 신호 기록(진화 맥락용)."""
    if not _LOG.exists():
        return []
    out = []
    try:
        for line in _LOG.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("symbol") == symbol:
                    out.append(rec)
            except Exception:
                continue
    except Exception:
        return []
    return out[-limit:]


def _log_signal(symbol: str, data: dict) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), "symbol": symbol,
               "signal": data.get("signal"), "confidence": data.get("confidence"),
               "price": data.get("price"), "summary": data.get("summary")}
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def evaluate_signals(symbol: str, registry, window: int = 5, thresh: float = 2.0) -> dict:
    """과거 신호의 결과(맞았나/틀렸나) 추적 → 정확도. (진화: 다음 판단의 캘리브레이션 근거)
    buy=window일 뒤 +thresh%↑ 이면 hit, sell=-thresh%↓ 이면 hit, hold=|변동|<thresh 면 hit."""
    sigs = recent_signals(symbol, limit=50)
    try:
        rows = [r for r in (registry.history(symbol, "1y") or []) if r and r.get("close") is not None]
    except Exception:
        rows = []
    dates = [r["date"] for r in rows]
    closes = [r["close"] for r in rows]
    idx = {d: i for i, d in enumerate(dates)}
    evaluated = hits = 0
    out = []
    for s in sigs:
        ts = (s.get("ts") or "")[:10]
        sig = s.get("signal")
        p0 = s.get("price")
        outcome = "pending"
        if ts in idx and p0:
            j = idx[ts] + window
            if j < len(closes):
                ret = (closes[j] - p0) / p0 * 100
                if sig in ("buy", "strong_buy"):
                    hit = ret >= thresh
                elif sig in ("sell", "strong_sell"):
                    hit = ret <= -thresh
                else:
                    hit = abs(ret) < thresh
                outcome = "hit" if hit else "miss"
                evaluated += 1
                hits += 1 if hit else 0
        out.append({**s, "outcome": outcome})
    acc = round(hits / evaluated * 100, 1) if evaluated else None
    return {"symbol": symbol, "evaluated": evaluated, "hits": hits,
            "accuracy_pct": acc, "window_days": window, "recent": out[-10:]}


def _write_obsidian(symbol: str, data: dict) -> None:
    """신호를 옵시디언 볼트 노트에 추가 + git 자동 커밋(로컬, best-effort)."""
    vault = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not vault:
        return
    try:
        import subprocess
        from pathlib import Path
        d = Path(vault) / "더듬이신호"
        d.mkdir(parents=True, exist_ok=True)
        note = d / f"{symbol}.md"
        header = f"# {data.get('name', symbol)} ({symbol}) 더듬이 신호\n\n" if not note.exists() else ""
        kf = ", ".join(data.get("key_factors") or [])
        entry = (
            f"## {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')} — {data.get('signal')} ({data.get('confidence')})\n"
            f"- 현재가: {data.get('price')}\n- 요약: {data.get('summary')}\n"
            f"- 매수: {data.get('buy_zone')} / 매도: {data.get('sell_zone')} / 손절: {data.get('stop')}\n"
            f"- 근거: {kf}\n\n"
        )
        with note.open("a", encoding="utf-8") as f:
            f.write(header + entry)
        env = {**os.environ, "GIT_AUTHOR_NAME": "hermes", "GIT_AUTHOR_EMAIL": "hermes@local",
               "GIT_COMMITTER_NAME": "hermes", "GIT_COMMITTER_EMAIL": "hermes@local"}
        subprocess.run(["git", "-C", vault, "add", "."], capture_output=True, timeout=10)
        subprocess.run(["git", "-C", vault, "commit", "-m", f"signal {symbol} {data.get('signal')}"],
                       capture_output=True, timeout=10, env=env)
    except Exception:
        pass


def analyze(symbol: str, feed: dict, recent: list[dict] | None = None, accuracy: dict | None = None) -> dict:
    if feed.get("error"):
        return {"symbol": symbol, "error": feed["error"]}
    from app import llm
    if not llm.configured():
        return {"symbol": symbol, "error": "LLM 미설정(.env LLM_ENDPOINT/LLM_MODEL)"}

    hist_txt = ""
    if recent:
        lines = [f"- {r.get('ts','')}: {r.get('signal')}({r.get('confidence')}) @ {r.get('price')}" for r in recent]
        hist_txt = "\n\n과거 내 신호 기록(참고, 일관성·과매매 점검용):\n" + "\n".join(lines)
    if accuracy and accuracy.get("accuracy_pct") is not None:
        hist_txt += (f"\n과거 이 종목 신호 적중률: {accuracy['accuracy_pct']}% "
                     f"({accuracy['evaluated']}건, {accuracy['window_days']}일 기준) — 과신 말고 보정에 참고.")

    prompt = (
        f"다음은 {feed.get('name')}({symbol}) 의 기술/밸류 지표 묶음(JSON)입니다.\n"
        f"```json\n{json.dumps(feed, ensure_ascii=False)}\n```\n"
        f"각각 따로 판단해 채우세요:\n"
        f"- deudeumi2 (변동장 시점): 최근 변동성·모멘텀(RSI/스토캐스틱/MACD)·볼린저로 '지금 진입/이탈 타이밍'인지.\n"
        f"- deudeumi3 (종목 추이): 이 종목의 추세 특성(정/역배열·거래량·수급·밸류)으로 본 중기 방향.\n"
        f"- 그리고 둘을 종합한 overall(signal/confidence/summary)과 매수존·매도존·손절·핵심근거.\n"
        f"예측 아닌 판단 보조로.{hist_txt}"
    )

    try:
        data = llm.chat_json(_SYSTEM, prompt, _SCHEMA, max_tokens=1024, source="deudeumi")
    except Exception as exc:
        return {"symbol": symbol, "error": f"분석 실패: {exc}"}

    data["symbol"] = symbol
    data["name"] = feed.get("name")
    data["price"] = feed.get("price")
    data["asof"] = feed.get("asof")
    # 중복 제거: 직전 신호와 같으면 기록·노트 생략(의미 있는 '변화'만 진화 로그에 남김)
    prev = recent_signals(symbol, 1)
    if not (prev and prev[-1].get("signal") == data.get("signal")):
        _log_signal(symbol, data)
        _write_obsidian(symbol, data)
    return data
