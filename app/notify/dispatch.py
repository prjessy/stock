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


def alert_recipients() -> list[dict]:
    """ALERT_TELEGRAM_IDS 를 [{id, name}] 로 파싱. 각 항목은 'id' 또는 'id:이름'.

    이름은 표시·헷갈림 방지용(발송엔 id만 사용). 예: '536439890:jessy,393257633:블롱레'.
    """
    from app.config import settings
    out: list[dict] = []
    for x in settings.alert_telegram_ids:
        i, _, n = x.partition(":")
        i = i.strip()
        if i:
            out.append({"id": i, "name": n.strip()})
    return out


def notify_all(subject: str, msg: str, shared: bool = False) -> dict:
    """텔레그램+카카오로 동시 발송. {telegram: bool, kakao: bool} 반환(절대 raise 안 함).

    shared=True(시황성 알람)면 ALERT_TELEGRAM_IDS(.env) 의 chat id 전체로 팬아웃 발송.
    shared=False(기본, 개인정보성: 보유·주간복기·자동매매 체결·목표가)면 홈채널(본인)만.
    일부 수신자 실패해도 나머지는 계속 보낸다.
    """
    from app.config import settings
    out: dict = {}
    env = {**os.environ, "HERMES_HOME": os.environ.get("HERMES_HOME", "/root/.hermes")}
    # 1) 텔레그램 (Hermes 중계) — 시황 알람만 지인 목록으로, 개인정보성은 홈채널(본인)만.
    recips = alert_recipients()
    targets = [f"telegram:{r['id']}" for r in recips] if (shared and recips) else ["telegram"]
    ok_any, errs = False, []
    for tgt in targets:
        try:
            r = subprocess.run(
                [_HERMES, "send", "--to", tgt, "--subject", subject, msg, "-q"],
                env=env, timeout=30, capture_output=True,
            )
            if r.returncode == 0:
                ok_any = True
            else:
                errs.append(f"{tgt}: rc={r.returncode} {r.stderr.decode('utf-8', 'ignore')[:80]}")
        except Exception as exc:
            errs.append(f"{tgt}: {exc}")
    out["telegram"] = ok_any
    if errs:
        out["telegram_error"] = "; ".join(errs)
        logger.warning("텔레그램(Hermes) 일부/전체 발송 실패: %s", "; ".join(errs))
    # 2) 카카오 '나에게 보내기' (연동돼 있을 때만)
    try:
        from app.kakao_notify import linked, send as kakao_send
        out["kakao"] = bool(linked() and kakao_send(f"{subject}\n{msg}"))
    except Exception:
        out["kakao"] = False
    return out
