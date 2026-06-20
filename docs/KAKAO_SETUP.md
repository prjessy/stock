# 카카오톡 '나에게 보내기' 연동 가이드 (무료)

본인 카톡(나와의 채팅)으로 알림을 받는 기능. 최초 1회 카카오 로그인(OAuth)만 하면,
이후 access_token 자동 갱신으로 무인 동작한다. (코드: `app/kakao_notify.py`, API: `app/web/api.py`)

## ⚠️ 핵심 — 설정은 전부 "플랫폼 키(REST API 키)" 화면에 있다

콘솔이 개편되면서 **Redirect URI는 [카카오 로그인] 페이지가 아니라 [앱 설정]→[앱 키(플랫폼 키)]→[REST API 키]** 화면에 있다.
키를 만들/관리할 때 그 화면에서 한 번에 등록한다. (이걸 몰라서 헤맴 — 2026-06)

- ❌ "로그아웃 리다이렉트 URI" 칸 = 다른 용도. 여기 넣으면 KOE006.
- ✅ "카카오 로그인 리다이렉트 URI" 칸 = 여기에 넣어야 함.

## 설정 순서 (developers.kakao.com)

앱: `stockwatch` / App ID `1491760` / REST API 키 `a9d5fad7...`

1. **앱 활성화 등 기본**
   - [제품 설정]→[카카오 로그인] → **활성화 ON**
   - [제품 설정]→[카카오 로그인]→[동의항목] → **"카카오톡 메시지 전송(talk_message)" 활성화**
   - OpenID Connect = **OFF** (안 씀)

2. **Redirect URI 등록** ← 가장 헷갈리는 부분
   - [앱 설정]→[앱 키/플랫폼 키]→**[REST API 키]** 화면
   - **"카카오 로그인 리다이렉트 URI"** 칸에 정확히 입력 후 `+` → 저장:
     ```
     https://jessystock.com/api/kakao/callback
     ```

3. **Client Secret** ([카카오 로그인]→[고급], 이 콘솔엔 "보안"이 아니라 "고급")
   - 우리 서버는 토큰 요청에 client_secret을 **안 보냄**.
   - 따라서 Client Secret **"사용 안 함"** 으로 두어야 한다. "사용함"이면 토큰 교환에서 **HTTP 401**.
   - (켜두고 쓰려면 `kakao_notify.py`의 토큰 요청에 `client_secret`을 추가하고 `.env`에 값 저장 필요.)

4. **서버 `.env`** (VPS, scp 전송)
   ```
   KAKAO_REST_API_KEY=<REST API 키>
   KAKAO_REDIRECT_URI=https://jessystock.com/api/kakao/callback
   ```

## 연동 실행 (최초 1회)

1. 브라우저에서 https://jessystock.com/api/kakao/login 열기 (카톡 로그인 상태)
2. 동의 → "✅ 카카오 연동 완료"
3. 테스트: https://jessystock.com/api/kakao/test → `{"ok":true}` 면 카톡에 메시지 도착
4. 토큰은 `data/kakao_token.json` 에 저장(refresh_token 회전 반영). 이 파일 지우면 재연동 필요.

## 에러 코드별 원인 (실제 겪은 것)

| 증상 | 원인 | 해결 |
|---|---|---|
| `KOE006` / "서비스 설정 오류, 관리자 확인 필요" | Redirect URI 미등록·오타 | REST API 키 화면 "카카오 로그인 리다이렉트 URI"에 정확히 등록 |
| 콜백 `code 없음` / `raw {}` | 위 KOE006 때문에 카카오가 code 없이 되돌림 | 위와 동일 |
| `HTTP Error 401` (code는 도착) | Client Secret "사용함" 인데 서버가 미전송 | [고급]에서 Client Secret "사용 안 함" |

## 한계 — '나에게 보내기'는 로그인한 본인에게만 간다

- memo API(`/v2/api/talk/memo/default/send`)는 **OAuth 로그인한 당사자 1명**에게만 전송.
- 현재 서버는 토큰을 **1개만** 저장 → **마지막으로 로그인한 1명**만 받는다.
- 여러 명에게 알림 → **텔레그램이 적합**(이미 병행 중). 카카오로 타인에게 보내려면:
  - 소수 테스터: [앱 설정]→[팀 관리]에 그 사람 카카오계정 등록 후 각자 로그인. 단 토큰 1개 저장이라 동시 다수는 서로 덮어씀 → **사용자별 토큰 저장 개발 필요**.
  - 일반 사용자: talk_message를 팀원 아닌 일반인에게 쓰려면 **비즈앱 전환 + 동의항목 검수**.
  - 친구톡/알림톡(진짜 대량 발송): **비즈채널 + 유료 + 검수**.
