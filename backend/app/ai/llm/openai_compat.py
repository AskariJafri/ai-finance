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
* **Give up fast once the daily quota is gone.** ``/models`` still answers 200
  when the chat quota is exhausted, so a health probe cannot see it. Without a
  circuit breaker every caller then discovers the limit the expensive way — the
  committee fans out ten calls, each burns a 429 and a retry, and a plan that
  will end up rules-based anyway takes ninety seconds to say so.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.ai.llm.base import LLMProvider, LLMUnavailable
from app.ai.llm.budget import current_budget
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

# "Rate limit exceeded: free-models-per-day" (OpenRouter) and "Rate limit
# reached for model ... per day" (Groq) mean the same thing and are spelled
# differently. Matching only one of them would leave the other's exhaustion
# looking like a per-minute limit: retried forever, never remembered, and the
# pool re-paying a round trip to a dead endpoint on every single call.
_DAILY_LIMIT_RE = re.compile(r"per[-\s]?day|requests per day|daily", re.IGNORECASE)

# When a provider says the daily allowance is spent, stop asking it. Keyed by
# endpoint identity rather than held as one number, because the quota belongs to
# an API key: with a failover pool, one provider running dry must not mark the
# others dead. Module-wide within a key, since every service shares the same
# credentials. Entries expire on their own once the reset time passes.
_quota_exhausted_until: dict[str, float] = {}

# How long to stay open when the provider does not tell us when it resets.
_DEFAULT_QUOTA_COOLDOWN = 900.0

_limiters: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Semaphore] = {}

# Hosts that meter each model on a key separately. On these a second model is a
# second budget, so it must not share the first one's breaker or semaphore.
_PER_MODEL_HOSTS = ("api.groq.com", "api.cerebras.ai")

# A 400 that means "no such model" rather than "bad request". Providers word it
# differently ("model_not_found", "does not exist", "has been decommissioned"),
# and only the body tells it apart from a malformed payload.
_MODEL_GONE_RE = re.compile(
    r"model[_\s-]?not[_\s-]?found|does not exist|decommissioned|"
    r"no longer (?:supported|available)|not a valid model|invalid model|unknown model",
    re.IGNORECASE,
)


def _quota_scope(base_url: str, scope: str | None) -> str:
    if scope in ("key", "model"):
        return scope
    return "model" if any(host in base_url for host in _PER_MODEL_HOSTS) else "key"


def _quota_key(base_url: str, api_key: str, model: str = "", scope: str | None = None) -> str:
    """Identity of the metered bucket.

    The credentials, plus the model on hosts that meter per model. Two models
    on one OpenRouter key share a daily quota, so they share a breaker; two
    models on one Groq key each have their own.
    """
    if _quota_scope(base_url, scope) == "model":
        return f"{base_url}|{api_key}|{model}"
    return f"{base_url}|{api_key}"


def _error_message(resp) -> str:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — the status line is the message then
        return resp.text[:200] if hasattr(resp, "text") else ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error or body)[:200]


def _is_model_gone(resp) -> bool:
    if resp.status_code == 404:
        return True
    return resp.status_code == 400 and bool(_MODEL_GONE_RE.search(_error_message(resp)))


def _quota_cooldown_remaining(key: str) -> float:
    """Seconds left on this endpoint's breaker, 0 when closed."""
    return max(0.0, _quota_exhausted_until.get(key, 0.0) - time.time())


