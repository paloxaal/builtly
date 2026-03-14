from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Claude",
    "gemini": "Gemini",
}

DEFAULT_PROVIDER_ORDERS = {
    "default": ["openai", "anthropic", "gemini"],
    "assistant": ["openai", "anthropic", "gemini"],
    "structured_review": ["openai", "anthropic", "gemini"],
    "document_engine": ["openai", "anthropic", "gemini"],
    "long_context": ["anthropic", "openai", "gemini"],
    "fast_draft": ["openai", "gemini", "anthropic"],
}


@dataclass
class GenerationResult:
    ok: bool
    text: str = ""
    provider: Optional[str] = None
    model: Optional[str] = None
    attempt_log: Optional[List[Dict[str, str]]] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "attempt_log": self.attempt_log or [],
            "error": self.error,
        }


def _clean_text(text: str) -> str:
    text = text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def openai_api_key() -> Optional[str]:
    return os.getenv("OPENAI_API_KEY")


def anthropic_api_key() -> Optional[str]:
    return os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")


def gemini_api_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def provider_ready(provider: str) -> bool:
    if provider == "openai":
        return bool(openai_api_key())
    if provider == "anthropic":
        return bool(anthropic_api_key())
    if provider == "gemini":
        return bool(gemini_api_key())
    return False


def ai_service_ready() -> bool:
    return any(provider_ready(provider) for provider in PROVIDER_LABELS)


def available_providers() -> List[str]:
    return [provider for provider in PROVIDER_LABELS if provider_ready(provider)]


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def provider_labels(providers: Sequence[str]) -> List[str]:
    return [provider_label(provider) for provider in providers]


def model_for(provider: str) -> str:
    if provider == "openai":
        return (
            os.getenv("BUILTLY_OPENAI_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4o-mini"
        )
    if provider == "anthropic":
        return (
            os.getenv("BUILTLY_CLAUDE_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or "claude-3-5-sonnet-latest"
        )
    if provider == "gemini":
        return (
            os.getenv("BUILTLY_GEMINI_MODEL")
            or os.getenv("GEMINI_MODEL")
            or "gemini-2.5-flash"
        )
    raise ValueError(f"Unknown provider: {provider}")


def _normalized_provider_list(providers: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for provider in providers:
        cleaned = (provider or "").strip().lower()
        if cleaned in PROVIDER_LABELS and cleaned not in seen:
            seen.append(cleaned)
    return seen


def provider_order_for_task(task: str = "default", estimated_context_chars: int = 0) -> List[str]:
    env_override = os.getenv("BUILTLY_PROVIDER_ORDER", "").strip()
    if env_override:
        order = _normalized_provider_list(env_override.split(","))
    else:
        order = list(DEFAULT_PROVIDER_ORDERS.get(task, DEFAULT_PROVIDER_ORDERS["default"]))

    if estimated_context_chars >= 14000:
        long_context_pref = ["anthropic", "openai", "gemini"]
        ordered = [provider for provider in long_context_pref if provider in order]
        ordered.extend(provider for provider in order if provider not in ordered)
        order = ordered

    ready_order = [provider for provider in order if provider_ready(provider)]
    if ready_order:
        return ready_order
    return [provider for provider in order if provider in PROVIDER_LABELS]


def _http_post_json(url: str, payload: Dict, headers: Dict[str, str], timeout: int = 80) -> Dict:
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {details}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc


def _parse_openai_responses_payload(data: Dict) -> str:
    output_parts: List[str] = []

    if isinstance(data.get("output_text"), str) and data.get("output_text").strip():
        return _clean_text(data["output_text"])

    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    output_parts.append(content["text"])

    if not output_parts:
        for choice in data.get("choices", []) or []:
            message = choice.get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                output_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("text"):
                        output_parts.append(part["text"])

    if not output_parts:
        raise RuntimeError("No text returned from OpenAI.")
    return _clean_text("\n".join(output_parts))


def _call_openai(system_prompt: str, user_prompt: str, max_output_tokens: int, temperature: float) -> tuple[str, str]:
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY mangler")
    model_name = model_for("openai")

    responses_payload = {
        "model": model_name,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }

    try:
        data = _http_post_json(
            "https://api.openai.com/v1/responses",
            responses_payload,
            {"Authorization": f"Bearer {api_key}"},
        )
        return _parse_openai_responses_payload(data), model_name
    except Exception:
        chat_payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        data = _http_post_json(
            "https://api.openai.com/v1/chat/completions",
            chat_payload,
            {"Authorization": f"Bearer {api_key}"},
        )
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            content = "\n".join(part for part in text_parts if part)
        content = _clean_text(str(content))
        if not content:
            raise RuntimeError("No text returned from OpenAI.")
        return content, model_name


def _call_anthropic(system_prompt: str, user_prompt: str, max_output_tokens: int, temperature: float) -> tuple[str, str]:
    api_key = anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY mangler")
    model_name = model_for("anthropic")

    payload = {
        "model": model_name,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "max_tokens": max_output_tokens,
        "temperature": temperature,
    }
    data = _http_post_json(
        "https://api.anthropic.com/v1/messages",
        payload,
        {
            "x-api-key": api_key,
            "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
        },
    )
    text_parts = [
        item.get("text", "")
        for item in data.get("content", []) or []
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    text = _clean_text("\n".join(part for part in text_parts if part))
    if not text:
        raise RuntimeError("No text returned from Claude.")
    return text, model_name


def _parse_gemini_payload(data: Dict) -> str:
    text_parts: List[str] = []
    for candidate in data.get("candidates", []) or []:
        content = candidate.get("content", {})
        for part in content.get("parts", []) or []:
            if isinstance(part, dict) and part.get("text"):
                text_parts.append(part["text"])

    if not text_parts:
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            raise RuntimeError(f"Response blocked by Gemini: {block_reason}")
        raise RuntimeError("No text returned from Gemini.")
    return _clean_text("\n".join(text_parts))


def _call_gemini(system_prompt: str, user_prompt: str, max_output_tokens: int, temperature: float) -> tuple[str, str]:
    api_key = gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY/GOOGLE_API_KEY mangler")
    model_name = model_for("gemini")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": system_prompt},
                    {"text": user_prompt},
                ],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "topP": 0.9,
            "maxOutputTokens": max_output_tokens,
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urlparse.quote(model_name, safe='')}:generateContent?key={urlparse.quote(api_key, safe='')}"
    )
    data = _http_post_json(url, payload, {})
    return _parse_gemini_payload(data), model_name


def _call_provider(provider: str, system_prompt: str, user_prompt: str, max_output_tokens: int, temperature: float) -> tuple[str, str]:
    if provider == "openai":
        return _call_openai(system_prompt, user_prompt, max_output_tokens, temperature)
    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, max_output_tokens, temperature)
    if provider == "gemini":
        return _call_gemini(system_prompt, user_prompt, max_output_tokens, temperature)
    raise RuntimeError(f"Unknown provider: {provider}")


