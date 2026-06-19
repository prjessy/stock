"""엔트리포인트 — 가격 알림 데몬.

설정 로딩 + DB 초기화 후 스케줄러(알림 데몬)를 기동한다.
대시보드(FastAPI)는 uvicorn 으로 별도 기동하므로 여기서 띄우지 않는다.

실행:
  python -m app.main                # 알림 데몬 상시 실행 (장 시간에만 폴링)
  python -m app.main --once         # 알림 1패스만 즉시 실행(장 시간 게이트 무시) 후 종료
  python -m app.main --dry-run      # 전송 대신 콘솔에 메시지 출력 (Hermes 불필요)
  python -m app.main --once --dry-run   # 1패스 + 콘솔 출력 (수동 검증용)
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.config import settings
from app.core import alerts, scheduler
from app.datasources.registry import SourceRegistry
from app.notify.console import ConsoleNotifier
from app.notify.hermes import HermesNotifier
from app.storage.db import init_db


def _print_banner() -> None:
    print("=" * 52)
    print(" 차트 탐지견 - 알림 데몬 기동")
    print("=" * 52)
    print(f" KR 종목       : {', '.join(settings.kr_symbols)}")
    print(f" US 지표       : {', '.join(settings.us_symbols)}")
    print(f" 임계값(%)     : {settings.thresholds}")
    print(f" 폴링 주기(s)  : {settings.poll_interval_seconds}")
    print(f" 장 운영시간   : {settings.market_open} ~ {settings.market_close} KST")
    print(f" Hermes URL    : {settings.hermes_base_url}")
    print(f" DB 경로       : {settings.db_path} (초기화 완료)")
    print("=" * 52)


def main() -> None:
    parser = argparse.ArgumentParser(description="차트 탐지견 가격 알림 데몬")
    parser.add_argument(
        "--once", action="store_true",
        help="알림 1패스만 즉시 실행(장 시간 무시) 후 종료",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="전송 대신 콘솔에 메시지 출력(Hermes 불필요)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Windows 콘솔(cp949)에서도 한글/기호가 깨지지 않도록 stdout 을 UTF-8 로.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    repo = init_db(settings.db_path)
    registry = SourceRegistry()
    notifier = ConsoleNotifier() if args.dry_run else HermesNotifier()

    _print_banner()

    if args.once:
        # 장 시간 게이트를 무시하고 즉시 1패스 (수동 검증 경로).
        print(" [--once] 알림 1패스 즉시 실행 (장 시간 무시)")
        sent = alerts.run_once(repo, notifier, registry)
        print(f" 발송 {sent}건 (이미 발송한 조건은 중복 방지로 제외됨)")
        repo.close()
        return

    try:
        scheduler.run(repo, notifier, registry)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
