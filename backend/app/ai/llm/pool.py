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
"""

from __future__ import annotations

import time
from contextvars import ContextVar

from app.ai.llm.base import LLMProvider, LLMUnavailable
from app.core.logging import get_logger

logger = get_logger(__name__)

# How long a member sits out after a failure it did not explain. A spent daily
# quota is remembered by the endpoint itself, precisely and for hours; this
# covers everything else — a rejected key, a 5xx, a refused connection — where
# the only thing we know is that asking again immediately is a waste. Short,
# because the preferred member is the one we actually want back.
_BENCH_SECONDS = 60.0

# The model that answered the call being handled right now. A ContextVar rather
# than an attribute because the pool is a process-wide singleton: three services
# read ``getattr(llm, "model", None)`` after ``complete()`` returns and persist
# it, and concurrent requests would otherwise overwrite each other's answer.
_answered_by: ContextVar[str | None] = ContextVar("llm_pool_answered_by", default=None)


class LLMPool(LLMProvider):
    def __init__(self, members: list[LLMProvider]) -> None:
        if not members:
            raise ValueError("an LLM pool needs at least one member")
        self.members = members
        # Parallel to `members`. A plain list rather than a lock: the only
        # mutation is one element assignment, which the event loop cannot
        # interleave, and a lost update costs one extra round trip.
        self._benched_until = [0.0] * len(members)

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
        last: Exception | None = None

        for index, member in enumerate(self.members):
            if self._benched_until[index] > time.monotonic():
                continue
            try:
                answer = await member.complete(
                    prompt,
                    system=system,
                    temperature=temperature,
                    json_object=json_object,
                )
            except LLMUnavailable as exc:
                # Everything that reaches here — spent quota, a 5xx, a truncated
                # reply, an empty choices array — means this endpoint did not
                # produce an answer. Another model may well succeed, so the
                # reason does not change what to do next; it only changes
                # whether the member remembers (its own breaker handles that).
                logger.warning(
                    "LLM %s unavailable (%s); trying the next endpoint",
                    _name(member),
                    exc,
                )
                self._benched_until[index] = time.monotonic() + _BENCH_SECONDS
                last = exc
                continue

            # It answered, so whatever put it on the bench is over.
            self._benched_until[index] = 0.0
            _answered_by.set(getattr(member, "model", None))
            if index:
                logger.info("LLM failed over to %s", _name(member))
            return answer

        # Every member declined. Raise rather than return something empty: all
        # seven callers already catch this and fall back to their rules-based
        # answer, and that contract is what keeps the app working with no AI at
        # all.
        raise LLMUnavailable(
            f"no LLM endpoint could answer ({len(self.members)} tried): {last}"
        ) from last

    async def is_available(self) -> bool:
        """True when *any* member is up.

        ``CommitteeService`` gates on this with no try/except, so an aggregate
        is required — reporting the preferred member's state alone would
        disable the feature whenever it is the one that ran out.
        """
        for member in self.members:
            try:
                if await member.is_available():
                    return True
            except Exception as exc:  # noqa: BLE001 — a probe is not worth failing on
                logger.warning("LLM %s health probe failed: %s", _name(member), exc)
        return False


def _name(member: LLMProvider) -> str:
    return str(getattr(member, "base_url", None) or getattr(member, "model", member))
