# Cloudflare 도메인 + HTTPS 가이드

목표: `https://내도메인` 으로 VPS 대시보드 접속. **Cloudflare Tunnel** 방식을 추천한다
(방화벽 포트 개방 불필요, HTTPS 자동, Caddy 불필요 — 가장 안전하고 쉬움).

> 선행: 먼저 `deploy/README.md` 로 VPS에서 앱이 `http://localhost:8000` 에 떠 있어야 한다.

---

## A. 도메인을 Cloudflare에 올리기

### A-1. 도메인이 없다면
- Cloudflare 대시보드 → **Domain Registration → Register Domains** 에서 구매(원가, 보통 연 $10 안팎),
  또는 아무 등록기관(가비아/Namecheap 등)에서 구매.

### A-2. 도메인을 Cloudflare에 추가 (다른 곳에서 샀을 때)
1. Cloudflare 대시보드 → **Add a site** → 도메인 입력 → Free 플랜 선택.
2. Cloudflare가 **네임서버 2개**(예: `xxx.ns.cloudflare.com`)를 알려줌.
3. 도메인 산 곳(등록기관) 관리화면에서 **네임서버를 그 2개로 변경**.
4. 활성화까지 보통 수 분~수 시간. (Cloudflare에서 구매했으면 이 단계 자동.)

---

## B. Cloudflare Tunnel 연결 (추천)

### B-1. 터널 생성 (Cloudflare 대시보드)
1. **Zero Trust** → **Networks → Tunnels** → **Create a tunnel**.
2. 종류 **Cloudflared** 선택 → 터널 이름(예: `stock`) → Save.
3. 다음 화면에 **설치 명령어**가 나옴 (`cloudflared service install eyJ...` 형태의 긴 토큰 포함). 복사.

### B-2. VPS에 cloudflared 설치 + 실행
VPS 터미널에서 (Ubuntu):
```bash
# cloudflared 설치
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# B-1에서 복사한 설치 명령 그대로 붙여넣기 (예시):
sudo cloudflared service install eyJhIjoi...복사한_토큰...
```
이러면 cloudflared가 서비스로 등록되어 부팅 시 자동 실행된다.

### B-3. 공개 호스트네임 매핑 (Cloudflare 대시보드)
같은 터널 설정의 **Public Hostname** 탭 → **Add a public hostname**:
- **Subdomain**: 예 `stock` (→ `stock.내도메인`)
- **Domain**: 본인 도메인 선택
- **Type**: `HTTP`
- **URL**: `localhost:8000`
- Save.

### B-4. 확인
브라우저에서 **`https://stock.내도메인`** 접속 → 대시보드가 HTTPS로 뜬다.
- DNS는 Cloudflare가 자동 생성(CNAME → 터널). 방화벽 포트 개방 **불필요**.
- 보안 강화(선택): Zero Trust → Access 로 접속에 이메일 인증을 걸 수 있음.

---

## C. (대안) DNS A 레코드 + Caddy
터널 대신 전통 방식을 원하면 `deploy/Caddyfile` 참고:
1. Cloudflare DNS에 **A 레코드**: `stock.내도메인 → VPS IP` (**처음엔 회색 구름=DNS only** — Caddy가 인증서 발급하도록).
2. VPS 보안그룹/방화벽에서 **80, 443** 개방.
3. Caddy 설치 후 `Caddyfile` 도메인 수정 → 적용. `https://stock.내도메인` 확인.
4. 정상 후 Cloudflare 프록시(주황 구름) 켜려면 SSL 모드를 **Full (strict)** 로.

> 8000 포트는 Cloudflare 프록시 대상이 아니라, 프록시를 쓰려면 Caddy(80/443)가 필요하다.
> 그래서 비전문가에겐 **B(터널)** 가 훨씬 간단하다.
