"""LLM provider abstraction.

Every AI feature (categorization now; the conversational assistant, summaries
and semantic search later) depends on this interface, never on a concrete
vendor. Phase 1 ships an Ollama implementation; adding a cloud provider is a
new adapter with zero changes to call sites.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> str:
        """Return the model's text completion for a single-turn prompt."""
        ...

    async def is_available(self) -> bool:
        """Cheap health check; used to degrade gracefully when the LLM is down."""
        ...
