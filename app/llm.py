"""사설/로컬 OpenAI 호환 LLM 어댑터.

이전엔 Anthropic Claude(messages.create + output_config json_schema)를 직접 호출했다.
이제 .env 의 LLM_* 설정으로 OpenAI 호환 엔드포인트(/v1/chat/completions)를 호출한다.
- chat_json: 스키마를 시스템 프롬프트에 명시 + response_format=json_object 로 JSON 응답 → dict.
- chat_text: 평문 응답을 문자열로 반환.
- 토큰 사용량은 응답 usage(prompt_tokens/completion_tokens)를 token_usage 에 누적.
- 실패(미설정·HTTP·파싱)는 예외로 올린다. 호출부가 기존처럼 try/except 로 흡수한다.

requests 만 사용(이미 의존성). 새 SDK 불필요.
"""
from __future__ import annotations

import json
import logging
import os

import requests

from app.config import settings

logger = logging.getLogger(__name__)


def _provider() -> str:
    """앱 AI 백엔드 선택: 'anthropic'(Claude) | 'openai'(로컬 OpenAI호환). 기본 openai.
    .env 의 LLM_PROVIDER 로 전환(앱만 해당 · Hermes 봇은 별개)."""
    return (settings.llm_provider or "openai").strip().lower()


def configured() -> bool:
    """선택된 백엔드가 사용 가능하게 설정됐는지."""
    if _provider() == "anthropic":
        return bool(settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))
    return bool(settings.llm_endpoint and settings.llm_model)


def _chat_url() -> str:
    """OpenAI 호환 chat 엔드포인트 URL. 엔드포인트에 /v1 이 없으면 붙인다."""
    base = settings.llm_endpoint.rstrip("/")
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _extract_json(text: str) -> dict:
    """모델이 코드펜스/잡설을 붙여도 JSON 오브젝트를 뽑아 파싱한다."""
    t = (text or "").strip()
    if t.startswith("```"):
        # ```json ... ``` 펜스 제거
        inner = t.split("```")
        t = inner[1] if len(inner) >= 3 else t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    try:
        return json.loads(t)
    except Exception:
        # 본문 중 첫 { ~ 마지막 } 구간만 시도
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


def _record(usage: dict | None, source: str) -> None:
    try:
        from app.analysis.token_usage import record_tokens
        if usage:
            record_tokens(int(usage.get("prompt_tokens", 0) or 0),
                          int(usage.get("completion_tokens", 0) or 0), source)
    except Exception:
        pass


def _post(messages: list[dict], max_tokens: int, source: str,
          response_format: dict | None = None) -> str:
    """공통 호출. 응답 message.content(문자열) 반환. 실패 시 예외를 올린다."""
    if not configured():
        raise RuntimeError("LLM 미설정(.env LLM_ENDPOINT/LLM_MODEL)")
    body: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if response_format:
        body["response_format"] = response_format
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    timeout = max(5, settings.llm_timeout_ms / 1000)

    last_exc: Exception | None = None
    for attempt in range(max(1, settings.llm_max_retries + 1)):
        try:
            resp = requests.post(_chat_url(), json=body, headers=headers, timeout=timeout)
            # response_format 미지원 서버(400) 대비 1회 폴백.
            if resp.status_code == 400 and "response_format" in body:
                body.pop("response_format", None)
                resp = requests.post(_chat_url(), json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            j = resp.json()
            _record(j.get("usage"), source)
            return j["choices"][0]["message"]["content"] or ""
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("LLM 호출 실패(%s, 시도 %d): %s", source, attempt + 1, exc)
    raise last_exc or RuntimeError("LLM 호출 실패")


# ---- Anthropic(Claude) 백엔드 — LLM_PROVIDER=anthropic 일 때 사용 ----
def _anthropic_client():
    import anthropic
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key)


def _anthropic_usage(resp, source: str) -> None:
    try:
        from app.analysis.token_usage import record_tokens
        u = getattr(resp, "usage", None)
        record_tokens(int(getattr(u, "input_tokens", 0) or 0),
                      int(getattr(u, "output_tokens", 0) or 0), source)
    except Exception:
        pass


def _anthropic_json(system: str, prompt: str, schema: dict, max_tokens: int, source: str) -> dict:
    resp = _anthropic_client().messages.create(
        model=settings.deudeumi_model, max_tokens=max_tokens, system=system,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    _anthropic_usage(resp, source)
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return _extract_json(text)


def _anthropic_text(system: str, prompt: str, max_tokens: int, source: str) -> str:
    resp = _anthropic_client().messages.create(
        model=settings.deudeumi_model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    _anthropic_usage(resp, source)
    return next((b.text for b in resp.content if b.type == "text"), "").strip()


def chat_json(system: str, prompt: str, schema: dict, max_tokens: int, source: str) -> dict:
    """스키마 기반 JSON 응답을 dict 로 반환. 실패 시 예외."""
    if _provider() == "anthropic":
        return _anthropic_json(system, prompt, schema, max_tokens, source)
    sys_full = (system + "\n\n반드시 아래 JSON 스키마에 맞는 JSON 객체만 출력하라"
                "(설명·마크다운·코드펜스 금지):\n" + json.dumps(schema, ensure_ascii=False))
    content = _post(
        [{"role": "system", "content": sys_full},
         {"role": "user", "content": prompt}],
        max_tokens, source, response_format={"type": "json_object"})
    return _extract_json(content)


def chat_text(system: str, prompt: str, max_tokens: int, source: str) -> str:
    """평문 응답을 문자열로 반환. 실패 시 예외."""
    if _provider() == "anthropic":
        return _anthropic_text(system, prompt, max_tokens, source)
    content = _post(
        [{"role": "system", "content": system},
         {"role": "user", "content": prompt}],
        max_tokens, source)
    return (content or "").strip()
