# 주식 알림/분석 보조 시스템

국내 2종목 + 미국 3지표의 가격 알림, 종목 분석, 아침 브리핑을 제공하는
**알림 전용**(자동매매 없음) 보조 시스템. 전송/명령은 Hermes 게이트웨이 경유 Telegram.

> 현재 단계: **골격(skeleton)**. 설정 로딩 + SQLite 초기화 + Hermes Notifier 인터페이스까지 구현.
> 알림 엔진/데이터소스/대시보드/브리핑/명령 라우터는 후속 슬라이스에서 추가됩니다.

## 요구사항
- Python 3.10+ (개발/검증: 3.14)

## 설치

```powershell
# 1) 가상환경 생성 + 활성화 (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# (Git Bash / Linux / macOS)
# python -m venv .venv && source .venv/bin/activate

# 2) 의존성 설치
pip install -r requirements.txt
```

## 설정

```powershell
# .env 템플릿 복사 후 값 편집
Copy-Item .env.example .env
```

`.env` 주요 키: `KR_SYMBOLS`, `US_SYMBOLS`, `THRESHOLDS`, `POLL_INTERVAL_SECONDS`,
`MARKET_OPEN`/`MARKET_CLOSE`, `BRIEFING_TIME`, `ALLOWED_CHAT_IDS`, `HERMES_BASE_URL`, `DB_PATH`.
모든 키는 기본값이 있으므로 `.env` 없이도 골격은 실행됩니다. (`.env`/DB 는 git 제외)

## 실행

```powershell
python -m app.main
```

설정 로딩 + DB 초기화가 끝나면 시작 배너가 출력됩니다.
`data/stock.db` 가 생성되고 테이블이 idempotent 하게 만들어집니다.

## 구조

```
app/
  main.py              # 엔트리 (골격: config 로딩 + DB 초기화 + 배너)
  config.py            # .env 설정 로딩
  storage/
    db.py              # SQLite Repository (init_db, 알림 중복방지)
    models.py          # 테이블 DDL 상수
  notify/
    base.py            # Notifier 인터페이스
    hermes.py          # Hermes localhost HTTP 구현체
  core/ datasources/ command/ web/   # 후속 슬라이스용 빈 패키지
data/                  # SQLite 파일 (git 제외)
```
