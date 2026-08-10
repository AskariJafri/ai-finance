"""Hosted LLM provider speaking the OpenAI chat-completions dialect.

Defaults to OpenRouter's free tier, but the endpoint is entirely configuration:
``LLM_BASE_URL`` / ``LLM_MODEL`` / ``LLM_API_KEY`` point the same code at Groq,
Cerebras, the Hugging Face router or an OpenAI-compatible proxy without a
change here.

Two constraints from the free tier shape this module:

* **Request budget.** Free models allow ~20 requests/minute and a small daily
  quota, so a module-wide semaphore caps in-flight calls — the committee fans
  out over tickers with ``asyncio.gather`` and would otherwise burst straight
  into a 429. Health probes hit ``/models`` (free, unmetered) and are cached,
  because spending a chat request to ask "are you up?" is the one call we can
  never afford.
* **Never fail the request.** Everything that goes wrong surfaces as
  ``LLMUnavailable`` so the existing rules-based fallbacks take over.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from app.ai.llm.base import LLMProvider, LLMUnavailable
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Reasoning-tuned models (several of the strongest free ones) prefix their answer
# with a private scratchpad. It must never reach user-facing prose.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_THINK_RE = re.compile(r"^.*?</think>", re.DOTALL)

# Longest we will honour a 429's Retry-After before giving up and degrading;
# a user is waiting on the other end of every one of these calls.
_MAX_RETRY_WAIT = 12.0

_limiters: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _limiter() -> asyncio.Semaphore:
    """Cap on concurrent upstream calls, shared by every provider instance.

    Keyed by event loop rather than held in a single global: an ``asyncio``
    primitive binds to the loop that first blocks on it, and the test suite runs
    each case in a fresh loop. In production there is exactly one loop and so
    exactly one budget, which is what the rate limit is measured against.
    """
    loop = asyncio.get_running_loop()
    limiter = _limiters.get(loop)
    if limiter is None:
        limiter = asyncio.Semaphore(max(1, settings.llm_max_concurrency))
        _limiters[loop] = limiter
    return limiter


def _strip_reasoning(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # opening tag was emitted as a separate field
        text = _OPEN_THINK_RE.sub("", text)
    return text.strip()


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.timeout = timeout or settings.llm_timeout_seconds
        # Injected only by tests; production always builds a real transport.
        self._transport = transport
        self._available_until = 0.0
        self._available = False

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    # --- request plumbing -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # OpenRouter attributes free-tier usage to the calling app; harmless
        # elsewhere since unknown headers are ignored.
        if settings.llm_app_url:
            headers["HTTP-Referer"] = settings.llm_app_url
        if settings.llm_app_title:
            headers["X-Title"] = settings.llm_app_title
        return headers

    def _payload(
        self, prompt: str, system: str | None, temperature: float, json_object: bool
    ) -> dict:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": settings.llm_max_tokens,
            "stream": False,
        }
        if json_object and settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _content(data: dict) -> str:
        # A 200 can still carry a provider-level error (OpenRouter does this when
        # an upstream model is out of capacity).
        if isinstance(data.get("error"), dict):
            raise LLMUnavailable(str(data["error"].get("message") or data["error"]))
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("no choices in response")
        message = choices[0].get("message") or {}
        return _strip_reasoning(message.get("content") or "")

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        json_object: bool = False,
    ) -> str:
        if not self.api_key:
            raise LLMUnavailable("no LLM_API_KEY configured")

        payload = self._payload(prompt, system, temperature, json_object)
        async with _limiter():
            return await self._post_with_retry(payload)

    async def _post_with_retry(self, payload: dict) -> str:
        url = f"{self.base_url}/chat/completions"
        last: Exception | None = None

        for attempt in (1, 2):
            try:
                async with self._client(self.timeout) as client:
                    resp = await client.post(url, json=payload, headers=self._headers())

                if resp.status_code == 429:
                    wait = _retry_after(resp)
                    if attempt == 1 and wait <= _MAX_RETRY_WAIT:
                        logger.warning("LLM rate limited; retrying in %.1fs", wait)
                        await asyncio.sleep(wait)
                        continue
                    raise LLMUnavailable("rate limited (429)")

                # Some models reject response_format outright — retry as plain
                # text, since the prompts ask for JSON anyway and the callers
                # salvage imperfect output.
                if resp.status_code == 400 and "response_format" in payload:
                    logger.warning("Model %r rejected JSON mode; retrying plain", self.model)
                    payload = {k: v for k, v in payload.items() if k != "response_format"}
                    continue

                resp.raise_for_status()
                return self._content(resp.json())

            except LLMUnavailable:
                raise
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                last = exc
                if attempt == 1 and _retryable(exc):
                    await asyncio.sleep(1.0)
                    continue
                break

        logger.warning("LLM request failed: %s", last)
        raise LLMUnavailable(str(last)) from last

    # --- health -----------------------------------------------------------

    async def is_available(self) -> bool:
        """Probe ``/models``, which is free and does not touch the chat quota.

        Cached briefly: the chat, committee and deploy endpoints each probe
        before working, and three round trips per user action is a poor trade
        for a value that changes rarely.
        """
        if not self.api_key or not settings.ai_enabled:
            return False
        now = time.monotonic()
        if now < self._available_until:
            return self._available
        try:
            async with self._client(5.0) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            self._available = resp.status_code == 200
        except httpx.HTTPError:
            self._available = False
        self._available_until = now + 60.0
        return self._available


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(0.0, float(resp.headers.get("Retry-After", "")))
    except ValueError:
        return 2.0


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)
