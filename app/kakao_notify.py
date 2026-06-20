"""카카오톡 '나에게 보내기'(메모 API) — 무료, 본인 카톡으로 알림.

최초 1회 카카오 로그인(OAuth)으로 refresh_token 확보 → 이후 access_token(약 6시간)을
refresh_token으로 무인 자동 갱신. 토큰은 data/kakao_token.json 에 저장(회전 반영).
hermes 미지원이라 앱이 카카오 REST API 를 직접 호출한다. 예외는 올리지 않는다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from app.config import settings

_AUTH = "https://kauth.kakao.com"
_API = "https://kapi.kakao.com"
_TOKEN_FILE = Path(settings.db_path).resolve().parent / "kakao_token.json"


def _rest_key() -> str:
    return os.environ.get("KAKAO_REST_API_KEY") or settings.kakao_rest_api_key


def _redirect_uri() -> str:
    return os.environ.get("KAKAO_REDIRECT_URI") or settings.kakao_redirect_uri


def configured() -> bool:
    """REST API 키가 설정돼 있나(연동 가능 상태)."""
    return bool(_rest_key())


def authorize_url() -> str:
    """카카오 로그인 동의 URL(여기로 보내 사용자가 1회 동의)."""
    params = {
        "client_id": _rest_key(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "talk_message",
    }
    return f"{_AUTH}/oauth/authorize?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _load() -> dict:
    try:
        if _TOKEN_FILE.exists():
            return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save(d: dict) -> None:
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def linked() -> bool:
    """refresh_token 확보(연동 완료) 상태인가."""
    return bool(_load().get("refresh_token"))


def exchange_code(code: str) -> dict:
    """callback 의 code → 토큰 교환·저장. {ok, error?}."""
    try:
        tok = _post_form(f"{_AUTH}/oauth/token", {
            "grant_type": "authorization_code",
            "client_id": _rest_key(),
            "redirect_uri": _redirect_uri(),
            "code": code,
        })
        if not tok.get("refresh_token"):
            return {"ok": False, "error": tok}
        _save({
            "access_token": tok.get("access_token"),
            "refresh_token": tok["refresh_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 21600)) - 60,
        })
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _valid_access() -> str | None:
    """유효한 access_token 반환. 만료면 refresh_token 으로 자동 갱신."""
    d = _load()
    if not d.get("refresh_token"):
        return None
    if d.get("access_token") and time.time() < float(d.get("expires_at", 0)):
        return d["access_token"]
    try:
        tok = _post_form(f"{_AUTH}/oauth/token", {
            "grant_type": "refresh_token",
            "client_id": _rest_key(),
            "refresh_token": d["refresh_token"],
        })
        if not tok.get("access_token"):
            return None
        d["access_token"] = tok["access_token"]
        d["expires_at"] = time.time() + int(tok.get("expires_in", 21600)) - 60
        if tok.get("refresh_token"):  # 만료 임박 시 카카오가 새 refresh_token 발급 → 회전 저장
            d["refresh_token"] = tok["refresh_token"]
        _save(d)
        return d["access_token"]
    except Exception:
        return None


def send(text: str, link_url: str | None = None) -> bool:
    """나에게 보내기. 성공 True. 미연동/실패 시 False(예외 안 냄)."""
    at = _valid_access()
    if not at:
        return False
    template = {
        "object_type": "text",
        "text": text[:1000],
        "link": {"web_url": link_url or "https://jessystock.com", "mobile_web_url": link_url or "https://jessystock.com"},
    }
    try:
        body = urllib.parse.urlencode({"template_object": json.dumps(template, ensure_ascii=False)}).encode()
        req = urllib.request.Request(
            f"{_API}/v2/api/talk/memo/default/send",
            data=body,
            headers={
                "Authorization": f"Bearer {at}",
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False
