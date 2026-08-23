"""The ordered failover pool.

What matters here is not that a pool can call a model — it is the ordering and
the boundaries. The preferred endpoint must answer whenever it can (otherwise
"fallback" means "whichever bucket happened to serve you"), a spent one must be
skipped without a round trip, and when every member declines the pool must raise
the same ``LLMUnavailable`` every caller already catches — because that
exception is what keeps the app working with no AI at all.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ai.llm.base import LLMUnavailable
from app.ai.llm.pool import LLMPool


class Member:
    """A pool member that answers, or refuses, on demand."""

    def __init__(self, model: str, *, answer: str | None = None, up: bool = True):
        self.model = model
        self.base_url = f"https://{model}.test"
        self.answer = answer
        self.up = up
        self.calls = 0
        self.probes = 0

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False):
        self.calls += 1
        if self.answer is None:
            raise LLMUnavailable(f"{self.model} declined")
        return self.answer

    async def is_available(self) -> bool:
        self.probes += 1
        return self.up


# --- ordering --------------------------------------------------------------
async def test_the_preferred_member_answers_and_the_rest_are_untouched():
    """The guarantee that makes this failover rather than round-robin."""
    first = Member("first", answer="from first")
    second = Member("second", answer="from second")

    assert await LLMPool([first, second]).complete("hi") == "from first"
    assert first.calls == 1
    assert second.calls == 0, "a working preferred endpoint must not be bypassed"


async def test_it_falls_through_to_the_next_member():
    spent = Member("spent")  # answer=None -> always declines
    backup = Member("backup", answer="from backup")

    assert await LLMPool([spent, backup]).complete("hi") == "from backup"
    assert spent.calls == 1
    assert backup.calls == 1


async def test_it_walks_past_several_dead_members():
    dead_one, dead_two = Member("a"), Member("b")
    alive = Member("c", answer="ok")

    assert await LLMPool([dead_one, dead_two, alive]).complete("hi") == "ok"


# --- exhaustion ------------------------------------------------------------
async def test_all_members_failing_raises_the_shared_exception():
    """Every consumer catches LLMUnavailable to reach its rules-based answer."""
    pool = LLMPool([Member("a"), Member("b")])

    with pytest.raises(LLMUnavailable) as excinfo:
        await pool.complete("hi")

    # The message should name how many were tried; a silent empty string here
    # would be far worse than an exception, since callers would render it.
    assert "2 tried" in str(excinfo.value)


async def test_a_pool_of_one_behaves_like_the_bare_provider():
    solo = Member("solo", answer="ok")
    assert await LLMPool([solo]).complete("hi") == "ok"

    with pytest.raises(LLMUnavailable):
        await LLMPool([Member("dead")]).complete("hi")


def test_an_empty_pool_is_a_programming_error():
    with pytest.raises(ValueError):
        LLMPool([])


# --- health ----------------------------------------------------------------
async def test_availability_is_true_when_any_member_is_up():
    """CommitteeService gates on this with no try/except, so it must aggregate."""
    pool = LLMPool([Member("down", up=False), Member("up", up=True)])
    assert await pool.is_available() is True


async def test_availability_is_false_only_when_every_member_is_down():
    pool = LLMPool([Member("a", up=False), Member("b", up=False)])
    assert await pool.is_available() is False


async def test_a_probe_that_raises_does_not_sink_the_pool():
    class Exploding(Member):
        async def is_available(self):
            raise RuntimeError("probe blew up")

    pool = LLMPool([Exploding("bad"), Member("good", up=True)])
    assert await pool.is_available() is True


# --- attribution -----------------------------------------------------------
async def test_model_reports_the_member_that_answered():
    """Three services persist `getattr(llm, "model", None)` after completing."""
    pool = LLMPool([Member("spent"), Member("backup", answer="ok")])

    await pool.complete("hi")

    assert pool.model == "backup", "the recorded model must not be the one that failed"


async def test_model_is_meaningful_before_anything_is_asked():
    pool = LLMPool([Member("preferred", answer="ok"), Member("other", answer="ok")])
    assert pool.model == "preferred"


async def test_concurrent_calls_do_not_overwrite_each_others_model():
    """The pool is a process-wide singleton, so this cannot be an attribute.

    Two requests in flight at once would otherwise race, and whichever finished
    last would stamp its model onto the other's stored insight.
    """
    slow = Member("slow", answer="slow")
    fast = Member("fast", answer="fast")

    async def call(member: Member) -> str | None:
        pool = LLMPool([member])
        await pool.complete("hi")
        # Yield so the other task interleaves between completing and reading.
        await asyncio.sleep(0)
        return pool.model

    slow_model, fast_model = await asyncio.gather(call(slow), call(fast))
    assert (slow_model, fast_model) == ("slow", "fast")


async def test_a_failing_member_is_benched_rather_than_retried_every_call():
    """The tax a pool exists to avoid.

    A rejected key or a dead host fails the same way every time. Re-attempting
    it on every call means the committee — which makes one call per ticker —
    pays that round trip ten times per plan, for as long as the member stays
    broken. The endpoint's own breaker covers a stated daily quota; this covers
    everything that arrives with no explanation.
    """
    broken = Member("broken")  # always declines, says nothing about why
    good = Member("good", answer="ok")
    pool = LLMPool([broken, good])

    for _ in range(5):
        assert await pool.complete("hi") == "ok"

    assert broken.calls == 1, "the broken member should be tried once, not five times"
    assert good.calls == 5


async def test_a_recovered_member_is_preferred_again():
    """Benching must not be permanent — the primary is the one we want back."""
    flaky = Member("flaky")
    backup = Member("backup", answer="from backup")
    pool = LLMPool([flaky, backup])

    assert await pool.complete("hi") == "from backup"

    # It comes back, and the bench expires.
    flaky.answer = "from flaky"
    pool._benched_until = [0.0, 0.0]

    assert await pool.complete("hi") == "from flaky"
    assert pool.model == "flaky"


async def test_members_get_their_own_concurrency_and_token_budgets():
    """Rate limits are per account, so the caps have to be per member.

    Groq's free tier allows a thousand requests a day but only eight thousand
    tokens a minute, and it reserves `max_tokens` against that budget — six
    concurrent calls of 3,500 exhaust the minute limit long before the daily
    one. Sizing one shared budget by the strictest member would throttle every
    other provider to match it.
    """
    from app.ai.llm.openai_compat import OpenAICompatProvider

    roomy = OpenAICompatProvider(base_url="https://roomy.test", model="m", api_key="a")
    tight = OpenAICompatProvider(
        base_url="https://tight.test",
        model="m",
        api_key="b",
        max_tokens=2000,
        max_concurrency=2,
    )

    assert tight.max_tokens == 2000
    assert tight.max_concurrency == 2
    # The unconfigured member keeps the global defaults rather than inheriting
    # its neighbour's ceiling.
    assert roomy.max_tokens != 2000
    assert roomy.max_concurrency != 2
    assert roomy.quota_key != tight.quota_key, "separate budgets need separate keys"


def test_extra_body_tunes_a_provider_without_touching_the_client():
    """Provider quirks belong in configuration, not in the request builder."""
    from app.ai.llm.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(
        base_url="https://groq.test",
        model="openai/gpt-oss-120b",
        api_key="k",
        extra_body={"reasoning_effort": "low"},
    )

    payload = provider._payload("hi", None, 0.3, json_object=True)
    assert payload["reasoning_effort"] == "low"
    assert payload["response_format"] == {"type": "json_object"}


def test_extra_body_cannot_rewrite_what_is_being_asked():
    """Tuning a provider must not let config silently swap the model or prompt."""
    from app.ai.llm.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(
        base_url="https://groq.test",
        model="the-real-model",
        api_key="k",
        extra_body={"model": "something-else", "messages": [], "stream": True},
    )

    payload = provider._payload("hi", None, 0.0, json_object=False)
    assert payload["model"] == "the-real-model"
    assert payload["messages"][0]["content"] == "hi"
    assert payload["stream"] is False
