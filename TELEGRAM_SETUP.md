# 텔레그램 알림 받기 — 설정 가이드

**받는 사람은 봇을 만들 필요가 없습니다.** 운영자 봇에게 `/start` 한 번이면 끝.
유저명(@아이디)은 입력하지 않습니다 — 텔레그램 규칙상 봇은 **먼저 `/start` 한 사람에게만**
보낼 수 있어, `/start`가 곧 "구독+등록" 역할을 합니다.

---

## A. 받는 사람 (여러 명, 매우 간단)
1. 운영자가 준 **봇 링크**(예: `t.me/my_stock_watch_bot`) 클릭
2. **`/start`** 누르기 (또는 아무 메시지)
3. 끝 — 이제 알림이 내 텔레그램으로 옵니다. (chat_id 자동 등록)

> 봇 생성·토큰·유저명 입력 전부 불필요. `/start` 한 번이 전부.
> 왜 username만으론 안 되나? 텔레그램은 스팸 방지로 **봇이 먼저 말 건 사람에게만** 발송 허용.

## B. 운영자 (한 번만, 봇 1개로 모두에게)
1. 텔레그램 **@BotFather** → `/newbot` → 봇 이름·username 정하기 → **토큰** 복사
2. 서버 `~/.hermes/.env` 에 `TELEGRAM_BOT_TOKEN=<토큰>` (또는 `hermes setup`)
3. `systemctl restart hermes-gateway`
4. 봇 링크(`t.me/<봇username>`)를 받을 사람들에게 공유 → 각자 A 수행
5. 확인: `hermes send --to telegram "테스트"`

- 봇 **1개로 여러 명**이 받습니다. 각자 `/start` 하면 각 chat_id가 등록됨.
- 특정 사람에게만 보내기: `hermes send --to telegram:<chat_id>`
- 등록된 채널 확인: `~/.hermes/channel_directory.json` / `hermes status`

## 모델 A(자체 호스팅)와의 관계
- **매매 키**(KIS 등)는 각자 본인 서버 `.env`에만 — 운영자가 안 받음(보안).
- **알림 수신**은 비밀이 아니므로, 운영자 봇에 `/start`만으로 충분(키 불필요).

## 트러블슈팅
- 안 옴 → `/start` 했는지, 봇 차단 안 했는지, 토큰 맞는지 확인.
- `hermes status` 로 Telegram 연결·등록 채널 확인.
