"""테이블 스키마 정의 (무거운 ORM 없이 SQL 상수로 관리).

architect_output.md 5절 스키마 기준. db.py 가 이 DDL 들을 실행한다.
"""
from __future__ import annotations

# 가격 알림 발송 이력. (거래일, 종목, 임계값) 으로 당일 1회 중복 방지.
CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    threshold  REAL    NOT NULL,
    fired_at   TEXT    NOT NULL,
    UNIQUE (trade_date, symbol, threshold)
);
"""

# 기준가(전일 종가) 캐시.
CREATE_PREV_CLOSE = """
CREATE TABLE IF NOT EXISTS prev_close (
    symbol     TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close      REAL NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);
"""

# 종목 분석(재무지표 + 차트 보조지표) 캐시.
CREATE_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    symbol            TEXT PRIMARY KEY,
    updated_at        TEXT NOT NULL,
    fundamentals_json TEXT,
    indicators_json   TEXT
);
"""

# 마케팅 자료(뉴스/공시/애널/SNS) 캐시.
CREATE_MARKETING_CACHE = """
CREATE TABLE IF NOT EXISTS marketing_cache (
    symbol      TEXT PRIMARY KEY,
    updated_at  TEXT NOT NULL,
    news_json   TEXT,
    filings_json TEXT,
    analyst_json TEXT,
    sns_json    TEXT
);
"""

# 아침 브리핑 발송 이력.
CREATE_BRIEFINGS = """
CREATE TABLE IF NOT EXISTS briefings (
    date         TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    sent_at      TEXT
);
"""

# 구글 로그인 사용자. google_sub(구글 고유 ID)로 식별, 이메일/이름/사진은 표시용.
CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    google_sub  TEXT    NOT NULL UNIQUE,
    email       TEXT,
    name        TEXT,
    picture     TEXT,
    created_at  TEXT    NOT NULL,
    last_login  TEXT
);
"""

# 로그인 세션. sid(랜덤 토큰)를 httponly 쿠키로 내려 user_id 와 연결한다.
CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    sid         TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# 사용자별 매매일지. 사용자(user_id)마다 자기 기록만 보고 쓴다.
CREATE_JOURNAL = """
CREATE TABLE IF NOT EXISTS journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    trade_date  TEXT    NOT NULL,
    symbol      TEXT,
    name        TEXT,
    side        TEXT,
    price       REAL,
    qty         REAL,
    reason      TEXT,
    memo        TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# init_db() 가 순서대로 실행할 DDL 목록.
ALL_TABLES = [
    CREATE_ALERTS,
    CREATE_PREV_CLOSE,
    CREATE_ANALYSIS_CACHE,
    CREATE_MARKETING_CACHE,
    CREATE_BRIEFINGS,
    CREATE_USERS,
    CREATE_SESSIONS,
    CREATE_JOURNAL,
]
