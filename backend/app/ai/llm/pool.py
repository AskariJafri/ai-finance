"""An ordered pool of interchangeable LLM endpoints.

Free tiers are metered per API key. One provider backing every AI feature means
one bucket: OpenRouter's free allowance is fifty requests a day, a single
portfolio plan spends ten of them, and when it runs dry the committee, the cash
planner, the portfolio review, Ask and merchant categorisation all drop to their
rules-based answers until midnight UTC.

Holding several endpoints fixes that without changing a single caller. The pool
satisfies ``LLMProvider`` itself, so everything downstream of
``factory.get_llm_provider()`` is unaware there is more than one.

**Preference order, not round-robin.** Members are tried in the order given and
the first one that answers wins, so the model you chose answers whenever it can
and a fallback is genuinely a fallback. Spreading load would triple the daily
ceiling but make an answer's quality depend on which bucket happened to serve
it — and the committee already fans out ten calls per plan, which would mix
models within a single verdict.

A member that has reported its daily quota spent is skipped without a round trip
(``OpenAICompatProvider.complete`` raises before it touches the network), so the
cost of a dead provider is paid once per day rather than once per call.

**The pool is patient when it can afford to be.** Most "AI offline" is not an
outage, it is a timing problem: a 429 that says "back in 21 seconds" arriving
inside a request with three minutes of client timeout in hand. Given a
``CallBudget`` (see ``budget.py``) the pool will sit that out and try again
rather than declaring itself down. With no budget set it behaves exactly as it
always did — one pass, no sleeping — which is what keeps the fast paths fast and
every existing caller unchanged.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from contextvars import ContextVar

from app.ai.llm.base import LLMFailureKind, LLMProvider, LLMUnavailable
from app.ai.llm.budget import current_budget
from app.core.logging import get_logger

logger = get_logger(__name__)

# How long a member sits out, by why it declined. The old code used one flat
# minute for everything, which was defensible only while every failure was
# equally opaque — now that the exception says what happened, treating a
# rejected key like a momentary 429 is just throwing endpoints away.
_BENCH_SECONDS = 60.0
_BENCH_BY_KIND: dict[LLMFailureKind, float] = {
    # The provider told us when. Floor so a "retry in 0s" cannot hot-loop; cap
    # so a wildly long value cannot retire a member for the whole session.
    "rate_limited": _BENCH_SECONDS,
    # Its own breaker already remembers this precisely, and for hours.
    "quota_exhausted": _BENCH_SECONDS,
    # A timeout or a 5xx: probably brief, but nobody said so.
    "transport": 30.0,
    # A rejected key does not heal. Re-dialling it every minute forever costs
    # latency on every request for the rest of the process's life.
    "bad_key": 900.0,
    # NOT a health problem. The endpoint answered promptly; the answer was
    # truncated or empty. Benching it would remove a working endpoint from the
    # pool over a max_tokens mistake — so it sits out for no time at all and
    # costs one attempt instead.
    "bad_response": 0.0,
    # The model was retired or never existed. Free models disappear without
    # notice; re-dialling one every 30s spends a round trip per call all day.
    "model_gone": 900.0,
    "unknown": _BENCH_SECONDS,
}
_RATE_LIMIT_BENCH_FLOOR = 5.0
_RATE_LIMIT_BENCH_CAP = 900.0

# Don't start a call that cannot finish: a completion takes seconds, and
# spending the last two of the budget dialling one is how a request times out
# with nothing to show.
_MIN_USEFUL = 4.0

# Bound on how many times the pool will walk the members in one call. Without
# it, a member that always fails with "retry in 1s" under a long budget would
# be retried until the deadline — a livelock that looks like a hang.
_MAX_PASSES = 3

# Attempts per member per call. More than one because `bad_response` benches
# for zero seconds and would otherwise be revisited on every pass.
_ATTEMPTS_PER_MEMBER = 2

# The model that answered the call being handled right now. A ContextVar rather
# than an attribute because the pool is a process-wide singleton: three services
# read ``getattr(llm, "model", None)`` after ``complete()`` returns and persist
# it, and concurrent requests would otherwise overwrite each other's answer.
_answered_by: ContextVar[str | None] = ContextVar("llm_pool_answered_by", default=None)


def _bench_for(exc: LLMUnavailable) -> float:
    kind: LLMFailureKind = getattr(exc, "kind", "unknown")
    retry_after = getattr(exc, "retry_after", None)
    if kind in ("rate_limited", "quota_exhausted") and retry_after is not None:
        return min(max(retry_after, _RATE_LIMIT_BENCH_FLOOR), _RATE_LIMIT_BENCH_CAP)
    return _BENCH_BY_KIND.get(kind, _BENCH_SECONDS)


class LLMPool(LLMProvider):
    def __init__(self, members: list[LLMProvider]) -> None:
        if not members:
            raise ValueError("an LLM pool needs at least one member")
        self.members = members
        # Parallel to `members`. A plain list rather than a lock: the only
        # mutation is one element assignment, which the event loop cannot
        # interleave, and a lost update costs one extra round trip.
        self._benched_until = [0.0] * len(members)
        # Why each member was last benched, for `status()`. Diagnostic only;
        # nothing decides on it.
        self._bench_kind: list[LLMFailureKind | None] = [None] * len(members)

    @property
    def model(self) -> str | None:
        """The model that answered the most recent call in this context.

        Falls back to the preferred member's model before anything has been
        asked, so a caller reading this without a completion still gets a
        meaningful label rather than ``None``.
        """
        return _answered_by.get() or getattr(self.members[0], "model", None)

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        json_object: bool = False,
    ) -> str:
        budget = current_budget()
        attempts = [0] * len(self.members)
        last: LLMUnavailable | None = None

        for _pass in range(_MAX_PASSES):
            soonest = math.inf
            # A member benched for zero seconds (a truncated or empty answer)
            # is available again immediately. It needs another pass, but not a
            # sleep — and not a budget either, since nothing is being waited on.
            retry_now = False
            out_of_time = False

            for index, member in enumerate(self.members):
                now = time.monotonic()
                if self._benched_until[index] > now:
                    soonest = min(soonest, self._benched_until[index])
                    continue
                if attempts[index] >= _ATTEMPTS_PER_MEMBER:
                    continue
                if budget.deadline is not None and budget.deadline - now < _MIN_USEFUL:
                    out_of_time = True
                    break

                attempts[index] += 1
                try:
                    answer = await member.complete(
                        prompt,
                        system=system,
                        temperature=temperature,
                        json_object=json_object,
                    )
                except LLMUnavailable as exc:
                    last = exc
                    wake = self._bench(index, exc)
                    if wake is None:
                        retry_now = True
                    else:
                        soonest = min(soonest, wake)
                    continue
                except Exception as exc:  # noqa: BLE001 — a provider bug is still a decline
                    # Callers guard on LLMUnavailable only, so anything else
                    # escaping here would bypass every fallback in the app.
                    last = LLMUnavailable(str(exc), kind="unknown")
                    wake = self._bench(index, last)
                    if wake is None:
                        retry_now = True
                    else:
                        soonest = min(soonest, wake)
                    continue

                # It answered, so whatever put it on the bench is over.
                self._benched_until[index] = 0.0
                self._bench_kind[index] = None
                _answered_by.set(getattr(member, "model", None))
                if index:
                    logger.info("LLM failed over to %s", _name(member))
                return answer

            if out_of_time:
                break
            if retry_now and any(a < _ATTEMPTS_PER_MEMBER for a in attempts):
                continue  # something is ready right now; no waiting involved

            # Every member is sitting out. Wait only when a budget says we can
            # afford to, and only when there would still be time to make the
            # call afterwards — sleeping right up to the deadline burns the
            # request's remaining seconds to arrive with none left to use.
            if budget.deadline is None or soonest is math.inf:
                break
            if soonest + _MIN_USEFUL > budget.deadline:
                break
            wait = soonest - time.monotonic()
            if wait <= 0 or wait > budget.max_pass_sleep:
                break
            logger.info("LLM pool waiting %.1fs for an endpoint to free up", wait)
            # Jitter: a fan-out shares `_benched_until`, so without it twenty
            # tasks wake on the same millisecond and rebuild the 429 they just
            # waited out.
            await asyncio.sleep(wait + random.uniform(0.0, min(2.0, wait * 0.25)))

        raise LLMUnavailable(
            f"no LLM endpoint could answer ({len(self.members)} tried): {last}",
            kind=getattr(last, "kind", "unknown"),
            retry_after=getattr(last, "retry_after", None),
        ) from last

    def _bench(self, index: int, exc: LLMUnavailable) -> float | None:
        """Sit a member out. Returns when it is next worth trying, or ``None``
        when it is worth trying again straight away."""
        seconds = _bench_for(exc)
        logger.warning(
            "LLM %s unavailable (%s); benched %.0fs, trying the next endpoint",
            _name(self.members[index]),
            exc,
            seconds,
        )
        if seconds <= 0:
            self._benched_until[index] = 0.0
            return None
        self._bench_kind[index] = getattr(exc, "kind", "unknown")
        self._benched_until[index] = time.monotonic() + seconds
        return self._benched_until[index]

    async def is_available(self) -> bool:
        """True when *any* member is up.

        ``CommitteeService`` gates on this with no try/except, so an aggregate
        is required — reporting the preferred member's state alone would
        disable the feature whenever it is the one that ran out.

        Benched members are skipped rather than probed: a probe costs an HTTP
        round trip per endpoint, and with a long pool a cold check would spend
        several seconds confirming what the bench already recorded.
        """
        now = time.monotonic()
        for index, member in enumerate(self.members):
            if self._benched_until[index] > now:
                continue
            try:
                if await member.is_available():
                    return True
            except Exception as exc:  # noqa: BLE001 — a probe is not worth failing on
                logger.warning("LLM %s health probe failed: %s", _name(member), exc)
        return False

    def live_member_count(self) -> int:
        """Members not currently benched or known quota-spent.

        Callers use this to decide whether the AI is *provably* gone, rather
        than gating a whole fan-out on one optimistic health probe.
        """
        now = time.monotonic()
        live = 0
        for index, member in enumerate(self.members):
            if self._benched_until[index] > now:
                continue
            remaining = getattr(member, "quota_cooldown_remaining", None)
            if callable(remaining) and remaining() > 0:
                continue
            live += 1
        return live

    def alternate(self, exclude_model: str | None) -> LLMPool | None:
        """A pool of the live members running a *different* model, or None.

        For a second opinion: asking the same weights twice (even through a
        different host — ``openai/gpt-oss-120b`` on Groq is ``gpt-oss-120b`` on
        Cerebras) is a re-roll, not a check.
        """
        now = time.monotonic()
        excluded = _model_family(exclude_model)
        others: list[LLMProvider] = []
        for index, member in enumerate(self.members):
            if self._benched_until[index] > now:
                continue
            remaining = getattr(member, "quota_cooldown_remaining", None)
            if callable(remaining) and remaining() > 0:
                continue
            if _model_family(getattr(member, "model", None)) == excluded:
                continue
            others.append(member)
        return LLMPool(others) if others else None

    def status(self) -> list[dict]:
        """Each member's state, for diagnostics. No network calls.

        ``state`` is ``ok``, ``quota`` (daily allowance spent), or the failure
        kind that benched it (``bad_key``, ``model_gone``, ``rate_limited``…).
        ``retry_in`` is whole seconds until it is worth asking again.
        """
        now = time.monotonic()
        out: list[dict] = []
        for index, member in enumerate(self.members):
            remaining = getattr(member, "quota_cooldown_remaining", None)
            quota = remaining() if callable(remaining) else 0.0
            bench = max(0.0, self._benched_until[index] - now)
            if quota > 0:
                state, wait = "quota", max(quota, bench)
            elif bench > 0:
                state, wait = str(self._bench_kind[index] or "benched"), bench
            else:
                state, wait = "ok", 0.0
            out.append(
                {
                    "label": _name(member),
                    "model": getattr(member, "model", None),
                    "state": state,
                    "retry_in": math.ceil(wait),
                }
            )
        return out


def _model_family(model: str | None) -> str:
    """``openai/gpt-oss-120b:free`` -> ``gpt-oss-120b``: the weights, not the host."""
    return (model or "").lower().rsplit("/", 1)[-1].split(":", 1)[0]


def _name(member: LLMProvider) -> str:
    return str(
        getattr(member, "label", None)
        or getattr(member, "base_url", None)
        or getattr(member, "model", member)
    )
