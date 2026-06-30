"""카카오 로그인(OAuth) — 사용자 인증용(구글 로그인과 병행).

알림용 kakao_notify.py 와 같은 REST 키를 재사용하되, 콜백은 분리한다:
  - 알림('나에게 보내기'): /api/kakao/callback (scope=talk_message)
  - 로그인(이 파일):       /api/auth/kakao/callback (scope=profile_nickname …)

⚠️ 카카오 개발자콘솔에서 ① '카카오 로그인' 활성화 ② 위 로그인 콜백 redirect URI 등록
   ③ 동의항목 profile_nickname(필수) 설정이 되어 있어야 동작한다.
사용자 식별자는 users.google_sub 칼럼에 'kakao:<id>' 형태로 저장(스키마 재사용).
예외는 올리지 않고 {ok:False, error} 로 돌려준다.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from app.config import settings

_AUTH = "https://kauth.kakao.com"
_API = "https://kapi.kakao.com"


def _rest_key() -> str:
    return os.environ.get("KAKAO_REST_API_KEY") or settings.kakao_rest_api_key


def _redirect_uri() -> str:
    """로그인 전용 콜백 URI. KAKAO_LOGIN_REDIRECT_URI 우선, 없으면 도메인+/api/auth/kakao/callback."""
    env = os.environ.get("KAKAO_LOGIN_REDIRECT_URI")
    if env:
        return env
    base = (settings.kakao_redirect_uri or "").rsplit("/api/kakao/callback", 1)[0]
    return (base or "https://jessystock.com") + "/api/auth/kakao/callback"


def configured() -> bool:
    """카카오 REST 키가 설정돼 있나(로그인 가능 상태)."""
    return bool(_rest_key())


def authorize_url(state: str) -> str:
    """카카오 로그인 동의 URL. state 는 CSRF 방지용."""
    params = {
        "client_id": _rest_key(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "state": state,
        "scope": "profile_nickname",
    }
    return f"{_AUTH}/oauth/authorize?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code: str) -> dict:
    """콜백 code → 토큰 교환 → 사용자 정보. 성공:{ok,sub,email,name,picture} / 실패:{ok:False,error}."""
    try:
        tok = _post_form(f"{_AUTH}/oauth/token", {
            "grant_type": "authorization_code",
            "client_id": _rest_key(),
            "redirect_uri": _redirect_uri(),
            "code": code,
        })
        access = tok.get("access_token")
        if not access:
            return {"ok": False, "error": tok}
        me = _get_json(f"{_API}/v2/user/me", access)
        kid = me.get("id")
        if not kid:
            return {"ok": False, "error": "user/me 에 id 없음"}
        acc = me.get("kakao_account") or {}
        prof = acc.get("profile") or {}
        return {
            "ok": True,
            "sub": f"kakao:{kid}",
            "email": acc.get("email", "") or "",
            "name": prof.get("nickname") or f"카카오{kid}",
            "picture": prof.get("profile_image_url", "") or "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
