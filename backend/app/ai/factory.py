"""Constructs the configured LLM provider + AI categorizer."""

from __future__ import annotations

from functools import lru_cache

from app.ai.categorizer import AICategorizer
from app.ai.llm.base import LLMProvider
from app.ai.llm.ollama_provider import OllamaProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    # Only Ollama is wired in Phase 1; swap here to add a cloud provider.
    return OllamaProvider()


@lru_cache
def get_ai_categorizer() -> AICategorizer:
    return AICategorizer(get_llm_provider())


@lru_cache
def get_advice_llm_provider() -> LLMProvider:
    # Narrative generation on a local CPU model needs far more headroom than
    # the categorizer's default 30s.
    return OllamaProvider(timeout=90.0)


@lru_cache
def get_chat_llm_provider() -> LLMProvider:
    # Someone is watching a typing indicator, so the ceiling is the user's
    # patience rather than the model's. Its own instance keeps a slow chat turn
    # from evicting the advice provider's negotiated model name.
    return OllamaProvider(timeout=120.0)
