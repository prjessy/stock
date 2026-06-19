"""엔트리포인트 (골격).

설정 로딩 + DB 초기화가 정상 동작하는지 확인하는 최소 실행기.
스케줄러/FastAPI 기동은 후속 슬라이스에서 추가한다.

실행: python -m app.main
"""
from __future__ import annotations

import logging
import sys

from app.config import settings
from app.storage.db import init_db


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Windows 콘솔(cp949)에서도 한글/기호가 깨지지 않도록 stdout 을 UTF-8 로.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    # DB 초기화 (테이블 idempotent 생성).
    repo = init_db(settings.db_path)
    repo.close()

    # 시작 배너: 설정 로딩 + DB 초기화 확인.
    print("=" * 52)
    print(" 주식 알림/분석 보조 시스템 - skeleton 기동 OK")
    print("=" * 52)
    print(f" KR 종목       : {', '.join(settings.kr_symbols)}")
    print(f" US 지표       : {', '.join(settings.us_symbols)}")
    print(f" 임계값(%)     : {settings.thresholds}")
    print(f" 폴링 주기(s)  : {settings.poll_interval_seconds}")
    print(f" 장 운영시간   : {settings.market_open} ~ {settings.market_close} KST")
    print(f" 아침 브리핑   : {settings.briefing_time} KST")
    print(f" Hermes URL    : {settings.hermes_base_url}")
    print(f" DB 경로       : {settings.db_path} (초기화 완료)")
    print(f" 허용 chat_id  : {settings.allowed_chat_ids or '(미설정)'}")
    print("=" * 52)
    print(" NOTE: 스케줄러/FastAPI 는 후속 슬라이스에서 기동합니다.")


if __name__ == "__main__":
    main()
