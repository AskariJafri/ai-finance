"""Constructs the configured LLM provider + AI categorizer."""

from __future__ import annotations

from functools import lru_cache

from app.ai.categorizer import AICategorizer
from app.ai.llm.base import LLMProvider
from app.ai.llm.openai_compat import OpenAICompatProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    return OpenAICompatProvider()


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
