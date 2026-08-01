"""손절 전용(autosell) 설정·트리거 단위 테스트."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_clean_rules_requires_trigger(tmp_path, monkeypatch):
    from app.trading import autosell_config as ac

    monkeypatch.setattr(ac, "_FILE", tmp_path / "autosell_config.json")
    saved = ac.save({
        "enabled": True,
        "rules": {
            "005930": {"stop_pct": -3, "max_qty": 2},
            "000660": {"stop_price": 100000, "max_qty": 1},
            "035420": {"sell_time": "14:30"},
            "dummy": {},  # 트리거 없음 → 드롭
            "bad": {"stop_pct": "xx"},  # 무효 → 드롭
        },
    })
    assert saved["enabled"] is True
    assert set(saved["rules"]) == {"005930", "000660", "035420"}
    assert saved["rules"]["005930"]["stop_pct"] == -3.0
    assert saved["rules"]["005930"]["max_qty"] == 2
    assert saved["rules"]["000660"]["stop_price"] == 100000
    assert saved["rules"]["035420"]["sell_time"] == "14:30"


def test_load_default_off(tmp_path, monkeypatch):
    from app.trading import autosell_config as ac

    monkeypatch.setattr(ac, "_FILE", tmp_path / "missing.json")
    d = ac.load()
    assert d == {"enabled": False, "rules": {}}


def test_persist_roundtrip(tmp_path, monkeypatch):
    from app.trading import autosell_config as ac

    path = tmp_path / "autosell_config.json"
    monkeypatch.setattr(ac, "_FILE", path)
    ac.save({"enabled": True, "rules": {"005930": {"stop_pct": -5, "max_qty": 3}}})
    d = ac.load()
    assert d["enabled"] is True
    assert d["rules"]["005930"]["stop_pct"] == -5.0
    assert json.loads(path.read_text(encoding="utf-8"))["enabled"] is True


def test_trigger_reason_stop_price():
    from app.trading.autosell_watch import AutoSellWatcher

    w = AutoSellWatcher(registry=None)
    reason = w._trigger_reason({"stop_price": 70000}, cur=69000, avg=75000, hhmm="10:00")
    assert reason is not None
    assert "손절가" in reason


def test_trigger_reason_stop_pct():
    from app.trading.autosell_watch import AutoSellWatcher

    w = AutoSellWatcher(registry=None)
    # 평단 100000, 현재 96000 → -4%  → stop_pct -3 이면 발동
    reason = w._trigger_reason({"stop_pct": -3}, cur=96000, avg=100000, hhmm="10:00")
    assert reason is not None
    assert "손절" in reason
    # -2% 손실이면 -3 손절 미도달
    assert w._trigger_reason({"stop_pct": -3}, cur=98000, avg=100000, hhmm="10:00") is None


def test_trigger_reason_sell_time():
    from app.trading.autosell_watch import AutoSellWatcher

    w = AutoSellWatcher(registry=None)
    assert w._trigger_reason({"sell_time": "14:30"}, cur=70000, avg=70000, hhmm="14:29") is None
    r = w._trigger_reason({"sell_time": "14:30"}, cur=70000, avg=70000, hhmm="14:30")
    assert r is not None
    assert "예약 매도" in r


def test_done_once_per_day():
    from app.trading.autosell_watch import AutoSellWatcher

    w = AutoSellWatcher(registry=None)
    assert w._already_done("005930") is False
    w._mark_done("005930")
    assert w._already_done("005930") is True
