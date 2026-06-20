"""알림 발송 디스패치 — 텔레그램(Hermes) + 카카오('나에게') 동시 전송.

텔레그램은 같은 VPS 의 Hermes CLI(`hermes send --to telegram`)로 중계하고,
카카오는 hermes 미지원이라 앱이 REST API 를 직접 호출한다(app.kakao_notify).
브라우저(/api/notify)와 서버 감시(alert_watch) 양쪽이 공유한다. 예외는 올리지 않는다.
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_HERMES = "/usr/local/bin/hermes"


def notify_all(subject: str, msg: str) -> dict:
    """텔레그램+카카오로 동시 발송. {telegram: bool, kakao: bool} 반환(절대 raise 안 함)."""
    out: dict = {}
    # 1) 텔레그램 (Hermes 중계)
    try:
        subprocess.run(
            [_HERMES, "send", "--to", "telegram", "--subject", subject, msg],
            env={**os.environ, "HERMES_HOME": os.environ.get("HERMES_HOME", "/root/.hermes")},
            timeout=30, capture_output=True,
        )
        out["telegram"] = True
    except Exception as exc:
        out["telegram"] = False
        out["telegram_error"] = str(exc)
        logger.warning("텔레그램(Hermes) 발송 실패: %s", exc)
    # 2) 카카오 '나에게 보내기' (연동돼 있을 때만)
    try:
        from app.kakao_notify import linked, send as kakao_send
        out["kakao"] = bool(linked() and kakao_send(f"{subject}\n{msg}"))
    except Exception:
        out["kakao"] = False
    return out