def generate_text_with_fallback(
    *,
    system_prompt: str,
    user_prompt: str,
    task: str = "default",
    preferred_providers: Optional[Sequence[str]] = None,
    estimated_context_chars: int = 0,
    max_output_tokens: int = 1600,
    temperature: float = 0.2,
) -> Dict:
    providers = (
        _normalized_provider_list(preferred_providers)
        if preferred_providers
        else provider_order_for_task(task, estimated_context_chars)
    )
    providers = [provider for provider in providers if provider_ready(provider)]
    attempts: List[Dict[str, str]] = []

    if not providers:
        return GenerationResult(
            ok=False,
            error="Ingen AI-leverandører er konfigurert. Sett OPENAI_API_KEY, ANTHROPIC_API_KEY eller GEMINI_API_KEY.",
            attempt_log=[],
        ).as_dict()

    for provider in providers:
        try:
            text, model_name = _call_provider(provider, system_prompt, user_prompt, max_output_tokens, temperature)
            if not text:
                raise RuntimeError("Tom respons")
            attempts.append({
                "provider": provider,
                "label": provider_label(provider),
                "model": model_name,
                "status": "ok",
                "error": "",
            })
            return GenerationResult(
                ok=True,
                text=text,
                provider=provider,
                model=model_name,
                attempt_log=attempts,
            ).as_dict()
        except Exception as exc:
            attempts.append({
                "provider": provider,
                "label": provider_label(provider),
                "model": model_for(provider),
                "status": "error",
                "error": str(exc),
            })

    return GenerationResult(
        ok=False,
        error=attempts[-1]["error"] if attempts else "Ukjent AI-feil",
        attempt_log=attempts,
    ).as_dict()


def extract_json_payload(text: str) -> Dict | List | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    fenced_match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        cleaned = fenced_match.group(1).strip()

    for candidate in [cleaned]:
        try:
            return json.loads(candidate)
        except Exception:
            pass

    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = cleaned[brace_start : brace_end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    bracket_start = cleaned.find("[")
    bracket_end = cleaned.rfind("]")
    if bracket_start >= 0 and bracket_end > bracket_start:
        candidate = cleaned[bracket_start : bracket_end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    return None


def generate_json_with_fallback(
    *,
    system_prompt: str,
    user_prompt: str,
    schema_hint: Dict,
    task: str = "structured_review",
    preferred_providers: Optional[Sequence[str]] = None,
    estimated_context_chars: int = 0,
    max_output_tokens: int = 1800,
    temperature: float = 0.15,
) -> Dict:
    schema_text = json.dumps(schema_hint, indent=2, ensure_ascii=False)
    json_system = (
        f"{system_prompt.strip()}\n\n"
        "Return only valid JSON and no surrounding commentary. "
        "Use exactly these top-level keys and keep the structure compact and practical.\n"
        f"Schema hint:\n{schema_text}"
    )
    result = generate_text_with_fallback(
        system_prompt=json_system,
        user_prompt=user_prompt,
        task=task,
        preferred_providers=preferred_providers,
        estimated_context_chars=estimated_context_chars,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    if not result.get("ok"):
        return result

    data = extract_json_payload(result.get("text", ""))
    if data is not None:
        result["data"] = data
        return result

    repair_prompt = (
        "Convert the following into valid JSON only. "
        "Do not add commentary. Preserve the meaning and map it into this schema.\n\n"
        f"Schema:\n{schema_text}\n\n"
        f"Content to repair:\n{result.get('text', '')}"
    )
    repair_order: List[str] = []
    if result.get("provider"):
        repair_order.append(result["provider"])
    for provider in provider_order_for_task(task, estimated_context_chars):
        if provider not in repair_order:
            repair_order.append(provider)

    repaired = generate_text_with_fallback(
        system_prompt="You repair malformed structured outputs.",
        user_prompt=repair_prompt,
        task=task,
        preferred_providers=repair_order,
        estimated_context_chars=min(estimated_context_chars + len(result.get("text", "")), 24000),
        max_output_tokens=max_output_tokens,
        temperature=0.0,
    )
    if repaired.get("ok"):
        repaired_data = extract_json_payload(repaired.get("text", ""))
        if repaired_data is not None:
            repaired["data"] = repaired_data
            return repaired

    result["ok"] = False
    result["error"] = "AI svarte, men ikke som gyldig JSON."
    return result
