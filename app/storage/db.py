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
        """모든 테이블을 idempotent 하게 생성하고, 구버전 스키마를 마이그레이션한다."""
        for ddl in models.ALL_TABLES:
            self.conn.execute(ddl)
        self._migrate_journal()
        self.conn.commit()

    def _migrate_journal(self) -> None:
        """구버전 journal 테이블에 누락된 컬럼만 추가(데이터 보존). 실패해도 앱은 뜬다."""
        try:
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(journal)").fetchall()}
            for col, ddl in models.JOURNAL_ADD_COLUMNS:
                if col not in cols:
                    self.conn.execute(ddl)
        except Exception:
            pass

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

    # --- 구글 로그인 사용자 / 세션 -------------------------------------

    def upsert_user(self, google_sub: str, email: str, name: str, picture: str) -> int:
        """구글 사용자 신규 생성 또는 기존 갱신 후 user_id 반환."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO users (google_sub, email, name, picture, created_at, last_login) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(google_sub) DO UPDATE SET "
            "email=excluded.email, name=excluded.name, picture=excluded.picture, last_login=excluded.last_login",
            (google_sub, email, name, picture, now, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()
        uid = int(row["id"])
        # 신규 사용자면 .env 기본종목으로 관심종목을 1회 시드(빈 대시보드 방지).
        self.seed_watchlist_if_empty(uid, list(settings.kr_symbols), list(settings.us_symbols))
        return uid

    def create_session(self, sid: str, user_id: int, expires_at: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions (sid, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (sid, user_id, now, expires_at),
        )
        self.conn.commit()

    def user_by_session(self, sid: str) -> dict | None:
        """세션 쿠키(sid)로 사용자를 찾는다. 만료/없음이면 None."""
        if not sid:
            return None
        try:
            row = self.conn.execute(
                "SELECT u.id, u.email, u.name, u.picture, s.expires_at "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.sid = ?",
                (sid,),
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat():
            self.delete_session(sid)
            return None
        return {"id": row["id"], "email": row["email"], "name": row["name"], "picture": row["picture"]}

    def delete_session(self, sid: str) -> None:
        try:
            self.conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
            self.conn.commit()
        except Exception:
            pass

    # --- 사용자별 관심종목(워치리스트) ---------------------------------

    def list_watchlist(self, user_id: int) -> list[dict]:
        """해당 사용자의 관심종목 [{symbol, market}] — 국내 먼저, 그다음 미국. 실패 시 []."""
        try:
            rows = self.conn.execute(
                "SELECT symbol, market FROM watchlist WHERE user_id = ? "
                "ORDER BY (market = 'US'), id",
                (user_id,),
            ).fetchall()
            return [{"symbol": r["symbol"], "market": r["market"]} for r in rows]
        except Exception:
            return []

    def add_watchlist(self, user_id: int, symbol: str, market: str) -> bool:
        """관심종목 추가. 이미 있으면 무시(False), 새로 넣으면 True."""
        try:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, symbol, market, added_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, symbol, market, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
            return cur.rowcount > 0
        except Exception:
            return False

    def remove_watchlist(self, user_id: int, symbol: str) -> None:
        try:
            self.conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol)
            )
            self.conn.commit()
        except Exception:
            pass

    def seed_watchlist_if_empty(self, user_id: int, kr_symbols: list[str],
                                us_symbols: list[str]) -> None:
        """관심종목이 하나도 없는 사용자에게 .env 기본종목을 1회 시드한다(신규 로그인용)."""
        try:
            n = self.conn.execute(
                "SELECT COUNT(*) AS c FROM watchlist WHERE user_id = ?", (user_id,)
            ).fetchone()["c"]
            if n:
                return
            now = datetime.now(timezone.utc).isoformat()
            rows = [(user_id, s, "KR", now) for s in kr_symbols] + \
                   [(user_id, s, "US", now) for s in us_symbols]
            self.conn.executemany(
                "INSERT OR IGNORE INTO watchlist (user_id, symbol, market, added_at) "
                "VALUES (?, ?, ?, ?)", rows,
            )
            self.conn.commit()
        except Exception:
            pass

    def all_watchlist_symbols(self) -> list[tuple[str, str]]:
        """모든 사용자 관심종목의 합집합 [(symbol, market)] — 폴러가 시세를 미리 받아두는 대상."""
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT symbol, market FROM watchlist"
            ).fetchall()
            return [(r["symbol"], r["market"]) for r in rows]
        except Exception:
            return []

    # --- 사용자별 매매일지 ----------------------------------------------

    def list_journal(self, user_id: int, limit: int = 500) -> list[dict]:
        """해당 사용자의 매매일지(최신순). 실패 시 빈 리스트(500 금지)."""
        try:
            rows = self.conn.execute(
                "SELECT id, trade_date, symbol, name, side, price, qty, category, "
                "currency, fx_rate, tax, amount, realized_pnl, reason, memo, created_at, updated_at "
                "FROM journal WHERE user_id = ? ORDER BY trade_date DESC, id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _avg_buy_cost(self, user_id: int, symbol: str | None, name: str | None,
                      currency: str, exclude_id: int | None = None) -> float | None:
        """같은 종목(코드 우선, 없으면 이름)·같은 통화 '매수'의 가중평균 단가(거래통화 기준).

        실현손익을 단순 가중평균법으로 계산하기 위한 보조값. 부분매도/시점 정밀추적은
        하지 않는다(개인 일지 수준의 근사). 매수 기록이 없으면 None.
        """
        key_col, key_val = ("symbol", symbol) if symbol else ("name", name)
        if not key_val:
            return None
        sql = (f"SELECT price, qty FROM journal WHERE user_id=? AND side='매수' "
               f"AND {key_col}=? AND IFNULL(currency,'KRW')=? AND price IS NOT NULL AND qty IS NOT NULL")
        params: list = [user_id, key_val, currency]
        if exclude_id is not None:
            sql += " AND id<>?"
            params.append(exclude_id)
        rows = self.conn.execute(sql, params).fetchall()
        tot_qty = sum((r["qty"] or 0) for r in rows)
        tot_cost = sum((r["price"] or 0) * (r["qty"] or 0) for r in rows)
        return (tot_cost / tot_qty) if tot_qty > 0 else None

    def _enrich(self, user_id: int, e: dict, exclude_id: int | None = None) -> None:
        """e 에 amount(총거래대금 KRW)·realized_pnl(매도 실현손익 KRW)을 채운다."""
        price, qty = e.get("price"), e.get("qty")
        fx = e.get("fx_rate") or 1
        tax = e.get("tax") or 0
        e["amount"] = (price * qty * fx) if (price is not None and qty is not None) else None
        realized = None
        if e.get("side") == "매도" and price is not None and qty is not None:
            avg = self._avg_buy_cost(user_id, e.get("symbol"), e.get("name"),
                                     e.get("currency") or "KRW", exclude_id)
            if avg is not None:
                realized = (price - avg) * qty * fx - tax
        e["realized_pnl"] = realized

    def add_journal(self, user_id: int, e: dict) -> int:
        now = datetime.now(timezone.utc).isoformat()
        self._enrich(user_id, e)
        cur = self.conn.execute(
            "INSERT INTO journal (user_id, trade_date, symbol, name, side, price, qty, category, "
            "currency, fx_rate, tax, amount, realized_pnl, reason, memo, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, e.get("trade_date"), e.get("symbol"), e.get("name"), e.get("side"),
             e.get("price"), e.get("qty"), e.get("category"), e.get("currency"), e.get("fx_rate"), e.get("tax"),
             e.get("amount"), e.get("realized_pnl"), e.get("reason"), e.get("memo"), now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_journal(self, user_id: int, entry_id: int, e: dict) -> bool:
        """본인 소유 일지만 수정(user_id 일치 강제). 변경 행 1 이상이면 True."""
        now = datetime.now(timezone.utc).isoformat()
        self._enrich(user_id, e, exclude_id=entry_id)
        cur = self.conn.execute(
            "UPDATE journal SET trade_date=?, symbol=?, name=?, side=?, price=?, qty=?, category=?, "
            "currency=?, fx_rate=?, tax=?, amount=?, realized_pnl=?, reason=?, memo=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (e.get("trade_date"), e.get("symbol"), e.get("name"), e.get("side"),
             e.get("price"), e.get("qty"), e.get("category"), e.get("currency"), e.get("fx_rate"), e.get("tax"),
             e.get("amount"), e.get("realized_pnl"), e.get("reason"), e.get("memo"), now, entry_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_journal(self, user_id: int, entry_id: int) -> bool:
        """본인 소유 일지만 삭제. 삭제됐으면 True."""
        cur = self.conn.execute(
            "DELETE FROM journal WHERE id=? AND user_id=?", (entry_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()


def init_db(db_path: str | None = None) -> Repository:
    """편의 함수: Repository 를 만들고 테이블까지 생성해 돌려준다."""
    repo = Repository(db_path)
    repo.init_db()
    return repo
