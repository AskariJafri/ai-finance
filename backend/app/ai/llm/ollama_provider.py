"""Ollama-backed LLM provider (local, private inference).

Talks to a local Ollama server over HTTP. Kept intentionally small: a single
``/api/generate`` call with streaming disabled. Network/availability errors are
surfaced as ``LLMUnavailable`` so callers can degrade gracefully rather than
failing a user request.
"""

from __future__ import annotations

import httpx

from app.ai.llm.base import LLMProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMUnavailable(RuntimeError):
    pass


# Models we never auto-select: embedding-only, or reasoning models that emit
# <think> blocks which would pollute user-facing text.
_UNSUITABLE = ("embed", "deepseek-r1", "qwen3")


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout

    async def _installed_model(self) -> str | None:
        """Pick any suitable installed model — this machine runs more than one
        Ollama instance and which one answers can change between restarts, so a
        hard-coded model name goes stale (Ollama 404s on a missing model)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                names = [m["name"] for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        for name in names:
            if not any(bad in name.lower() for bad in _UNSUITABLE):
                return name
        return None

    async def complete(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> str:
        try:
            return await self._generate(self.model, prompt, system, temperature)
        except LLMUnavailable as exc:
            if "404" not in str(exc):
                raise
            fallback = await self._installed_model()
            if not fallback or fallback == self.model:
                raise
            logger.warning(
                "Ollama model %r not installed; falling back to %r",
                self.model,
                fallback,
            )
            self.model = fallback
            return await self._generate(fallback, prompt, system, temperature)

    async def _generate(
        self, model: str, prompt: str, system: str | None, temperature: float
    ) -> str:
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return (data.get("response") or "").strip()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Ollama request failed: %s", exc)
            raise LLMUnavailable(str(exc)) from exc

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
