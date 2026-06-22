# 🔐 구글 로그인 + 사용자별 매매일지 — 작업 현황 & 설정 가이드

작성: 2026-06-23 세션. 요청: ①구글 인증 ②사용자별 매매일지 ③대시보드 수급(외/기/개) ④차트 삼성·하이닉스 종가 ⑤포트폴리오 탭(총자산·비중).

---

## ✅ 완료(코드 작성 + 로컬 검증 통과)

- **구글 OAuth 로그인** — `app/auth/google_auth.py`(무의존 urllib, 카카오와 동일 방식).
  로그인/콜백/로그아웃/세션 라우트: `/api/auth/google/login`·`/callback`·`/api/auth/logout`·`/api/auth/me`.
  세션은 랜덤 `sid` httponly 쿠키 ↔ DB `sessions` 테이블(30일).
- **사용자별 매매일지** — DB `users`/`journal` 테이블 + CRUD API
  `GET/POST /api/journal`, `PUT/DELETE /api/journal/{id}`. **본인 소유만**(user_id 강제), 비로그인 401.
  프론트 **📔 매매일지 탭**: 비로그인 게이트 → 로그인 시 입력폼(날짜·구분·종목·가격·수량·이유·복기) + 목록 + 삭제.
- **인증 3모드 토글(⚙️설정)** — `off`(미사용·로그인/일지탭 숨김) / `journal`(매매일지만 로그인) / `full`(사이트 전체 로그인).
  `GET/POST /api/auth-config`, `data/auth_config.json` 저장, **변경엔 비밀번호(TRADE_PASSWORD) 필요**(아무나 못 잠금).
  `full` 모드는 비로그인 시 전체화면 게이트(클라이언트 차단).
- **📊 대시보드(써머리)에 수급 카드 추가** — 삼성·하이닉스 외국인/기관/개인 순매수(`/api/investor`).
- **💼 포트폴리오 탭 신설** — 총자산 히어로 + 🍩 종목별 비중 도넛 + 보유종목 표(KIS `/api/balance`).
  기존 써머리에 있던 총자산/비중은 이 탭으로 **이동**.
- **차트 탭** — 삼성·하이닉스 종가는 기존 히스토리 차트(캔들=종가 포함) + 📅 일별 종가 표로 이미 제공(유지).
- **웹앱 startup 에서 `init_db()` 호출 추가** — VPS DB 에 새 테이블(users/sessions/journal) 자동 생성(idempotent).
- `.env.example` 에 GOOGLE_* / REQUIRE_LOGIN 추가. `config.py` 설정 필드 추가.

검증: 라우트 전부 등록 확인, `/api/auth/me`={mode:journal}, `/api/journal` 비로그인 401,
키 미설정 시 로그인 버튼 자동 숨김, 잘못된 비번 auth-config 403 — 모두 통과.

---

## ⏳ 미완료 / 남은 일

1. **VPS 배포(동기화)** — 코드 `git push` → VPS `git pull && bash deploy/deploy.sh`.
   (이번 세션에선 시간상 보류. 아래 '배포' 절 참고. SSH 자동배포도 가능.)
2. **사용자 액션(약 10분) — Google Cloud Console OAuth 클라이언트 생성**(아래 설정 가이드).
   키 2개를 VPS `.env` 에 넣기 전엔 로그인 버튼이 안 보임(나머지 기능은 정상).

---

## 🛠 Google Cloud Console 설정 (사용자가 1회)

1. https://console.cloud.google.com → 프로젝트 생성(또는 기존 선택).
2. **API 및 서비스 > OAuth 동의 화면**: User Type=외부, 앱이름/이메일 입력, 게시(테스트 중이면 본인 계정 테스터 등록).
   scope 는 기본(email·profile·openid)이면 충분.
3. **사용자 인증 정보 > 사용자 인증 정보 만들기 > OAuth 클라이언트 ID**:
   - 애플리케이션 유형: **웹 애플리케이션**
   - **승인된 리디렉션 URI**: `https://jessystock.com/api/auth/google/callback`  ← 정확히 일치해야 함
4. 발급된 **클라이언트 ID / 클라이언트 보안 비밀번호**를 VPS `.env` 에 추가:
   ```
   GOOGLE_CLIENT_ID=<클라이언트 ID>
   GOOGLE_CLIENT_SECRET=<클라이언트 보안 비밀번호>
   GOOGLE_REDIRECT_URI=https://jessystock.com/api/auth/google/callback
   ```
5. `bash deploy/deploy.sh`(또는 서비스 재시작)로 반영. 헤더에 'G 로그인' 버튼이 보이면 성공.

> 🔐 **보안**: GOOGLE_CLIENT_SECRET 도 KIS·카카오 키처럼 **절대 git 커밋 금지** — VPS `.env` 에만.
> `.gitignore` 가 `.env` 를 추적하지 않으므로 `git pull` 배포로도 덮어쓰이지 않는다. (`docs/SECURITY_ENV.md`)

---

## 🚀 배포(나중에)

```bash
# PC
git add -A && git commit -m "..." && git push
# VPS (SSH)
cd ~/stock-watchdog && git pull && bash deploy/deploy.sh
```
DB(`data/`)는 gitignore 라 VPS 자체 DB 유지 — 새 테이블은 startup `init_db()` 가 자동 생성.
PC SSH 키로 VPS 자동배포도 가능(메모리 참고).
