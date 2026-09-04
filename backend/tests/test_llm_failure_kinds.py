"""What an endpoint's refusal actually says.

The pool's decision depends entirely on *why* a member declined and *when* it
will be back. Before these fields existed the only channel was a message
string, so "429, back in 21 seconds" and "revoked key, dead forever" were
handled identically — both benched for a flat minute, and a provider that had
told us exactly when to return was ignored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from app.ai.llm.base import LLMUnavailable
from app.ai.llm.openai_compat import (
    OpenAICompatProvider,
    _parse_duration,
    reset_quota_breaker,
)


def _resp(body: dict, status: int = 429, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body,
        headers=headers or {},
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


def _always(resp: httpx.Response):
    calls = {"n": 0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return resp

    return _Client, calls


@pytest.fixture
def provider(monkeypatch):
    reset_quota_breaker()
    monkeypatch.setattr("app.ai.llm.openai_compat.asyncio.sleep", _noop)
    p = OpenAICompatProvider(base_url="https://example.test", model="m", api_key="k", label="stub")
    yield p
    reset_quota_breaker()


async def _noop(_seconds):
    return None


DAILY = {
    "error": {
        "message": "Rate limit exceeded: free-models-per-day.",
        "metadata": {"limit_source": "openrouter_free_tier_daily"},
    }
}
PER_MINUTE = {"error": {"message": "Rate limit exceeded: 20 requests per minute"}}


# --- the back-compat contract every existing caller depends on --------------


def test_the_message_is_unchanged_and_the_kind_defaults():
    exc = LLMUnavailable("something went wrong")
    assert str(exc) == "something went wrong"
    assert exc.kind == "unknown"
    assert exc.retry_after is None


# --- classification ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_daily_limit_says_so_and_carries_its_cooldown(provider, monkeypatch):
    Client, _ = _always(_resp(DAILY))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.kind == "quota_exhausted"
    assert excinfo.value.retry_after and excinfo.value.retry_after > 0


@pytest.mark.asyncio
async def test_a_short_rate_limit_carries_the_wake_up_time(provider, monkeypatch):
    """The headline bug: `retry-after: 21` used to be thrown away entirely,
    because it exceeded the in-member 12s cap — so the pool never learned that
    the endpoint would be back in twenty-one seconds."""
    Client, _ = _always(_resp(PER_MINUTE, headers={"Retry-After": "21"}))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.kind == "rate_limited"
    assert excinfo.value.retry_after == pytest.approx(21.0)


@pytest.mark.asyncio
async def test_a_rejected_key_is_not_retried(provider, monkeypatch):
    Client, calls = _always(_resp({"error": {"message": "invalid api key"}}, status=401))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.kind == "bad_key"
    assert calls["n"] == 1, "a rejected key does not fix itself on a second try"


@pytest.mark.asyncio
async def test_a_truncated_answer_is_a_bad_response_not_an_outage(provider, monkeypatch):
    body = {"choices": [{"message": {"content": "half an ans"}, "finish_reason": "length"}]}
    Client, _ = _always(_resp(body, status=200))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.kind == "bad_response"


@pytest.mark.asyncio
async def test_an_empty_two_hundred_raises_instead_of_returning_nothing(provider, monkeypatch):
    """A silent hole: a 200 with empty content used to return "".

    The pool never saw a failure, so it never tried the next member — chat fell
    to its glossary and the committee to rules, with nothing in the log to say
    why.
    """
    body = {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]}
    Client, _ = _always(_resp(body, status=200))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.kind == "bad_response"


# --- how long is "later"? ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2m59.56s", 179.56),
        ("1h", 3600.0),
        ("30s", 30.0),
        ("500ms", 0.5),
        ("1m30s", 90.0),
    ],
)
def test_go_style_durations_are_understood(text, expected):
    """Groq spells resets this way. Unparsed, they silently became the 15-minute
    default — standing a healthy endpoint down five times longer than asked."""
    assert _parse_duration(text) == pytest.approx(expected)


def test_nonsense_durations_are_not_guessed_at():
    assert _parse_duration("soon") is None
    assert _parse_duration("") is None


@pytest.mark.asyncio
async def test_an_http_date_retry_after_is_parsed(provider, monkeypatch):
    when = datetime.now(UTC) + timedelta(seconds=30)
    Client, _ = _always(_resp(PER_MINUTE, headers={"Retry-After": format_datetime(when)}))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.retry_after == pytest.approx(30.0, abs=3.0)


@pytest.mark.asyncio
async def test_a_groq_style_reset_header_is_read(provider, monkeypatch):
    Client, _ = _always(_resp(PER_MINUTE, headers={"X-RateLimit-Reset-Tokens": "2m59.56s"}))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi")

    assert excinfo.value.retry_after == pytest.approx(179.56)
