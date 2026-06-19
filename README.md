# 주식 알림/분석 보조 시스템

국내 2종목 + 미국 3지표의 가격 알림, 종목 분석, 아침 브리핑을 제공하는
**알림 전용**(자동매매 없음) 보조 시스템. 전송/명령은 Hermes 게이트웨이 경유 Telegram.

> 현재 단계: **골격 + 대시보드(1차)**. 설정 로딩 + SQLite 초기화 + Hermes Notifier 인터페이스에 더해
> **시세/차트 대시보드**(국내 2종목 + 미국 3지표)를 브라우저에서 확인할 수 있습니다.
> 알림 엔진/브리핑/명령 라우터/재무·기술적 지표는 후속 슬라이스에서 추가됩니다.

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

### 골격(스케줄러/알림 엔트리)

```powershell
python -m app.main
```

설정 로딩 + DB 초기화가 끝나면 시작 배너가 출력됩니다.
`data/stock.db` 가 생성되고 테이블이 idempotent 하게 만들어집니다.

### 대시보드 (시세 + 차트)

```powershell
# 방법 1: 모듈 실행 (host 0.0.0.0:8000)
python -m app.web

# 방법 2: uvicorn 직접 실행 (개발 시 --reload 추가 가능)
python -m uvicorn app.web.api:app --host 0.0.0.0 --port 8000
```

접속 URL:
- 로컬: <http://localhost:8000>
- VPS: `http://<서버IP>:8000` (host 0.0.0.0 로 바인딩)

대시보드 기능:
- 워치리스트 카드(현재가 / 등락률 / 통화·지수·선물 표시), 30초 자동 갱신
- 카드 클릭 시 해당 종목 가격 라인 차트(1개월/3개월/6개월/1년 전환)
- 시세 출처(FinanceDataReader/yfinance) 실패 시 "데이터 없음" placeholder 로 표시되고 페이지는 계속 동작

JSON API:
- `GET /api/quotes` — 워치리스트 전체 시세
- `GET /api/history/{symbol}?period=3mo` — 차트용 OHLC 이력

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
  datasources/
    base.py            # PriceSource 인터페이스 + TTL 캐시
    kr_price.py        # 국내 시세/이력 (FinanceDataReader)
    us_market.py       # 미국 시세/이력 (yfinance)
    registry.py        # 심볼 -> 출처 디스패처 (워치리스트 일괄 순회)
  web/
    api.py             # FastAPI: / · /api/quotes · /api/history/{symbol}
    __main__.py        # python -m app.web 실행 엔트리
    static/index.html  # 다크 테마 대시보드 (카드 + Chart.js 라인차트)
  core/ command/        # 후속 슬라이스용 빈 패키지
data/                  # SQLite 파일 (git 제외)
```
