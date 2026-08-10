"""LLM provider abstraction.

Every AI feature (categorization, monthly advice, invest insight, the chat
assistant and the investment committee) depends on this interface, never on a
concrete vendor. The shipped implementation talks to any OpenAI-compatible
chat-completions endpoint, so switching hosts is an env change, not a code
change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class LLMUnavailable(RuntimeError):
    """The model could not be reached, or refused to answer.

    Lives here rather than in a provider module because every call site catches
    it to degrade gracefully — the exception is part of the interface.
    """


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
