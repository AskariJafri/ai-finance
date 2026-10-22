"""Constructs the configured LLM provider + AI categorizer."""

from __future__ import annotations

from functools import lru_cache

from app.ai.categorizer import AICategorizer
from app.ai.llm.base import LLMProvider
from app.ai.llm.openai_compat import OpenAICompatProvider
from app.ai.llm.pool import LLMPool
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_llm_provider() -> LLMProvider:
    """The configured endpoint, plus any fallbacks, as one provider.

    Always a pool — even with nothing configured beyond the primary — so there
    is one code path rather than two that drift apart. A pool of one behaves
    exactly like the bare client it wraps.
    """
    members: list[LLMProvider] = [OpenAICompatProvider()]
    members += [
        OpenAICompatProvider(
            base_url=endpoint.base_url,
            model=endpoint.model,
            api_key=endpoint.api_key,
            timeout=endpoint.timeout,
            max_tokens=endpoint.max_tokens,
            max_concurrency=endpoint.max_concurrency,
            extra_body=endpoint.extra_body,
            label=endpoint.display(),
            quota_scope=endpoint.quota_scope,
            json_mode=endpoint.json_mode,
        )
        # A fallback with no key is not a fallback. Left in, it declines on
        # every single call and is benched on every single pass — pure noise in
        # the logs and one wasted position in the preference order, which
        # matters once the ladder is ten deep.
        for endpoint in settings.llm_fallbacks
        if endpoint.api_key
    ]
    logger.info(
        "LLM pool: %d endpoint(s) — %s",
        len(members),
        ", ".join(getattr(m, "label", "?") for m in members),
    )
    return LLMPool(members)


@lru_cache
def get_ai_categorizer() -> AICategorizer:
    return AICategorizer(get_llm_provider())


def get_advice_llm_provider() -> LLMProvider:
    """Narrative generation and committee deliberation.

    Separate names because the call sites are separate concerns, but one shared
    instance: it keeps a single health-probe cache and a single concurrency
    budget, which is what the free tier's rate limit is measured against. (Under
    Ollama these needed their own timeouts — a hosted model answers all three in
    seconds.)
    """
    return get_llm_provider()


def get_chat_llm_provider() -> LLMProvider:
    return get_llm_provider()
