"""구글 로그인(OAuth 2.0) — 사용자별 매매일지 인증용.

카카오 연동(kakao_notify.py)과 같은 무의존(urllib) 방식. authorization code 흐름:
  1) authorize_url() 로 구글 동의 페이지 이동
  2) 콜백의 code 를 exchange_code() 로 토큰 교환
  3) userinfo 엔드포인트로 sub/email/name/picture 획득

키(GOOGLE_CLIENT_ID/SECRET)는 .env 에만 둔다(공개 레포라 커밋 금지).
예외는 올리지 않고 {ok:False, error} 로 돌려준다(상위 500 금지).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from app.config import settings

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"
_USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"


def _client_id() -> str:
    return settings.google_client_id


def _client_secret() -> str:
    return settings.google_client_secret


def _redirect_uri() -> str:
    return settings.google_redirect_uri


def configured() -> bool:
    """구글 로그인 키가 설정돼 있나(로그인 가능 상태)."""
    return bool(_client_id() and _client_secret())


def authorize_url(state: str) -> str:
    """구글 로그인 동의 URL. state 는 CSRF 방지용 랜덤값(콜백에서 대조)."""
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{_AUTH}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code: str) -> dict:
    """콜백 code → 토큰 교환 → 사용자 정보.

    성공: {ok:True, sub, email, name, picture}. 실패: {ok:False, error}.
    """
    try:
        tok = _post_form(_TOKEN, {
            "grant_type": "authorization_code",
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _redirect_uri(),
            "code": code,
        })
        access = tok.get("access_token")
        if not access:
            return {"ok": False, "error": tok}
        info = _get_json(_USERINFO, access)
        sub = info.get("sub")
        if not sub:
            return {"ok": False, "error": "userinfo 에 sub 없음"}
        return {
            "ok": True,
            "sub": sub,
            "email": info.get("email", ""),
            "name": info.get("name") or info.get("email", "") or "사용자",
            "picture": info.get("picture", ""),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
