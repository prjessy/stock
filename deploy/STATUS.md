# 배포 상태 & 이어가기 (KIS 실시간 · VPS)

> 작업 요약 문서. 새 세션/다른 사람이 와도 여기만 보면 현황과 다음 할 일을 안다.
> ⚠️ VPS IP·SSH 등 접속정보는 여기(공개 레포)에 적지 않는다 — 로컬 메모리에만 둔다.

## 한눈에
KIS(한국투자증권) OpenAPI 실전계좌로 **국내 시세를 초단위 실시간** 조회하는 라이브 대시보드.
Ubuntu VPS에 systemd로 24시간 배포. GitHub Pages는 별개의 무료소스 15분 미리보기.

## 아키텍처 (코드 1개, 키는 머신마다 분리)
```
        코드(git)                         키(.env, 손으로만 — git X)
 PC ──push──▶ GitHub ──pull──▶ VPS        PC의 .env       VPS의 .env
              (키 없음)                     (KIS 키)        (KIS 키, scp/직접)
```
- 실시간 = 상시 백엔드 필요 → VPS의 `app.web`(FastAPI, systemd, active+부팅 자동기동).
- GitHub Pages(`docs/`)는 정적이라 초단위 불가 → 무료소스 15분(키 불필요).

## ✅ 완료
- KIS 실시간(`app/datasources/kis_price.py`): REST `inquire-price`, 토큰 캐시(`data/kis_token.json`),
  커넥션 재사용, **발급 실패 시 60초 백오프**(하머링/레이트리밋 방지). `registry`는 키 있으면 KIS, 없으면 FDR 폴백.
- 백그라운드 폴러(`app/web/realtime.py`): 시세를 메모리에 미리 받아 `/api/quotes` 즉시 응답(느린 소스로 인한 정체 제거).
- 세션(`app/core/market.py`): 프리장 08:00~09:00 / 본장 09:00~15:40 / 에프터장 15:40~20:00 (주말 구분 없음).
- 대시보드(`app/web/static/index.html`):
  - 종목카드(실시간, 가격 변동 시 플래시, ±% 🔔)
  - ⚡ 실시간(초단위 누적 라인 차트)
  - 🕔 오늘 흐름 **표** — `/api/intraday`(yfinance), 간격 1/5/15/30분 옵션(기본 5분). **정규장 09:00~15:30만**.
  - 📈 히스토리(1개월 일봉 차트) + 📅 일별 종가 **표**
  - 🟢 LIVE 표시(폴링마다 펄스 + 수신시각 + 지연/끊김), ⚙️ 설정(클라이언트 ±% 임계값)
- 배포 키트 `deploy/`(`deploy.sh`·`README.md`·`CLOUDFLARE.md`·`Caddyfile`). VPS 배포 완료.
- 보안: 키는 `.env`에만, `.gitignore`로 `key.txt`/`.env`/`data/` 제외(인라인 주석 버그도 수정).

## ⏳ 남은 것 / 다음
1. **도메인 + HTTPS** — `jessystock.com` 등록(Cloudflare Registrar) → Cloudflare Zero Trust 터널 생성
   → VPS에 `cloudflared` 설치(토큰 1줄) → Public Hostname `→ http://localhost:8000` → `https://jessystock.com`.
   무료 대안: **DuckDNS**(`http://이름.duckdns.org:8000`). 자세히는 `deploy/CLOUDFLARE.md`.
2. **장중 검증(08:00~20:00 KST)** — LIVE 펄스·카드 플래시·5분 표 갱신 확인(진짜 실시간 증거).
3. **(선택) 시간외 분봉** — yfinance엔 프리/에프터 분봉이 없음 → 필요 시 KIS 틱을 서버에서 5분 집계(별도 작업).
4. **(선택) 헤르메스(텔레그램 알림) / Claude API(AI 브리핑)** — 현재 대시보드와 무관, 나중에.

## 운영 명령 (VPS)
```bash
sudo systemctl status stock-watchdog        # 상태
sudo journalctl -u stock-watchdog -f        # 로그
sudo systemctl restart stock-watchdog       # 재시작
cd ~/stock-watchdog && git pull && bash deploy/deploy.sh   # 코드 업데이트
```

## ⚠️ 주의
- KIS 키는 PC·VPS의 `.env`에만. 절대 커밋 금지(`.gitignore` 확인됨).
- VPS `.env`의 긴 시크릿(180자)은 터미널 붙여넣기 시 줄바꿈이 껴 잘림 → `scp`나 heredoc 파이프로 전송.
- 같은 앱키를 두 머신에서 동시 운용하면 토큰 발급(1분당 1회)이 충돌 → 상시 운용은 VPS 한 곳.
- "기능 안 보임" = 거의 항상 브라우저 캐시 → Ctrl+Shift+R / 시크릿창.
