# 주식 알림/분석 보조 시스템 — Architect 설계 (코드 없음)

> 전제: planner_output.md v1.1의 결정사항 기준. **구현 코드는 작성하지 않음.** 파일 구조·모듈 경계·데이터 흐름·인터페이스·스키마·테스트 전략만 정의.

## 1. 기술 스택
- 언어: Python
- 웹/API: FastAPI (대시보드 JSON API + Hermes 인바운드 수신)
- 저장소: SQLite (storage 추상화 → 추후 MySQL 전환)
- 스케줄러: APScheduler
- 데이터: FinanceDataReader/pykrx(국내), yfinance(미국 MU/SOX/NQ), OpenDartReader(국내 공시)
- 차트: 데이터는 백엔드 제공, 렌더는 프론트(경량 차트 라이브러리)

## 2. 디렉터리 구조 (제안)
```
stock/
  app/
    main.py              # 엔트리: 스케줄러 + FastAPI 동시 기동
    config.py            # .env 로딩, 종목/임계값/시각/허용 chat_id 설정
    core/
      threshold_engine.py  # 전일종가 대비 ±% 판정
      dedupe.py            # 중복 방지 (거래일, 종목, 임계값)
      scheduler.py         # 폴링(60s)/07:00 브리핑/일일 분석 리프레시
      analysis.py          # 재무지표 + 차트 보조지표(MA/RSI/MACD) 계산
      briefing.py          # 미국(MU/SOX/NQ) 아침 브리핑 생성
      marketing.py         # 뉴스/공시·IR/애널 의견/SNS 취합
    datasources/
      base.py              # PriceSource/FinancialSource/NewsSource 인터페이스
      kr_price.py          # 국내 시세
      us_market.py         # MU/SOX/NQ
      financials.py        # 재무
      news.py              # 뉴스/공시/커뮤니티
    notify/
      base.py              # Notifier 인터페이스 (send_message/send_report)
      hermes.py            # Hermes HTTP 구현체 (localhost)
    command/
      router.py            # 인바운드 명령 파싱 → core 서비스 디스패치 → 응답
    web/
      api.py               # FastAPI 라우트: 대시보드 JSON + /hermes/inbound
      static/              # 경량 프론트(차트/지표 표시)
    storage/
      db.py                # SQLite 추상화 (Repository 패턴)
      models.py            # 테이블 정의
  data/                    # SQLite 파일 (git 제외)
  .env.example             # 설정 템플릿 (실제 .env는 git 제외)
  requirements.txt
  README.md
```

## 3. 핵심 인터페이스 (개념 — 시그니처 수준)
- **Notifier** (notify/base.py)
  - `send_message(text) -> bool`
  - `send_report(payload) -> bool`
  - 구현체: `HermesNotifier`(localhost HTTP POST). 추후 다른 채널 추가 가능.
- **PriceSource** (datasources/base.py)
  - `get_prev_close(symbol) -> float`
  - `get_current_price(symbol) -> float`
- **FinancialSource**: `get_fundamentals(symbol) -> dict`
- **NewsSource**: `get_marketing(symbol) -> {news, filings, analyst, sns}`
- **Repository** (storage/db.py): 알림이력/기준가/분석캐시 CRUD. 인터페이스로 두어 SQLite→MySQL 교체 시 상위 로직 불변.

## 4. 데이터 흐름
1. **가격 알림**: scheduler(60s) → kr_price/us_market 조회 → threshold_engine 판정 → dedupe 확인 → 신규면 Notifier(Hermes)→Telegram, 이력 저장.
2. **아침 브리핑**: scheduler 07:00 → briefing 생성(MU/SOX/NQ) → Notifier 발송.
3. **명령(인바운드)**: Telegram → Hermes(AI 의도해석) → `POST /hermes/inbound` → command/router → core 호출 → 응답을 Notifier로 회신.
4. **대시보드**: 브라우저 → FastAPI JSON API(캐시된 분석/시세) → 프론트 렌더. 외부 접속은 VPS IP→추후 Cloudflare 도메인.

## 5. SQLite 스키마 (초안)
- `alerts(id, trade_date, symbol, threshold, fired_at)` — 중복키 (trade_date, symbol, threshold) UNIQUE
- `prev_close(symbol, trade_date, close)` — 기준가 캐시
- `analysis_cache(symbol, updated_at, fundamentals_json, indicators_json)`
- `marketing_cache(symbol, updated_at, news_json, filings_json, analyst_json, sns_json)`
- `briefings(date, payload_json, sent_at)`

## 6. 스케줄/시간대
- 폴링: 한국 정규장 09:00~15:30 KST, 60초 간격
- 거래일 변경 시: 기준가/당일 알림이력 리셋
- 아침 브리핑: 매일 07:00 KST
- 미국 데이터: EST/EDT → KST 변환 처리(전일 종가 + 야간 선물)

## 7. 보안/운영
- secrets는 `.env`(허용 chat_id 포함), git 제외
- Hermes 연동은 localhost 내부 통신(외부 노출 X)
- 대시보드: 지인 공유용 → 가벼운 접근제한(비번/허용목록)
- 부분 실패 격리: 한 데이터소스/전송 실패가 전체 중단 안 되게
- 데이터 연속 실패 시 Telegram 1회 헬스 통지

## 8. 테스트 전략
- 단위: threshold_engine(경계값 ±3% 정확/중복), dedupe(키 단위 1회), 시간대 변환
- 통합: datasource 어댑터 mock으로 알림→Notifier 호출 검증
- 회귀: 재시작 후 이력 유지(중복 미발송) 시나리오
- 수동: 실제 Hermes localhost 연동 1건 발송 확인

## 9. 1차 구현 슬라이스(권장 순서) — *구현은 승인 후*
1. config + storage(SQLite) + Notifier(Hermes) 골격
2. 국내 2종목 ±3% 알림 + 중복방지 (vertical slice)
3. 미국 07:00 브리핑
4. 종목 분석(재무+차트지표) + 대시보드 API/프론트
5. 마케팅 자료 취합(되는 출처부터)
6. 명령 라우터(현재가/브리핑/분석)

> reviewer는 최종 단계에서 fresh context로 검증(요구사항 누락/중복알림 버그/과설계/보안).
