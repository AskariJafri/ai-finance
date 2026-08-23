"""The daily-quota circuit breaker.

``/models`` still answers 200 when the chat quota is spent, so no health probe
can see the limit. Without a breaker, every caller rediscovers it the expensive
way: the committee fans out one call per ticker, each burns a 429 plus a retry,
and a plan that was always going to be rules-based takes a minute and a half to
say so. These tests pin the distinction that makes it safe — a per-minute limit
is worth retrying, a daily one is not.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai.llm.base import LLMUnavailable
from app.ai.llm.openai_compat import OpenAICompatProvider, reset_quota_breaker


def _resp(body: dict, status: int = 429) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body,
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


DAILY = {
    "error": {
        "message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock",
        "code": 429,
        "metadata": {
            "limit_source": "openrouter_free_tier_daily",
            "headers": {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0"},
        },
    }
}

PER_MINUTE = {
    "error": {
        "message": "Rate limit exceeded: 20 requests per minute",
        "code": 429,
        "metadata": {"limit_source": "openrouter_free_tier_minute"},
    }
}


@pytest.fixture(autouse=True)
def _closed_breaker():
    reset_quota_breaker()
    yield
    reset_quota_breaker()


@pytest.fixture
def provider(monkeypatch) -> OpenAICompatProvider:
    p = OpenAICompatProvider(base_url="https://example.test", model="test-model", api_key="k")
    return p


def _always(resp: httpx.Response):
    """A client whose every POST returns `resp`, counting the calls."""
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


async def test_a_daily_limit_trips_the_breaker(provider, monkeypatch):
    Client, calls = _always(_resp(DAILY))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable):
        await provider.complete("hi")

    assert calls["n"] == 1, "a daily limit must not be retried"
    assert provider.quota_cooldown_remaining() > 0


async def test_a_tripped_breaker_short_circuits_without_a_request(provider, monkeypatch):
    """The point of the whole thing: no round trip once we know."""
    Client, calls = _always(_resp(DAILY))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable):
        await provider.complete("first")
    before = calls["n"]

    for _ in range(5):
        with pytest.raises(LLMUnavailable):
            await provider.complete("again")

    assert calls["n"] == before, "further calls must not reach the provider"


async def test_a_per_minute_limit_is_still_retried(provider, monkeypatch):
    """Seconds-long limits are worth waiting out; the breaker must ignore them."""
    Client, calls = _always(_resp(PER_MINUTE))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())
    monkeypatch.setattr("app.ai.llm.openai_compat.asyncio.sleep", _noop)

    with pytest.raises(LLMUnavailable):
        await provider.complete("hi")

    assert calls["n"] == 2, "a per-minute limit gets one retry"
    assert provider.quota_cooldown_remaining() == 0


async def test_availability_reports_false_while_out_of_quota(provider, monkeypatch):
    """`/models` answers 200 with the chat quota spent — don't trust it alone."""
    Client, _ = _always(_resp(DAILY))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())
    with pytest.raises(LLMUnavailable):
        await provider.complete("hi")

    assert await provider.is_available() is False


async def _noop(*args, **kwargs):
    return None


async def test_one_endpoint_running_dry_does_not_disable_another(monkeypatch):
    """The property the whole failover pool rests on.

    The breaker used to be a single module-level float, so a daily 429 from one
    provider marked *every* instance dead — including one on a different host
    with a different key. Failing over to a second endpoint would have been
    impossible: it would have been skipped before it was ever tried.
    """
    spent = OpenAICompatProvider(base_url="https://spent.test", model="m", api_key="key-a")
    fresh = OpenAICompatProvider(base_url="https://fresh.test", model="m", api_key="key-b")

    Client, _ = _always(_resp(DAILY))
    monkeypatch.setattr(spent, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable):
        await spent.complete("hi")

    assert spent.quota_cooldown_remaining() > 0
    assert fresh.quota_cooldown_remaining() == 0, "a fallback must stay usable"


async def test_the_same_key_shares_one_bucket(monkeypatch):
    """Quota is metered per credential, so two models on one key share a breaker."""
    big = OpenAICompatProvider(base_url="https://one.test", model="big", api_key="k")
    small = OpenAICompatProvider(base_url="https://one.test", model="small", api_key="k")

    Client, _ = _always(_resp(DAILY))
    monkeypatch.setattr(big, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable):
        await big.complete("hi")

    assert small.quota_cooldown_remaining() > 0


# Groq spells the same limit differently and reports no `limit_source`. Matching
# only OpenRouter's wording would leave a spent Groq key looking like a
# per-minute limit: retried forever, never remembered.
GROQ_DAILY = {
    "error": {
        "message": (
            "Rate limit reached for model `llama-3.3-70b-versatile` in "
            "organization `org_x` on tokens per day (TPD): Limit 100000, Used 100000"
        ),
        "type": "tokens",
        "code": "rate_limit_exceeded",
    }
}


async def test_a_daily_limit_phrased_the_groq_way_also_trips(monkeypatch):
    provider = OpenAICompatProvider(base_url="https://api.groq.test", model="m", api_key="k")
    Client, calls = _always(_resp(GROQ_DAILY))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable):
        await provider.complete("hi")

    assert calls["n"] == 1, "a daily limit must not be retried, however it is worded"
    assert provider.quota_cooldown_remaining() > 0


async def test_an_implausible_reset_time_falls_back_to_the_default(monkeypatch):
    """A past timestamp would leave the breaker looking set but closed.

    `X-RateLimit-Reset` is epoch-milliseconds on OpenRouter. A provider that
    reports seconds instead divides down into 1970, and `max(existing, until)`
    then silently keeps the breaker open for no time at all.
    """
    body = {
        "error": {
            "message": "Rate limit exceeded: free-models-per-day",
            "metadata": {
                "limit_source": "openrouter_free_tier_daily",
                # Seconds, not milliseconds — lands in 1970 once divided.
                "headers": {"X-RateLimit-Reset": "1787443200"},
            },
        }
    }
    provider = OpenAICompatProvider(base_url="https://seconds.test", model="m", api_key="k")
    Client, _ = _always(_resp(body))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable):
        await provider.complete("hi")

    assert provider.quota_cooldown_remaining() > 0, "the breaker must actually be open"
