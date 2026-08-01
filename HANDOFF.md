# HANDOFF — stock watchdog

> 세션 종료 시 갱신하는 인수인계 문서. 접속정보(IP·키)는 여기 적지 않는다(로컬 메모리·key.txt 전용).
> 마지막 갱신: 2026-08-02

## 라이브
| 항목 | 값 |
|---|---|
| 공개 URL | https://jessystock.com (Cloudflare Tunnel → localhost:8000) |
| VPS 서비스 | systemd `stock-watchdog` · 경로 `~/stock-watchdog` · 포트 8000 |
| 배포 방식 | **scp + systemctl restart** (git pull 아님) — `app/llm.py`는 git 추적 제외, VPS에만 존재 |
| LLM | 사내 LSAP 게이트웨이(OpenAI 호환) · `qwen3.5-122b-fast` 단일 모델(텍스트+비전 겸용) |
| 동기화 상태 | git main = VPS 배포본 = 로컬 (2026-08-02 md5 대조 확인, HEAD c3aeece 기준) |

## 이번 세션(2026-08-02): 매매일지 사진 OCR 검증·개선
**검증 로그** — VPS 실서버 `/api/journal/photo`를 임시 세션(sid DB 삽입)으로 직접 호출:
- 비로그인 401, 업로드→`data/uploads/<uid>/` 저장, 사진 GET, OCR 추출, 산출물 정리까지 E2E 정상.
- 실제 증권앱 캡처 2건(케이뱅크 매수·하이트진로 매도): 날짜·단가·수량·세금 100% 정확.
  초기 결함 2건 발견 → 프롬프트 보완(커밋 c3aeece) 후 전 필드 일치:
  ① 종목명이 화면 상단 제목에 있는 UI에서 name 누락 ② 거래구분 텍스트 잘림 시 매수로 오인
  (→ 거래세>0·잔고 증감으로 매도 판별하게 함).
- **증권사 폼 독립성 확인**: 토스형 카드UI(구매/판매 용어)·키움형 다건 표·한투형 라벨나열·
  미국주식(USD/AAPL) 4종 목업 → 8/8 엔트리 전부 정확. 전문 OCR 모델 불필요(멀티모달 qwen 하나로 처리).
- 처리 시간 건당 7~11초(LLM 비전 호출). OCR 실패 시에도 사진 첨부는 유지 + 실패 사유 반환.
- 설계 유의: OCR 결과는 **자동 저장 아님** — `llm` 배지 + `needs_review`로 입력폼만 채우고 사용자가 확인 후 저장.

**테스트 노하우**: sessions 테이블 만료시각은 `datetime.isoformat()`(T+타임존) 형식 문자열비교 —
SQLite `datetime('now')` 형식으로 넣으면 만료 취급되어 401.

## 미완 / 다음 후보
- 삼성전자 ±2% 자동매매 연결(KIS 주문모듈·수동테스트 버튼은 완료, 실전계좌).
- 더듬이 2단계(더듬이1/4). 자동감시 인터벌은 API 비용 문제로 꺼둔 상태(`DEUDEUMI_INTERVAL_MIN=0`).
- 구글 로그인: 코드 완료, VPS 구글 키 설정 확인 필요 여부 점검.
- `/api/health` 엔드포인트 부재(공통 규약 항목) — 필요 시 버전·LLM 준비상태 반환용으로 추가.

## 저장소 주의
- 루트의 한글 이름 스크린샷·mp4·hwp 들은 사용자 개인 작업파일 — **커밋하지 않는다**
  (특히 `매매일지*.png`는 실계좌 거래내역 캡처).
- `key.txt`/`.env`/`data/`/`app/llm.py`는 git 금지(.gitignore·추적제외 유지).
