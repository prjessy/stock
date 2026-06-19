첫 번째 목표는 로컬에서 동작하는 삼성전자 Telegram 알림 기능이다.

요구사항:
- 종목: 삼성전자
- 기준가: 전일 종가
- 알림 조건: +3%, -3%
- 같은 조건은 중복 발송 금지
- 로컬 PC에서 실행
- Telegram Bot으로 메시지 전송
- 나중에 Hermes를 붙일 수 있도록 notifier 인터페이스 분리
- secrets는 .env 사용
- 너무 복잡한 구조 금지

작업 방식:
1. planner가 먼저 요구사항을 재정리해라.
2. 부족한 정보가 있으면 질문해라.
3. acceptance criteria를 명확히 적어라.
4. architect가 파일 구조와 모듈 경계를 설계해라.
5. builder는 내가 승인한 뒤에만 구현해라.
6. reviewer는 마지막에 점검해라.

먼저 planner 결과만 보여줘.