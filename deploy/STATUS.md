# 배포 상태 & 이어가기 (KIS 실시간 · VPS)

> 작업 요약 문서. 새 세션/다른 사람이 와도 여기만 보면 현황과 다음 할 일을 안다.

## 한눈에
KIS(한국투자증권) OpenAPI 실전계좌로 **국내 시세를 초단위 실시간** 조회하는 라이브 대시보드.
PC에서 개발/검증 → **Ubuntu VPS에 24시간 배포**. GitHub Pages는 별개의 무료소스 15분 미리보기.

## 아키텍처 (코드 1개, 키는 머신마다 분리)
```
        코드(git)                         키(.env, 손으로만 — git X)
 PC ──push──▶ GitHub ──pull──▶ VPS        PC의 .env       VPS의 .env
              (키 없음)                     (KIS 키)        (KIS 키, scp로 넣음)
```
- 실시간 = **상시 백엔드 필요** → VPS의 `app.web`(FastAPI, systemd). PC 꺼져도 24시간.
- GitHub Pages(`docs/`)는 정적이라 초단위 불가 → 무료소스 15분 그대로(키 불필요).

## ✅ 완료
- 실시간 코드: `app/datasources/kis_price.py`(`KisPriceSource`, REST `inquire-price`,
  토큰 캐시+연결재사용+발급실패 백오프), `app/web/realtime.py`(백그라운드 폴러 → `/api/quotes` 즉시 응답),
  `app/core/market.py`(세션: 프리장 08:00~09:00 / 본장 09:00~15:40 / 에프터장 15:40~20:00, 주말X).
- 대시보드(`app/web/static/index.html`): 1초 폴링, 세션 배지, ⚡초단위 실시간 라인차트, ⚙️ 설정(±% 표시 임계값).
- 배포 키트: `deploy/deploy.sh`(systemd 자동작성)·`README.md`·`CLOUDFLARE.md`·`Caddyfile`.
- VPS 배포 완료: `/root/stock-watchdog`, systemd `stock-watchdog.service` = active + 부팅 자동기동.
- 키 보안: `.env`/`key.txt`/`data/` gitignore(인라인주석 버그 수정), GitHub엔 키 없음 확인.
- VPS `.env` 시크릿 잘림(103자) → scp로 교체 → **180자 정상**.

## ⏳ 남은 것 (다음 세션 시작점)
1. **(즉시) VPS에서 KIS 실시간 확인** — SSH 창에서:
   ```bash
   cd ~/stock-watchdog
   chmod 600 .env
   sudo systemctl restart stock-watchdog
   sleep 5
   curl -s http://localhost:8000/api/quotes   # "source":"KIS" + error 없음이면 성공
   ```
2. **외부 접속** — 브라우저 `http://<VPS_IP>:8000`. 안 열리면 제공사 보안그룹에서 8000/TCP 개방,
   또는 3번(Cloudflare Tunnel)으로.
3. **도메인 + HTTPS** — `deploy/CLOUDFLARE.md`의 Cloudflare Tunnel 절차(포트개방/Caddy 불필요).
4. **장중 검증(08:00~20:00 KST)** — 가격 초단위 변동 + 세션 배지 전환 확인.

## 운영 명령 (VPS)
```bash
sudo systemctl status stock-watchdog       # 상태
sudo journalctl -u stock-watchdog -f       # 로그
sudo systemctl restart stock-watchdog      # 재시작
cd ~/stock-watchdog && git pull && bash deploy/deploy.sh   # 코드 업데이트
```

## ⚠️ 주의
- KIS 키는 **PC·VPS의 `.env`에만**. 절대 커밋 금지. VPS `.env`는 `scp`로 넣음(붙여넣기는 긴 시크릿이 잘림).
- 같은 앱키를 두 머신에서 동시 운용하면 토큰 발급(1분당 1회)이 충돌 → **상시 운용은 VPS 한 곳**.
- "기능 안 보임" = 거의 항상 브라우저 캐시 → Ctrl+Shift+R.
