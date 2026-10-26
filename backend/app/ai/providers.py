from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from app.security import decrypt_secret


class ProviderError(Exception):
    pass


@dataclass
class LLMResult:
    parsed: BaseModel
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    latency_ms: int


def parse_json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Model reply was not a JSON object")
    return value


def _usage_openai(payload: dict) -> tuple[int, int, int]:
    usage = payload.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), int(details.get("cached_tokens") or 0)


def _post(url: str, *, headers: dict, body: dict, params: dict | None = None, timeout: float = 45) -> dict:
    try:
        response = httpx.post(url, headers=headers, json=body, params=params, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError("The model provider could not be reached") from exc
    if response.status_code >= 400:
        raise ProviderError(f"The model provider returned {response.status_code}")
    return response.json()


def image_list(image) -> list[tuple[str, str]]:
    if not image:
        return []
    if isinstance(image, tuple) and len(image) == 2 and isinstance(image[0], str):
        return [image]
    return list(image)


def openai_user_content(user_prompt: str, image):
    images = image_list(image)
    if not images:
        return user_prompt
    content = [{"type": "text", "text": user_prompt}]
    for media_type, data in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
    return content


def _openai_text(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    image: tuple[str, str] | None = None,
) -> tuple[str, int, int, int]:
    payload = _post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        body={
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": openai_user_content(user_prompt, image)},
            ],
        },
        timeout=90 if image_list(image) else 45,
    )
    prompt, completion, cached = _usage_openai(payload)
    text = payload["choices"][0]["message"].get("content") or ""
    return text, prompt, completion, cached


def _anthropic_text(
    api_key: str,
    model: str,
    system: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    image: tuple[str, str] | None = None,
) -> tuple[str, int, int, int]:
    images = image_list(image)
    content: str | list = user_prompt
    if images:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
            for media_type, data in images
        ]
        content.append({"type": "text", "text": user_prompt})
    payload = _post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        body={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system + "\nReply with one JSON object only.",
            "messages": [{"role": "user", "content": content}],
        },
        timeout=90 if images else 45,
    )
    usage = payload.get("usage") or {}
    text = payload["content"][0]["text"]
    return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), 0


def _gemini_text(
    api_key: str,
    model: str,
    system: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    image: tuple[str, str] | None = None,
) -> tuple[str, int, int, int]:
    name = model.removeprefix("models/")
    images = image_list(image)
    parts: list = [{"text": user_prompt}]
    for media_type, data in images:
        parts.append({"inline_data": {"mime_type": media_type, "data": data}})
    payload = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent",
        headers={},
        params={"key": api_key},
        body={
            "system_instruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
            "contents": [{"role": "user", "parts": parts}],
        },
        timeout=90 if images else 45,
    )
    usage = payload.get("usageMetadata") or {}
    text = payload["candidates"][0]["content"]["parts"][0]["text"]
    return text, int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0), 0


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item if isinstance(item, str) else str(item) for item in value]
    return [str(value)]


def _as_score(value) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, min(5, int(value)))
    if isinstance(value, str):
        digits = [part for part in "".join(ch if ch.isdigit() else " " for ch in value).split() if part]
        if digits:
            return max(0, min(5, int(digits[0])))
    return 0


def normalize_model_json(schema, data: dict) -> dict:
    """Accept the JSON shapes models commonly return for an evaluation."""
    if schema.__name__ not in {"EvalAndNext", "EvalAndSummary", "Evaluation"}:
        return data
    eval_keys = {
        "score",
        "correct_claims",
        "incomplete_claims",
        "incorrect_claims",
        "math_errors",
        "misconception",
        "correction",
        "confidence",
    }
    if not isinstance(data.get("evaluation"), dict) and eval_keys & set(data):
        data = {**data, "evaluation": {key: data[key] for key in eval_keys if key in data}}
    evaluation = data.get("evaluation")
    if isinstance(evaluation, dict):
        for key in ("correct_claims", "incomplete_claims", "incorrect_claims", "math_errors"):
            if key in evaluation:
                evaluation[key] = _as_str_list(evaluation[key])
        if "score" in evaluation:
            evaluation["score"] = _as_score(evaluation["score"])
        misconception = evaluation.get("misconception")
        if misconception in (0, False, "", "0", "none", "None", "null"):
            evaluation["misconception"] = None
        elif isinstance(misconception, list):
            evaluation["misconception"] = "; ".join(str(item) for item in misconception) or None
        confidence = evaluation.get("confidence")
        if isinstance(confidence, str):
            evaluation["confidence"] = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(confidence.strip().lower(), 0.5)
    nxt = data.get("next_question")
    if isinstance(nxt, str):
        data["next_question"] = {"question": nxt}
    elif isinstance(nxt, dict) and isinstance(nxt.get("rubric"), list):
        nxt["rubric"] = {"key_points": _as_str_list(nxt["rubric"])}
    if schema.__name__ == "Evaluation":
        return data.get("evaluation") if isinstance(data.get("evaluation"), dict) else data
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("strengths", "weak_subtopics", "misconceptions"):
            if key in summary:
                summary[key] = _as_str_list(summary[key])
    return data


def http_transport(
    *,
    credential,
    model: str,
    system: str,
    user_prompt: str,
    schema,
    temperature: float,
    max_tokens: int,
    image: tuple[str, str] | None = None,
) -> LLMResult:
    api_key = decrypt_secret(credential.api_key_enc)
    prompt = user_prompt
    last_error: Exception | None = None
    totals = [0, 0, 0]
    for _ in range(2):
        text, prompt_tokens, completion_tokens, cached = _dispatch(
            credential, api_key, model, system, prompt, temperature, max_tokens, image
        )
        totals[0] += prompt_tokens
        totals[1] += completion_tokens
        totals[2] += cached
        try:
            parsed = schema.model_validate(normalize_model_json(schema, parse_json_object(text)))
            return LLMResult(parsed, totals[0], totals[1], totals[2], 0)
        except (ValidationError, ValueError, json.JSONDecodeError, TypeError) as exc:
            last_error = exc
            prompt = (
                user_prompt
                + "\n\nYour previous reply could not be used. Reply with one complete JSON object only, "
                + "with an evaluation object and, when requested, a next_question object. Do not stop halfway."
            )
    raise ProviderError("The model reply could not be read") from last_error


def _dispatch(
    credential,
    api_key: str,
    model: str,
    system: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    image: tuple[str, str] | None = None,
):
    provider = credential.provider
    if provider == "openai":
        return _openai_text("https://api.openai.com/v1", api_key, model, system, user_prompt, temperature, max_tokens, image)
    if provider == "openai_compatible":
        return _openai_text(credential.base_url, api_key, model, system, user_prompt, temperature, max_tokens, image)
    if provider == "anthropic":
        return _anthropic_text(api_key, model, system, user_prompt, temperature, max_tokens, image)
    if provider == "gemini":
        return _gemini_text(api_key, model, system, user_prompt, temperature, max_tokens, image)
    raise ProviderError("Unsupported model provider")
