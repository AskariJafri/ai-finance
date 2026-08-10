"""Hosted LLM provider: the failure modes a free tier actually produces.

Every test drives a stubbed transport — nothing here touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai.llm.base import LLMUnavailable
from app.ai.llm.openai_compat import OpenAICompatProvider


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _provider(handler, **kwargs) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="https://llm.test/v1",
        model="test/model",
        api_key="k",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_returns_message_content():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return _chat_response("  Solid month.  ")

    assert await _provider(handler).complete("hi", system="be brief") == "Solid month."
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k"


@pytest.mark.asyncio
async def test_strips_reasoning_scratchpad():
    """Several of the strongest free models think out loud before answering."""
    body = "<think>weighing the options</think>Spending is up 4%."
    assert await _provider(lambda _: _chat_response(body)).complete("hi") == "Spending is up 4%."


@pytest.mark.asyncio
async def test_retries_once_on_rate_limit_then_succeeds():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return _chat_response("ok")

    assert await _provider(handler).complete("hi") == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_persistent_rate_limit_degrades():
    with pytest.raises(LLMUnavailable):
        await _provider(lambda _: httpx.Response(429, headers={"Retry-After": "0"})).complete("hi")


@pytest.mark.asyncio
async def test_retries_without_json_mode_when_model_rejects_it():
    """JSON mode is a hint; a model that refuses it must still answer."""
    formats: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        formats.append("response_format" in json.loads(request.content))
        if formats[-1]:
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return _chat_response('{"category":"groceries"}')

    result = await _provider(handler).complete("hi", json_object=True)
    assert result == '{"category":"groceries"}'
    assert formats == [True, False]


@pytest.mark.asyncio
async def test_error_body_on_a_200_is_not_treated_as_an_answer():
    body = {"error": {"message": "upstream out of capacity"}}
    with pytest.raises(LLMUnavailable, match="capacity"):
        await _provider(lambda _: httpx.Response(200, json=body)).complete("hi")


@pytest.mark.asyncio
async def test_transport_failure_degrades():
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMUnavailable):
        await _provider(handler).complete("hi")


@pytest.mark.asyncio
async def test_missing_key_never_reaches_the_network():
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("request attempted without an API key")

    provider = OpenAICompatProvider(
        base_url="https://llm.test/v1", api_key="", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LLMUnavailable, match="LLM_API_KEY"):
        await provider.complete("hi")
    assert await provider.is_available() is False


@pytest.mark.asyncio
async def test_health_probe_hits_models_and_is_cached():
    """The probe must not spend a chat request — the daily quota is the scarce thing."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    provider = _provider(handler)
    assert await provider.is_available() is True
    assert await provider.is_available() is True
    assert calls == ["/v1/models"]