def _open_quota_breaker(key: str, resp) -> None:
    """Trip the breaker from a 429 that names the daily limit.

    A per-minute 429 is worth retrying seconds later; a daily one is not worth
    retrying at all today, and the two are only distinguishable from the body.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a malformed body is not a daily limit
        return
    error = body.get("error") or {}
    message = str(error.get("message") or "")
    metadata = error.get("metadata") or {}
    source = str(metadata.get("limit_source") or "")
    if not _DAILY_LIMIT_RE.search(message) and "daily" not in source.lower():
        return

    now = time.time()
    until = now + _DEFAULT_QUOTA_COOLDOWN
    reset = (metadata.get("headers") or {}).get("X-RateLimit-Reset")
    if reset:
        try:
            # Milliseconds since the epoch, in the provider's own clock.
            until = float(reset) / 1000.0
        except (TypeError, ValueError):
            # Groq reports a duration ("2m59.56s") instead of a timestamp.
            # Reading it is the difference between standing this endpoint down
            # for three minutes and standing it down for the default fifteen.
            offset = _parse_duration(str(reset))
            until = now + (offset if offset is not None else _DEFAULT_QUOTA_COOLDOWN)
    if not now < until <= now + 26 * 3600:
        # A seconds-valued epoch divided by 1000 lands in 1970, and a past
        # timestamp would leave the breaker closed while looking set — the
        # quiet failure this whole guard exists to prevent. Anything outside a
        # plausible day is treated as "unknown".
        until = now + _DEFAULT_QUOTA_COOLDOWN
    _quota_exhausted_until[key] = max(_quota_exhausted_until.get(key, 0.0), until)
    logger.warning(
        "Daily quota exhausted for %s; skipping it for %.0f minutes",
        key.split("|")[0],
        _quota_cooldown_remaining(key) / 60,
    )


def reset_quota_breaker() -> None:
    """Test hook — the breakers are module state shared by every instance."""
    _quota_exhausted_until.clear()


def _limiter(key: str, size: int) -> asyncio.Semaphore:
    """Cap on concurrent upstream calls to one metered account.

    Keyed by quota bucket as well as event loop. A rate limit is measured per
    account, so two accounts are two budgets — sizing one shared budget by the
    most restrictive member would throttle every other provider to match it.
    The loop is part of the key because an ``asyncio`` primitive binds to the
    loop that first blocks on it, and the test suite runs each case in a fresh
    one.
    """
    ident = (asyncio.get_running_loop(), key)
    limiter = _limiters.get(ident)
    if limiter is None:
        limiter = asyncio.Semaphore(max(1, size))
        _limiters[ident] = limiter
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
        max_tokens: int | None = None,
        max_concurrency: int | None = None,
        extra_body: dict | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        label: str = "",
        quota_scope: str | None = None,
        json_mode: bool | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        # Human-readable name for logs. With ten endpoints configured, a log
        # line naming only the base_url is unreadable when several models share
        # one host.
        self.label = label or f"{self.base_url} ({self.model})"
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.timeout = timeout or settings.llm_timeout_seconds
        # Per-endpoint because rate limits are per account — see `_limiter`.
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.max_concurrency = max_concurrency or settings.llm_max_concurrency
        self.json_mode = settings.llm_json_mode if json_mode is None else json_mode
        self.extra_body = dict(extra_body if extra_body is not None else settings.llm_extra_body)
        # Injected only by tests; production always builds a real transport.
        self._transport = transport
        # Which metered bucket this instance draws on. Computed once: it is the
        # key the breaker is stored under and the pool skips on.
        self.quota_key = _quota_key(self.base_url, self.api_key, self.model, quota_scope)
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
        # A surface may ask for less than the endpoint's ceiling, never more.
        # This matters on a provider metered by tokens per minute, where
        # max_tokens is *reserved* against the budget: the categorizer's
        # twenty-token answer used to reserve 3,500, so six concurrent
        # categorizations exhausted an 8,000/minute allowance on their own.
        max_tokens = self.max_tokens
        surface_cap = current_budget().max_output_tokens
        if surface_cap is not None:
            max_tokens = min(max_tokens, surface_cap)
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if settings.llm_seed and temperature == 0:
            payload["seed"] = settings.llm_seed
        if "openrouter.ai" in self.base_url:
            # Reasoning models bill their scratchpad against max_tokens, and a
            # long one can consume the whole budget before the answer starts —
            # which surfaces as the scratchpad itself being returned as the
            # reply. OpenRouter can drop it server-side. Sent only there because
            # a stricter OpenAI-compatible server rejects unknown fields.
            payload["reasoning"] = {"exclude": True}
        if json_object and self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        # Endpoint-specific extras last, so configuration can tune a provider
        # without a code change — but never the fields that define *what* is
        # being asked, which are this method's whole job.
        for key, value in self.extra_body.items():
            if key in ("model", "messages", "stream"):
                continue
            if isinstance(value, dict) and isinstance(payload.get(key), dict):
                # Merge, so {"reasoning": {"effort": "low"}} keeps the
                # {"exclude": true} set above instead of replacing it.
                payload[key] = {**payload[key], **value}
            else:
                payload[key] = value
        return payload

    def _content(self, data: dict) -> str:
        # A 200 can still carry a provider-level error (OpenRouter does this when
        # an upstream model is out of capacity).
        if isinstance(data.get("error"), dict):
            raise LLMUnavailable(
                str(data["error"].get("message") or data["error"]),
                kind="bad_response",
                endpoint=self.label,
            )
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("no choices in response", kind="bad_response", endpoint=self.label)
        message = choices[0].get("message") or {}
        content = _strip_reasoning(message.get("content") or "")
        if choices[0].get("finish_reason") == "length":
            # The response was cut off mid-generation. For a reasoning model
            # that usually means the answer never started and what came back is
            # raw scratchpad, so this degrades to the reviewed fallback rather
            # than showing the model thinking out loud.
            #
            # ``bad_response``, not a health problem: the endpoint answered
            # promptly and correctly, the answer was just too long for the
            # budget. Benching it would take a working endpoint out of the pool
            # for a minute over a `max_tokens` mistake.
            raise LLMUnavailable(
                "response truncated before the answer completed",
                kind="bad_response",
                endpoint=self.label,
            )
        if not content.strip():
            # A 200, a normal finish reason, and nothing in it. Previously this
            # returned "" and the pool never learned anything failed — so the
            # next member was never tried, chat silently fell to its glossary
            # and the committee to rules, with no log line to explain either.
            raise LLMUnavailable("empty response body", kind="bad_response", endpoint=self.label)
        return content

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        json_object: bool = False,
    ) -> str:
        budget = current_budget()
        if not self.api_key:
            raise LLMUnavailable("no LLM_API_KEY configured", kind="bad_key", endpoint=self.label)
        cooldown = self.quota_cooldown_remaining()
        if cooldown > 0:
            # Fail here rather than at the provider: the committee makes one
            # call per ticker, and ten round trips to be told the same thing is
            # ninety seconds the user waits to reach the same rules-based plan.
            raise LLMUnavailable(
                "daily quota exhausted",
                kind="quota_exhausted",
                retry_after=cooldown,
                endpoint=self.label,
            )

        payload = self._payload(prompt, system, temperature, json_object)
        async with _limiter(self.quota_key, self.max_concurrency):
            return await self._post_with_retry(payload, budget.remaining())

    async def _post_with_retry(self, payload: dict, remaining: float | None = None) -> str:
        url = f"{self.base_url}/chat/completions"
        # Never hold a socket open past the request's own deadline. With a long
        # pool the global 45s timeout x 2 attempts x N members outlives even the
        # most generous client timeout, so the client hangs up while the server
        # is still spending quota on an answer nobody will read.
        timeout = self.timeout
        if remaining is not None:
            timeout = max(1.0, min(timeout, remaining))
        last: Exception | None = None

        for attempt in (1, 2):
            try:
                async with self._client(timeout) as client:
                    resp = await client.post(url, json=payload, headers=self._headers())

                if resp.status_code == 429:
                    _open_quota_breaker(self.quota_key, resp)
                    cooldown = self.quota_cooldown_remaining()
                    if cooldown > 0:
                        # Out for the day — retrying costs time and tells us
                        # nothing new.
                        raise LLMUnavailable(
                            "daily quota exhausted",
                            kind="quota_exhausted",
                            retry_after=cooldown,
                            endpoint=self.label,
                        )
                    wait = _retry_after(resp)
                    if attempt == 1 and wait <= _MAX_RETRY_WAIT:
                        logger.warning("LLM rate limited; retrying in %.1fs", wait)
                        await asyncio.sleep(wait)
                        continue
                    # Past the in-member cap. The wait is NOT discarded — it
                    # rides out on the exception so the pool can decide whether
                    # to sit it out. The cap stays low here because this sleep
                    # happens with the concurrency semaphore held, where a long
                    # wait would starve every sibling call; a long wait belongs
                    # to the pool, outside the semaphore.
                    raise LLMUnavailable(
                        f"rate limited (429), retry in {wait:.0f}s",
                        kind="rate_limited",
                        retry_after=wait,
                        endpoint=self.label,
                    )

                if resp.status_code in (401, 403):
                    # A rejected key does not fix itself. Retrying it every
                    # minute forever is how a dead endpoint keeps costing
                    # latency on every single request.
                    raise LLMUnavailable(
                        f"endpoint rejected the API key ({resp.status_code})",
                        kind="bad_key",
                        endpoint=self.label,
                    )

                if _is_model_gone(resp):
                    # Checked before the JSON-mode retry: a retired model would
                    # otherwise be asked twice and then reported as a transport
                    # blip. Logged as an error because it needs a config change,
                    # not patience.
                    detail = _error_message(resp)
                    logger.error(
                        "LLM model %r at %s is gone (%s): %s — update LLM_MODEL/LLM_FALLBACKS",
                        self.model,
                        self.base_url,
                        resp.status_code,
                        detail,
                    )
                    raise LLMUnavailable(
                        f"model not available ({resp.status_code}): {detail}",
                        kind="model_gone",
                        endpoint=self.label,
                    )

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
        raise LLMUnavailable(
            str(last),
            kind="transport" if isinstance(last, httpx.HTTPError) else "bad_response",
            endpoint=self.label,
        ) from last

    def quota_cooldown_remaining(self) -> float:
        """Seconds until this endpoint's daily quota resets, 0 when it has not
        been reported exhausted. Public so the pool can skip a spent member
        without a round trip, and so tests need not reach into module state."""
        return _quota_cooldown_remaining(self.quota_key)

    def saturated(self) -> bool:
        """True when every concurrency slot on this account is in use, so a new
        call would queue behind the ones in flight. The pool reads it to send
        overflow to an idle member instead of waiting on a slow one."""
        return _limiter(self.quota_key, self.max_concurrency).locked()

    # --- health -----------------------------------------------------------

    async def is_available(self) -> bool:
        """Probe ``/models``, which is free and does not touch the chat quota.

        Cached briefly: the chat, committee and deploy endpoints each probe
        before working, and three round trips per user action is a poor trade
        for a value that changes rarely.
        """
        if not self.api_key or not settings.ai_enabled:
            return False
        if self.quota_cooldown_remaining() > 0:
            # /models answers 200 with the chat quota spent, so the probe alone
            # cannot see this.
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


#: Providers spell "come back later" three different ways in the same family of
#: headers: plain seconds, an HTTP-date, and a Go-style duration. Groq uses the
#: last (`"2m59.56s"`), which parsed as neither and silently became the default.
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)")
_DURATION_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def _parse_duration(value: str) -> float | None:
    """Seconds from a Go-style duration like ``2m59.56s``, or None."""
    matches = _DURATION_RE.findall(value.strip())
    if not matches:
        return None
    return sum(float(amount) * _DURATION_UNITS[unit] for amount, unit in matches)


def _retry_after(resp: httpx.Response) -> float:
    """How long the provider says to wait, in seconds.

    Tries every spelling before falling back, because the fallback (2s) is
    usually wrong in the expensive direction: a member that actually needs 21
    seconds gets asked again after 2, fails, and is benched.
    """
    for header in ("Retry-After", "X-RateLimit-Reset-Requests", "X-RateLimit-Reset-Tokens"):
        raw = resp.headers.get(header, "").strip()
        if not raw:
            continue
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        parsed = _parse_duration(raw)
        if parsed is not None:
            return max(0.0, parsed)
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            continue
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    return 2.0


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)
