# 텔레그램 알림 받기 — 설정 가이드

알림(목표가·더듬이 사이렌)을 텔레그램으로 받기 위한 단계. **유저명은 직접 입력하지 않습니다** —
봇에게 `/start` 하면 `chat_id`가 자동 등록됩니다. 필요한 건 **봇 토큰** 하나입니다.

---

## 1. 텔레그램 봇 만들기 (1분)
1. 텔레그램에서 **@BotFather** 검색 → 대화 시작
2. `/newbot` 입력
3. 봇 이름(아무거나) → 봇 username(끝이 `bot`, 예: `my_stock_watch_bot`) 입력
4. BotFather가 주는 **토큰** 복사 (예: `8123456789:AAH...xyz`)

## 2. 서버에 토큰 등록 (.env)
hermes 게이트웨이가 그 봇으로 메시지를 보냅니다. 서버에서:
```
# /root/.hermes/.env  (hermes 설정)
TELEGRAM_BOT_TOKEN=8123456789:AAH...xyz
```
또는 대화형으로: `hermes setup` → Telegram 선택 → 토큰 입력.
설정 후 게이트웨이 재시작: `systemctl restart hermes-gateway`

## 3. 내 chat_id 자동 등록 (유저명 입력 X)
1. 텔레그램에서 **방금 만든 내 봇**을 검색
2. 봇에게 **`/start`** (또는 아무 메시지) 전송
3. → hermes가 내 `chat_id`를 **자동 등록**(`~/.hermes/channel_directory.json`)
4. 끝. 이제 그 봇이 나에게 알림을 보냅니다.

> 즉 "유저명"을 어디에도 적지 않습니다. `/start` 한 번이 등록을 대신합니다.

## 4. 확인
```
hermes send --to telegram "테스트"
```
텔레그램에 "테스트"가 오면 정상.

---

## 멀티 사용자 (모델 A)
- **각 사용자가 본인 봇 + 본인 서버**로 자체 호스팅 → 위 1~3을 각자 수행.
- 키(봇 토큰)는 각자 본인 `.env`에만, 운영자는 받지 않음.
- 한 봇을 여러 명이 쓰려면 각자 그 봇에 `/start` 하면 각 `chat_id`가 등록되고, 특정 대상 발송은
  `hermes send --to telegram:<chat_id>` 로 지정.

## 트러블슈팅
- 안 옴 → 봇을 차단(block)하지 않았는지, `/start` 했는지, 토큰이 맞는지 확인.
- `hermes status` 로 Telegram 연결·등록 채널 확인.
