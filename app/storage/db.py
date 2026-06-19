"""SQLite 저장소 추상화 (Repository 패턴).

상위 로직은 Repository 메서드만 호출한다. 추후 MySQL/Postgres 로 교체할 때
이 파일만 바꾸면 되도록 SQLite 세부를 여기에 가둔다.

현재 슬라이스(골격)에서는 init_db() + 알림 중복방지 stub 만 제공한다.
나머지 CRUD(분석/마케팅/브리핑)는 후속 슬라이스에서 추가한다.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from app.config import settings
from app.storage import models


def _connect(db_path: str) -> sqlite3.Connection:
    """DB 파일을 열고 커넥션을 돌려준다. 폴더가 없으면 만든다."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class Repository:
    """SQLite 기반 저장소. 커넥션 1개를 들고 다닌다(단일 프로세스 전제)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self.conn = _connect(self.db_path)

    def init_db(self) -> None:
        """모든 테이블을 idempotent 하게 생성한다."""
        for ddl in models.ALL_TABLES:
            self.conn.execute(ddl)
        self.conn.commit()

    # --- 알림 중복방지 (R-4 / AC-2, AC-4) -------------------------------
    # 골격 단계 stub. 임계값 판정 로직은 builder-2(core)가 담당하고,
    # 여기서는 "이미 보냈는지 확인 + 신규면 기록" 의 저장소 책임만 가진다.

    def alert_already_sent(self, trade_date: str, symbol: str, threshold: float) -> bool:
        """(거래일, 종목, 임계값) 조합이 이미 발송됐는지 확인."""
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE trade_date = ? AND symbol = ? AND threshold = ?",
            (trade_date, symbol, threshold),
        ).fetchone()
        return row is not None

    def record_alert(self, trade_date: str, symbol: str, threshold: float) -> bool:
        """발송 이력을 기록한다. 이미 있으면 False(중복), 새로 넣으면 True.

        UNIQUE 제약으로 경쟁 조건에서도 중복 발송을 막는다.
        """
        try:
            self.conn.execute(
                "INSERT INTO alerts (trade_date, symbol, threshold, fired_at) VALUES (?, ?, ?, ?)",
                (trade_date, symbol, threshold, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        """최근 감지된 알림을 최신순으로 돌려준다(알람 탭 표시용).

        alerts 테이블 컬럼(id, trade_date, symbol, threshold, fired_at)을
        dict 리스트로 반환한다. 조회 실패 시 빈 리스트(전체 500 금지).
        """
        try:
            rows = self.conn.execute(
                "SELECT trade_date, symbol, threshold, fired_at "
                "FROM alerts ORDER BY fired_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def close(self) -> None:
        self.conn.close()


def init_db(db_path: str | None = None) -> Repository:
    """편의 함수: Repository 를 만들고 테이블까지 생성해 돌려준다."""
    repo = Repository(db_path)
    repo.init_db()
    return repo
