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

_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["strong_buy", "buy", "hold", "sell", "strong_sell"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string", "description": "한국어 한두 문장 판단 요약"},
        "buy_zone": {"type": "string", "description": "매수 적정 구간(가격), 없으면 '-'"},
        "sell_zone": {"type": "string", "description": "매도 목표 구간(가격), 없으면 '-'"},
        "stop": {"type": "string", "description": "손절 참고가, 없으면 '-'"},
        "key_factors": {"type": "array", "items": {"type": "string"}, "description": "판단 핵심 근거 지표 3~5개"},
    },
    "required": ["signal", "confidence", "summary", "buy_zone", "sell_zone", "stop", "key_factors"],
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
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"symbol": symbol, "error": "ANTHROPIC_API_KEY 미설정"}
    try:
        import anthropic
    except Exception:
        return {"symbol": symbol, "error": "anthropic 패키지 미설치"}

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
        f"[더듬이2] 최근 변동성·추세 흐름상 매수/매도 시점이 임박했는가?\n"
        f"[더듬이3] 이 종목의 최근 추이 특성(정/역배열, 거래량, 모멘텀)을 반영해 판단.\n"
        f"추세·지지/저항·거래량·모멘텀(RSI/스토캐스틱/MACD)·밸류(PER/PBR)·52주 위치를 종합해 "
        f"매수/매도 우호도와 근거를 판단 보조로 제시하세요.{hist_txt}"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=settings.deudeumi_model,
            max_tokens=1024,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data = json.loads(text)
    except Exception as exc:
        return {"symbol": symbol, "error": f"분석 실패: {exc}"}

    data["symbol"] = symbol
    data["name"] = feed.get("name")
    data["price"] = feed.get("price")
    data["asof"] = feed.get("asof")
    _log_signal(symbol, data)
    _write_obsidian(symbol, data)
    return data
