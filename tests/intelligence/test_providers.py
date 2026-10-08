"""Provider transport: Gemini request shape, schema translation, and selection."""

import httpx
import pytest
import respx

from demand_radar.config import Config
from demand_radar.intelligence.providers import (
    GeminiProvider,
    NullProvider,
    _to_gemini_schema,
    get_provider,
)
from demand_radar.intelligence.screening_schema import SCREENING_SCHEMA

MODEL = "gemini-2.5-pro"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def _response(text: str):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_null_provider_is_unavailable_and_explains_itself():
    provider = NullProvider()
    assert provider.available is False
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        provider.complete_json("p", system="s", schema={})


def test_provider_selection_defaults_to_null():
    config = Config()
    config.gemini_api_key = None
    assert get_provider(config).name == "none"


def test_provider_selection_uses_gemini_when_keyed():
    config = Config()
    config.gemini_api_key = "key"
    config.gemini_model = "gemini-flash-x"
    provider = get_provider(config)
    assert provider.name == "gemini"
    assert provider.model == "gemini-flash-x"
    assert provider.available is True


def test_gemini_without_a_key_is_unavailable():
    assert GeminiProvider(api_key=None).available is False
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiProvider(api_key=None).complete_json("p", system="s", schema={})


@respx.mock
def test_gemini_returns_the_response_text():
    respx.post(ENDPOINT).mock(return_value=_response('{"relevant": true}'))
    output = GeminiProvider(api_key="k", model=MODEL).complete_json(
        "prompt", system="system", schema=SCREENING_SCHEMA
    )
    assert output == '{"relevant": true}'


@respx.mock
def test_gemini_request_carries_the_key_system_prompt_and_schema():
    route = respx.post(ENDPOINT).mock(return_value=_response("{}"))
    GeminiProvider(api_key="secret", model=MODEL).complete_json(
        "the prompt", system="the system", schema=SCREENING_SCHEMA
    )

    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == "secret"
    import json

    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "the system"
    assert body["contents"][0]["parts"][0]["text"] == "the prompt"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseSchema"]["type"] == "OBJECT"


@respx.mock
def test_classification_is_deterministic():
    """The same signal must not land in two different buckets across runs."""
    import json

    route = respx.post(ENDPOINT).mock(return_value=_response("{}"))
    GeminiProvider(api_key="k", model=MODEL).complete_json("p", system="s", schema={})
    assert json.loads(route.calls.last.request.content)["generationConfig"]["temperature"] == 0.0


@respx.mock
def test_multipart_responses_are_joined():
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"rele'}, {"text": 'vant": true}'}]}}]},
        )
    )
    output = GeminiProvider(api_key="k", model=MODEL).complete_json("p", system="s", schema={})
    assert output == '{"relevant": true}'


@respx.mock
def test_a_blocked_prompt_raises_with_the_reason():
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    with pytest.raises(RuntimeError, match="SAFETY"):
        GeminiProvider(api_key="k", model=MODEL).complete_json("p", system="s", schema={})


@respx.mock
def test_an_empty_response_raises_with_the_finish_reason():
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
        )
    )
    with pytest.raises(RuntimeError, match="MAX_TOKENS"):
        GeminiProvider(api_key="k", model=MODEL).complete_json("p", system="s", schema={})


@respx.mock
def test_http_errors_propagate_for_the_caller_to_degrade():
    respx.post(ENDPOINT).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        GeminiProvider(api_key="k", model=MODEL).complete_json("p", system="s", schema={})


@respx.mock
def test_the_model_name_selects_the_endpoint():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-9:generateContent"
    ).mock(return_value=_response("{}"))
    GeminiProvider(api_key="k", model="gemini-flash-9").complete_json("p", system="s", schema={})
    assert route.called


# -- schema translation ----------------------------------------------------
def test_union_types_become_a_nullable_flag():
    converted = _to_gemini_schema({"type": ["string", "null"]})
    assert converted == {"type": "STRING", "nullable": True}


def test_additional_properties_is_stripped():
    assert "additionalProperties" not in _to_gemini_schema(
        {"type": "object", "additionalProperties": False}
    )


def test_a_null_enum_member_becomes_nullable():
    converted = _to_gemini_schema({"type": ["string", "null"], "enum": ["a", "b", None]})
    assert converted["enum"] == ["a", "b"]
    assert converted["nullable"] is True


def test_nested_properties_and_items_are_translated():
    converted = _to_gemini_schema(
        {
            "type": "object",
            "properties": {"list": {"type": "array", "items": {"type": ["string", "null"]}}},
        }
    )
    assert converted["properties"]["list"]["type"] == "ARRAY"
    assert converted["properties"]["list"]["items"] == {"type": "STRING", "nullable": True}


def test_the_real_screening_schema_translates_cleanly():
    converted = _to_gemini_schema(SCREENING_SCHEMA)
    assert converted["type"] == "OBJECT"
    assert "additionalProperties" not in converted
    for name, spec in converted["properties"].items():
        assert spec["type"].isupper(), name
        assert "null" not in str(spec.get("type")), name
