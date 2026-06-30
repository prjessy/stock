# 다음 세션 할 일 — 아이폰 세션 / PWA / 플레이스토어

작성: 2026-06-26 (세션 핸드오프)

## 🔎 원인 (찾음)
- **아이폰 "홈 화면에 추가" 아이콘(standalone)** 으로 열면 로그인 세션이 앱 종료 시 사라짐.
- iOS 는 홈화면 웹앱을 **별도 쿠키 저장소**로 돌리는데, 그동안 **PWA manifest/service worker 가 없어** iOS 가 *옛 standalone 모드*(쿠키 휘발)로 실행한 것.
- PC(크롬)·Safari 직접 접속은 정상 유지됨(쿠키 저장소가 영구).

## ✅ 이번 세션에 한 것 (배포 완료)
1. **세션 쿠키 강화** (`api.py _set_sid_cookie`): `Max-Age + Expires(30일)` 둘 다 + **SameSite=Lax**(None→Lax, iOS ITP 휘발 방지). HttpOnly·Secure.
2. **PWA 정식 구성** — 아이폰 홈화면 설치 시 세션 유지 + Play Store TWA 1단계:
   - `app/web/static/manifest.webmanifest` (name·icons 192/512·display:standalone·theme)
   - `app/web/static/sw.js` (서비스워커: 같은출처 GET 네트워크우선+캐시, API/CDN 비개입)
   - `api.py` 라우트: `GET /sw.js`(루트 스코프), `GET /manifest.webmanifest`(application/manifest+json)
   - `index.html <head>`: manifest 링크 + apple-mobile-web-app-* 메타 + SW 등록 스크립트
   - 검증: `/sw.js`=200 js, `/manifest.webmanifest`=200 manifest+json ✅

## ▶ 다음 세션: 먼저 검증 (사용자)
1. 아이폰 Safari 로 **jessystock.com 직접** 접속(인앱 브라우저·시크릿 금지)
2. **로그아웃 → 재로그인**(Lax 새 쿠키 발급) — 구글 또는 카카오
3. **기존 홈화면 아이콘 삭제 → 다시 「홈 화면에 추가」**(새 manifest 반영 필수)
4. 홈화면 아이콘으로 열기 → 로그인 → **앱 완전 종료(백그라운드 제거) → 다시 열기**
5. ✅ 로그인 유지되면 해결. ❌ 그래도 안 되면 아래 추가안.

### 그래도 안 될 때 (iOS 버전·환경 따라)
- iOS 16.4 미만이면 standalone 쿠키 영속이 약함 → iOS 업데이트 권장.
- 최후수단: 로그인 유지 토큰을 별도 처리(복잡) 또는 "Safari 직접 사용 안내". 단 **안드로이드 Play Store TWA 는 크롬 쿠키 저장소라 이 문제 없음**.

## 🗺️ 플레이스토어 등록 (다음 단계)
순서: **PWA(완료) → TWA 패키징 → Play Console**
1. **PWABuilder**(pwabuilder.com)에 `https://jessystock.com` 입력 → manifest/SW 점검 통과 확인 → **Android 패키지(.aab) 생성**
2. **Digital Asset Links**: `https://jessystock.com/.well-known/assetlinks.json` 추가(주소창 없는 풀스크린) — PWABuilder가 주는 SHA256 지문 넣기. (api.py 라우트 또는 static 서빙 필요)
3. **Google Play Console**: 개발자 등록 **$25 1회(평생, 월 아님)** + 신원확인 → 앱 생성 → 스토어 등록정보(설명·스크린샷·아이콘) → 콘텐츠등급 → **개인정보처리방침 URL(필수)** → 데이터보안 양식 → .aab 업로드(내부테스트→프로덕션) → 심사(며칠)
4. ⚠️ 금융/투자 앱: 심사 엄격 + "수익보장" 표현 금지 + 위험고지·면책 필요.
- 내가 해줄 것: assetlinks 라우트, 개인정보처리방침 초안, 스크린샷/설명 가이드.
- 본인: Play 개발자 등록·결제·심사 제출·법률 검토.

## 📌 기타 미해결/확인
- 카카오 로그인: 작동 확인됨(user id 12). 단 카카오 계정=구글과 별개 사용자.
- 자동매매 분할/시간범위/기준가: 모의투자(KIS_PAPER) 실검증 권장.
- 공모주 상장일: 네이버 누락분 보강 여지(38커뮤니케이션).
- 익명 localStorage 개인화: 백엔드만 존재, 프론트 보류(로그인=개인화로 결정).

## 운영 메모
- 배포: 변경 파일만 `tar→scp→systemctl restart stock-watchdog`(git 미경유). 백업 `~/stock-watchdog/.deploy_bak/<ts>/`.
- SSH 접속정보(IP·키)는 **공개 레포에 적지 않음** — 로컬 메모리 `stock-watchdog-deploy` 참조.
- 소유자=구글 user1(prjessy@gmail.com). 카카오 단일토큰 알림=소유자만.
