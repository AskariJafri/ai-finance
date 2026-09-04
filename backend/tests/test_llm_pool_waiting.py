"""The pool waits when it can afford to, and stands down when it cannot.

Most "AI offline" was never an outage. It was a 429 saying "back in 21 seconds"
arriving inside a request with three minutes of client timeout in hand, and a
pool with nowhere to record that it could afford to wait.

The clock here is fake and the sleep advances it, so bench expiry is exact and
the suite stays instant. Assertions are on the durations *requested*, which is
a much stronger claim than "it slept".
"""

from __future__ import annotations

import pytest

from app.ai.llm.base import LLMUnavailable
from app.ai.llm.budget import llm_budget
from app.ai.llm.pool import LLMPool


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock that only moves when something sleeps."""
    state = {"now": 1_000.0, "slept": []}

    def _monotonic():
        return state["now"]

    async def _sleep(seconds):
        state["slept"].append(seconds)
        state["now"] += seconds

    monkeypatch.setattr("app.ai.llm.pool.time.monotonic", _monotonic)
    monkeypatch.setattr("app.ai.llm.pool.asyncio.sleep", _sleep)
    monkeypatch.setattr("app.ai.llm.budget.time.monotonic", _monotonic)
    # Jitter would make sleep durations unassertable.
    monkeypatch.setattr("app.ai.llm.pool.random.uniform", lambda a, b: 0.0)
    return state


class Member:
    """Fails `fail_times` times with a given kind, then answers."""

    model = "stub"

    def __init__(self, *, kind="rate_limited", retry_after=None, fail_times=10**6):
        self.kind = kind
        self.retry_after = retry_after
        self.fail_times = fail_times
        self.calls = 0

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise LLMUnavailable("nope", kind=self.kind, retry_after=self.retry_after)
        return "answer"

    async def is_available(self):
        return True


# --- the legacy contract ----------------------------------------------------


@pytest.mark.asyncio
async def test_with_no_budget_the_pool_behaves_exactly_as_before(clock):
    """The test that protects every other test in the suite.

    A budget is opt-in. Without one the pool makes a single pass and raises
    immediately, which is what 370-odd existing tests assume.
    """
    member = Member(kind="rate_limited", retry_after=8.0)
    pool = LLMPool([member])

    with pytest.raises(LLMUnavailable):
        await pool.complete("hi")

    assert clock["slept"] == [], "no budget means no waiting"
    assert member.calls == 1


@pytest.mark.asyncio
async def test_the_failure_message_still_names_how_many_were_tried(clock):
    pool = LLMPool([Member(), Member()])
    with pytest.raises(LLMUnavailable) as excinfo:
        await pool.complete("hi")
    assert "2 tried" in str(excinfo.value)


# --- waiting ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_short_rate_limit_is_waited_out_rather_than_failed(clock):
    """The headline fix: 21 seconds of patience instead of an offline message."""
    member = Member(kind="rate_limited", retry_after=8.0, fail_times=1)
    pool = LLMPool([member])

    with llm_budget(wait=60.0, max_pass_sleep=25.0):
        answer = await pool.complete("hi")

    assert answer == "answer"
    assert clock["slept"] == [pytest.approx(8.0)], "waited exactly what it was told"
    assert member.calls == 2


@pytest.mark.asyncio
async def test_a_budget_too_small_to_wait_does_not_wait(clock):
    """The deadline genuinely bounds latency — it is not advisory."""
    member = Member(kind="rate_limited", retry_after=30.0)
    pool = LLMPool([member])

    with pytest.raises(LLMUnavailable):
        with llm_budget(wait=5.0, max_pass_sleep=25.0):
            await pool.complete("hi")

    assert clock["slept"] == []


@pytest.mark.asyncio
async def test_one_members_long_cooldown_cannot_eat_the_request(clock):
    """max_pass_sleep exists so a 15-minute bench does not become a 15-minute wait."""
    member = Member(kind="rate_limited", retry_after=300.0)
    pool = LLMPool([member])

    with pytest.raises(LLMUnavailable):
        with llm_budget(wait=600.0, max_pass_sleep=25.0):
            await pool.complete("hi")

    assert clock["slept"] == []


@pytest.mark.asyncio
async def test_a_healthy_member_answers_without_anyone_waiting(clock):
    dead = Member(kind="rate_limited", retry_after=20.0)
    alive = Member(fail_times=0)
    pool = LLMPool([dead, alive])

    with llm_budget(wait=60.0):
        assert await pool.complete("hi") == "answer"

    assert clock["slept"] == [], "failover is free; only a dead pool waits"


@pytest.mark.asyncio
async def test_the_pool_gives_up_after_a_bounded_number_of_passes(clock):
    """Anti-livelock: 'retry in 1s' under a long budget must still terminate."""
    member = Member(kind="rate_limited", retry_after=1.0)
    pool = LLMPool([member])

    with pytest.raises(LLMUnavailable):
        with llm_budget(wait=300.0, max_pass_sleep=25.0):
            await pool.complete("hi")

    assert len(clock["slept"]) <= 3
    assert member.calls <= 6, "bounded passes x bounded attempts"


# --- benching by cause ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_truncated_answer_does_not_bench_a_healthy_endpoint(clock):
    """`bad_response` is a max_tokens problem, not an outage.

    Benching here used to remove a perfectly good endpoint from the pool for a
    minute because one answer ran long — a live source of "AI offline".
    """
    member = Member(kind="bad_response", fail_times=1)
    pool = LLMPool([member])

    assert await pool.complete("hi") == "answer"
    assert clock["slept"] == [], "it was never unavailable, so there was nothing to wait for"
    assert member.calls == 2


@pytest.mark.asyncio
async def test_a_rejected_key_is_stood_down_for_much_longer_than_a_rate_limit(clock):
    bad_key = LLMPool([Member(kind="bad_key")])
    with pytest.raises(LLMUnavailable):
        await bad_key.complete("hi")
    assert bad_key._benched_until[0] - clock["now"] == pytest.approx(900.0)

    limited = LLMPool([Member(kind="rate_limited", retry_after=8.0)])
    with pytest.raises(LLMUnavailable):
        await limited.complete("hi")
    assert limited._benched_until[0] - clock["now"] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_a_benched_member_is_skipped_by_a_later_call(clock):
    member = Member(kind="transport")
    pool = LLMPool([member])

    with pytest.raises(LLMUnavailable):
        await pool.complete("first")
    with pytest.raises(LLMUnavailable):
        await pool.complete("second")

    assert member.calls == 1, "the second call must not re-dial a benched member"


# --- robustness -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_provider_bug_does_not_escape_the_pool(clock):
    """Callers guard on LLMUnavailable only; anything else would bypass every
    fallback in the app and surface as a 500."""

    class Exploding:
        model = "boom"

        async def complete(self, *a, **k):
            raise TypeError("provider bug")

        async def is_available(self):
            return True

    pool = LLMPool([Exploding()])
    with pytest.raises(LLMUnavailable):
        await pool.complete("hi")


@pytest.mark.asyncio
async def test_live_member_count_ignores_benched_members(clock):
    """What the planners gate on: is the AI *provably* gone, not is it perfect."""
    pool = LLMPool([Member(kind="transport"), Member(kind="transport")])
    assert pool.live_member_count() == 2

    with pytest.raises(LLMUnavailable):
        await pool.complete("hi")

    assert pool.live_member_count() == 0, "both benched, so the AI is provably down"
