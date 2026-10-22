"""LLM provider abstraction.

Every AI feature (categorization, monthly advice, invest insight, the chat
assistant and the investment committee) depends on this interface, never on a
concrete vendor. The shipped implementation talks to any OpenAI-compatible
chat-completions endpoint, so switching hosts is an env change, not a code
change.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

#: Why an endpoint declined. The distinction that matters is *when it comes
#: back*: seconds for a short rate limit, tomorrow for a spent daily allowance,
#: never for a rejected key — and immediately for a reply that was merely too
#: long, where the endpoint itself is perfectly healthy.
LLMFailureKind = Literal[
    "rate_limited",
    "quota_exhausted",
    "transport",
    "bad_key",
    "bad_response",
    # The provider says the model does not exist (404, or a 400 naming an
    # unknown or retired model). Free models are withdrawn without notice, and
    # until this existed a retired primary looked like a 30-second blip and was
    # re-dialled all day.
    "model_gone",
    "unknown",
]


class LLMUnavailable(RuntimeError):
    """The model could not be reached, or refused to answer.

    Lives here rather than in a provider module because every call site catches
    it to degrade gracefully — the exception is part of the interface.

    It carries *why* and *for how long* because the pool's decision depends on
    both: a 429 that says "back in 21 seconds" is worth waiting out, while a
    401 is worth standing down from for a quarter of an hour. Every field
    defaults and ``str(exc)`` is unchanged, so the seven call sites that only
    catch-and-degrade need no edits.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: LLMFailureKind = "unknown",
        retry_after: float | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: LLMFailureKind = kind
        #: Seconds from now until this endpoint is worth asking again. ``None``
        #: means we genuinely do not know — not "immediately".
        self.retry_after = retry_after
        self.endpoint = endpoint


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        json_object: bool = False,
    ) -> str:
        """Return the model's text completion for a single-turn prompt.

        ``json_object`` asks the provider to constrain the reply to valid JSON
        where the model supports it. Callers must still tolerate imperfect
        output — it is a hint, not a guarantee.
        """
        ...

    async def is_available(self) -> bool:
        """Cheap health check; used to degrade gracefully when the LLM is down."""
        ...
