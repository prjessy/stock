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
# approved: 로그인 승인제 — 0=대기(가입만 됨, 기능 사용 불가) / 1=소유자가 승인.
# 소유자(첫 가입자)는 항상 1 (init_db 마이그레이션 + upsert_user 가 보장).
CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    google_sub  TEXT    NOT NULL UNIQUE,
    email       TEXT,
    name        TEXT,
    picture     TEXT,
    created_at  TEXT    NOT NULL,
    last_login  TEXT,
    approved    INTEGER NOT NULL DEFAULT 0
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
# side: 매수/매도/배당/메모. currency: KRW/USD(+fx_rate 환율). tax: 세금(원).
# amount: 총 거래대금(원, =price*qty*fx_rate). realized_pnl: 매도 시 평단 대비 실현손익(원).
CREATE_JOURNAL = """
CREATE TABLE IF NOT EXISTS journal (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    trade_date   TEXT    NOT NULL,
    symbol       TEXT,
    name         TEXT,
    side         TEXT,
    price        REAL,
    qty          REAL,
    category     TEXT    DEFAULT '일반주',
    currency     TEXT    DEFAULT 'KRW',
    fx_rate      REAL    DEFAULT 1,
    tax          REAL    DEFAULT 0,
    amount       REAL,
    realized_pnl REAL,
    reason       TEXT,
    memo         TEXT,
    photo        TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# 사용자별 관심종목(대시보드 워치리스트). user_id 마다 자기 종목만 보고 추가/삭제한다.
# market: 'KR'(국내 6자리) / 'US'(미국 티커). 첫 로그인 시 .env 기본종목으로 시드한다.
CREATE_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    symbol    TEXT    NOT NULL,
    market    TEXT    NOT NULL DEFAULT 'KR',
    added_at  TEXT    NOT NULL,
    UNIQUE (user_id, symbol),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# 기존 DB(구버전 journal)에 누락 컬럼만 idempotent 하게 추가하기 위한 (컬럼, DDL) 목록.
# SQLite 는 ADD COLUMN IF NOT EXISTS 가 없어 db.py 가 PRAGMA 로 존재 여부를 확인 후 실행한다.
JOURNAL_ADD_COLUMNS = [
    ("category", "ALTER TABLE journal ADD COLUMN category TEXT DEFAULT '일반주'"),
    ("currency", "ALTER TABLE journal ADD COLUMN currency TEXT DEFAULT 'KRW'"),
    ("fx_rate", "ALTER TABLE journal ADD COLUMN fx_rate REAL DEFAULT 1"),
    ("tax", "ALTER TABLE journal ADD COLUMN tax REAL DEFAULT 0"),
    ("amount", "ALTER TABLE journal ADD COLUMN amount REAL"),
    ("realized_pnl", "ALTER TABLE journal ADD COLUMN realized_pnl REAL"),
    ("photo", "ALTER TABLE journal ADD COLUMN photo TEXT"),  # 첨부 사진 파일명(uploads/<uid>/ 아래)
]

# 구버전 users 테이블(approved 없음) 마이그레이션용. 기존 사용자는 전부 0(대기)으로 시작하고,
# 소유자(첫 가입자)만 db.py 가 즉시 1로 올린다 — 이미 들어와 있던 미지의 계정도 재승인 대상.
USERS_ADD_COLUMNS = [
    ("approved", "ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0"),
]

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
    CREATE_WATCHLIST,
]
