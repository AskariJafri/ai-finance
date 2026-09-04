"""The per-request wait allowance.

The load-bearing property is the one in
``test_a_budget_is_shared_by_every_task_in_a_fan_out``: a plan issues its
committee calls concurrently, and what bounds the request is a single absolute
deadline they all share — not a per-call allowance each of them may spend in
full. That is the whole reason this is a ContextVar and not an argument.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ai.llm.budget import current_budget, llm_budget


def test_no_budget_is_the_default():
    """Waiting is opt-in. Everything that does not ask for it keeps the old
    fail-fast behaviour, which is what the rest of the suite assumes."""
    budget = current_budget()
    assert budget.deadline is None
    assert budget.remaining() is None
    assert budget.max_output_tokens is None


def test_a_none_wait_caps_tokens_without_setting_a_deadline():
    """ "Don't wait" must not become "don't try".

    A `wait=0` budget sets a deadline that has already passed, and the pool's
    minimum-useful-time floor then refuses to start any call at all — the
    categorizer stopped reaching the model entirely. Unattended callers want an
    answer without a queue, which is a deadline of None.
    """
    with llm_budget(wait=None, max_output_tokens=200):
        budget = current_budget()
        assert budget.deadline is None
        assert budget.remaining() is None
        assert budget.max_output_tokens == 200


def test_a_budget_reports_its_remaining_time():
    with llm_budget(wait=30.0):
        remaining = current_budget().remaining()
    assert remaining is not None and 0 < remaining <= 30.0


def test_a_budget_is_cleared_on_exit():
    with llm_budget(wait=30.0):
        pass
    assert current_budget().deadline is None


def test_a_budget_is_cleared_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with llm_budget(wait=30.0):
            raise ValueError("boom")
    assert current_budget().deadline is None


def test_budgets_nest_without_leaking():
    """Chat inside a plan must not widen the plan's allowance, or narrow it
    permanently once it returns."""
    with llm_budget(wait=100.0, max_output_tokens=2400):
        outer = current_budget()
        with llm_budget(wait=5.0, max_output_tokens=900):
            inner = current_budget()
            assert inner.max_output_tokens == 900
            assert inner.deadline < outer.deadline
        assert current_budget().max_output_tokens == 2400


@pytest.mark.asyncio
async def test_a_budget_is_shared_by_every_task_in_a_fan_out():
    """The property that justifies a ContextVar over a parameter.

    ``asyncio.gather`` copies the context at task creation, so twenty concurrent
    committee calls see one deadline — the request's. A per-call allowance
    would instead let each wave wait the full amount, bounded by nothing the
    client cares about.
    """
    seen: list[float | None] = []

    async def one():
        seen.append(current_budget().deadline)

    with llm_budget(wait=60.0):
        expected = current_budget().deadline
        await asyncio.gather(*(one() for _ in range(20)))

    assert seen == [expected] * 20


@pytest.mark.asyncio
async def test_a_budget_does_not_leak_into_unrelated_tasks():
    async def outside():
        return current_budget().deadline

    with llm_budget(wait=60.0):
        pass
    assert await outside() is None
