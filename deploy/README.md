# VPS 24시간 배포 가이드

라이브 앱(`app.web`)을 Ubuntu VPS에서 24시간 돌려, 어디서든 접속 가능한 **초단위 실시간 대시보드**를 만든다.

## 핵심 원칙 — 키 분리
- **코드는 GitHub 경유**(`git pull`). **키(.env)는 절대 git 에 안 올린다.**
- VPS의 `.env`는 **VPS에서 직접 손으로** 만든다(이 가이드 3단계). PC의 `.env`와 별개.

---

## 1단계 · 코드 받기
VPS 터미널에서:
```bash
git clone https://github.com/prjessy/stock.git ~/stock-watchdog
cd ~/stock-watchdog
```
(이미 받았으면: `cd ~/stock-watchdog && git pull`)

## 2단계 · .env 생성 (키 입력)
```bash
cp .env.example .env
nano .env
```
- `KIS_APP_KEY`, `KIS_APP_SECRET` 두 줄에 값 입력 → `Ctrl+O`, `Enter`, `Ctrl+X` 로 저장.
- 이 파일은 `.gitignore`에 있어 **git 에 안 올라간다**(안전).

## 3단계 · 배포 실행
```bash
bash deploy/deploy.sh
```
- 파이썬 가상환경 + 의존성 설치 → systemd 서비스 등록 → 자동 시작.
- 끝에 `active (running)` 이 보이면 성공.

## 4단계 · 확인
```bash
curl -s http://localhost:8000/api/session   # 세션 JSON 이 나오면 OK
```
브라우저(폰 포함)에서: **http://&lt;VPS_IP&gt;:8000**
> 외부 접속이 안 되면 **클라우드 제공사 보안그룹/방화벽**에서 8000 포트(TCP)를 열어야 한다.

## 운영 명령
```bash
sudo systemctl status stock-watchdog      # 상태
sudo journalctl -u stock-watchdog -f      # 실시간 로그
sudo systemctl restart stock-watchdog     # 재시작
```
서버가 죽거나 VPS가 재부팅돼도 **자동으로 다시 뜬다**(systemd `Restart=always` + 부팅 자동기동).

## 코드 업데이트 (다음부터)
PC에서 `git push` → VPS에서:
```bash
cd ~/stock-watchdog && git pull && bash deploy/deploy.sh
```

---

## (선택) 도메인 + HTTPS — Cloudflare
초단위 동작을 IP로 먼저 확인한 뒤 진행 권장.
**[deploy/CLOUDFLARE.md](CLOUDFLARE.md) 참고** — Cloudflare Tunnel 방식(포트 개방·인증서 불필요, 가장 쉬움)을 추천.
전통적인 A레코드 + Caddy 방식은 `deploy/Caddyfile` 주석 참고.
