"""How long one request is willing to wait for the model, and how much of it
it is willing to spend.

A rate limit is usually a *timing* problem, not an outage: the provider says
"back in 21 seconds" and we have three minutes of client timeout in hand. What
was missing was somewhere to record that we can afford to wait.

**Why a ContextVar and not a parameter on ``complete()``.**

The value is not per-call, it is per-fan-out. Portfolio Actions issues up to
twenty concurrent completions; giving each one "you may wait 25s" permits
`waves × 25s` of waiting bounded by nothing the user's client cares about. What
actually bounds a request is a single absolute deadline shared by every task in
it — which is exactly what a ContextVar gives, because ``asyncio.gather`` copies
the context at task creation, after the service opened the budget.

``pool.py`` already reaches for a ContextVar (``_answered_by``) for the sibling
reason: the pool is an ``lru_cache``d process-wide singleton, so per-request
state cannot live on it as an attribute.

**The default is no budget at all** — one pass, no sleeping, exactly the
behaviour that existed before this module. A caller opts in; nothing opts in on
its behalf. That is what lets the categorizer (which nobody is waiting on) and
every existing test keep the old semantics for free.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class CallBudget:
    #: Absolute ``time.monotonic()`` after which no more waiting is worthwhile.
    #: ``None`` means "no budget" — the legacy fail-fast path.
    deadline: float | None = None
    #: Longest single sleep the pool may take in one pass. Separate from the
    #: deadline on purpose: it stops one member's twenty-minute cooldown from
    #: swallowing a request that has other members worth trying.
    max_pass_sleep: float = 0.0
    #: Per-surface cap on generated tokens. Only ever lowers the endpoint's own
    #: ``max_tokens`` — a 20-token answer should not reserve 3,500 against a
    #: provider that meters tokens per minute.
    max_output_tokens: int | None = None

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()


_NO_BUDGET = CallBudget()
_budget: ContextVar[CallBudget] = ContextVar("llm_call_budget", default=_NO_BUDGET)


@contextmanager
def llm_budget(
    *,
    wait: float | None,
    max_pass_sleep: float = 25.0,
    max_output_tokens: int | None = None,
) -> Iterator[None]:
    """Open a budget for everything called inside this block.

    ``wait`` is the whole request's allowance, in seconds, and should be set
    against the *client's* timeout for that surface — a budget that outlives the
    client is spent producing an answer nobody receives.

    ``wait=None`` means "no waiting, but still apply the token cap": one pass,
    fail fast, exactly the pre-budget behaviour. That is a different thing from
    ``wait=0``, which would set a deadline that has already passed and stop the
    pool starting a call at all — the caller wants an answer, it just does not
    want to sit in a queue for one.
    """
    token = _budget.set(
        CallBudget(
            deadline=None if wait is None else time.monotonic() + wait,
            max_pass_sleep=max_pass_sleep,
            max_output_tokens=max_output_tokens,
        )
    )
    try:
        yield
    finally:
        _budget.reset(token)


def current_budget() -> CallBudget:
    return _budget.get()
