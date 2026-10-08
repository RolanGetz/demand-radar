"""Model providers for structured screening.

The interface is intentionally one method wide: a (system, prompt, schema)
triple in, raw JSON text out. Validation lives in
:mod:`demand_radar.intelligence.screening_schema`, so a provider stays a thin
transport and adding another one is a small class.

Gemini is the only configured model today. :class:`NullProvider` is the default,
so the pipeline runs — and is testable — with no credentials at all.
"""

from __future__ import annotations

import json

import httpx

from demand_radar.config import Config

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_TIMEOUT = 60.0


class ScreeningProvider:
    name = "base"
    available = False
    model: str | None = None

    def complete_json(self, prompt: str, *, system: str, schema: dict) -> str:
        raise NotImplementedError


class NullProvider(ScreeningProvider):
    """The default: no network, no key. Callers fall back to the cheap verdict."""

    name = "none"
    available = False

    def complete_json(self, prompt: str, *, system: str, schema: dict) -> str:
        raise RuntimeError(
            "No screening model configured. Set GEMINI_API_KEY to enable structured "
            "classification; the local cheap filter is used until then."
        )


class GeminiProvider(ScreeningProvider):
    """Google Gemini via the Generative Language REST API.

    Uses the API's native structured-output mode (``responseSchema``), so the
    model is constrained to the decision model rather than asked politely to
    follow it.
    """

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-pro", **options):
        self._api_key = api_key
        self.model = model
        self.options = options

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete_json(self, prompt: str, *, system: str, schema: dict) -> str:
        if not self._api_key:
            raise RuntimeError("GeminiProvider requires GEMINI_API_KEY")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(schema),
                # Classification must be reproducible: the same signal should
                # not land in two different buckets across runs.
                "temperature": 0.0,
            },
        }
        url = _GEMINI_ENDPOINT.format(model=self.model)
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.post(
                url, json=body, headers={"x-goog-api-key": self._api_key}
            )
            response.raise_for_status()
            payload = response.json()
        return _extract_text(payload)


def get_provider(config: Config) -> ScreeningProvider:
    """Pick a provider from configuration. Falls back to the null provider."""
    if config.gemini_api_key:
        return GeminiProvider(api_key=config.gemini_api_key, model=config.gemini_model)
    return NullProvider()


def _extract_text(payload: dict) -> str:
    """Pull the text out of a Gemini response, with an explicit failure path."""
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback", {})
        blocked = feedback.get("blockReason")
        raise RuntimeError(f"Gemini returned no candidates (blockReason={blocked})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        finish_reason = candidates[0].get("finishReason")
        raise RuntimeError(f"Gemini returned an empty response (finishReason={finish_reason})")
    return text


def _to_gemini_schema(schema: dict) -> dict:
    """Translate our JSON Schema into the subset Gemini's responseSchema accepts.

    Gemini rejects ``additionalProperties`` and does not accept a union type
    list (``["string", "null"]``); nullability is expressed with a ``nullable``
    flag instead. Enum members must also be plain strings, so a ``None`` member
    becomes the nullable flag rather than an enum value.
    """
    converted: dict = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, list):
            non_null = [item for item in value if item != "null"]
            converted["type"] = (non_null or ["string"])[0].upper()
            if "null" in value:
                converted["nullable"] = True
            continue
        if key == "type" and isinstance(value, str):
            converted["type"] = value.upper()
            continue
        if key == "enum" and isinstance(value, list):
            members = [item for item in value if isinstance(item, str)]
            converted["enum"] = members
            if None in value:
                converted["nullable"] = True
            continue
        if key == "properties" and isinstance(value, dict):
            converted["properties"] = {
                name: _to_gemini_schema(sub) for name, sub in value.items()
            }
            continue
        if key == "items" and isinstance(value, dict):
            converted["items"] = _to_gemini_schema(value)
            continue
        converted[key] = value
    return converted


def schema_as_text(schema: dict) -> str:
    """Render the schema for providers that take it as prompt text."""
    return json.dumps(schema, ensure_ascii=False, indent=2)
