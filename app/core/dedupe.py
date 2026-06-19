"""중복 발송 방지 계층 (storage 위의 얇은 래퍼).

키 = (거래일, 종목, 임계값) → 각 임계값 레벨은 거래일당 1회만 발송(R-4/AC-2).
거래일이 바뀌면 키가 달라지므로 자동으로 재무장된다(R-19/AC-5: set & forget).

storage/db.py 의 Repository(alert_already_sent / record_alert)를 재사용한다.
이 모듈은 호출 의도를 명확히 드러내는 이름만 제공할 뿐, 저장 로직을 새로 만들지 않는다.
"""
from __future__ import annotations

from app.storage.db import Repository


def already_sent(repo: Repository, trade_date: str, symbol: str, threshold: float) -> bool:
    """(거래일, 종목, 임계값) 알림이 이미 발송됐는지 확인."""
    return repo.alert_already_sent(trade_date, symbol, threshold)


def mark_sent(repo: Repository, trade_date: str, symbol: str, threshold: float) -> bool:
    """발송 이력을 기록한다. 새로 기록하면 True, 이미 있으면 False."""
    return repo.record_alert(trade_date, symbol, threshold)
