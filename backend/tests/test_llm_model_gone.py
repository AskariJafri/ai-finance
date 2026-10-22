"""Retired models, per-model quotas, and the pool's self-report.

Free models are withdrawn without notice. Before ``model_gone`` existed, a
retired primary answered 404, was filed as a transport blip, benched for 30
seconds and re-dialled all day — every plan paid a round trip to a model that
no longer existed, and nothing in the logs said so.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai.llm.base import LLMUnavailable
from app.ai.llm.openai_compat import OpenAICompatProvider, _quota_key, reset_quota_breaker
from app.ai.llm.pool import LLMPool


def _resp(body: dict, status: int) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body,
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


def _always(resp: httpx.Response):
    calls = {"n": 0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return resp

    return _Client, calls


@pytest.fixture
def provider(monkeypatch):
    reset_quota_breaker()
    p = OpenAICompatProvider(base_url="https://example.test", model="m", api_key="k", label="stub")
    yield p
    reset_quota_breaker()


# --- classification --------------------------------------------------------
@pytest.mark.asyncio
async def test_a_404_is_a_gone_model_and_is_asked_once(provider, monkeypatch):
    Client, calls = _always(_resp({"error": {"message": "Function not found"}}, 404))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi", json_object=True)

    assert excinfo.value.kind == "model_gone"
    # Not retried as plain text, not retried as a transport error.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_a_400_naming_a_decommissioned_model_is_gone(provider, monkeypatch):
    body = {"error": {"message": "The model `m` has been decommissioned", "code": "model_decommissioned"}}
    Client, _ = _always(_resp(body, 400))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi", json_object=True)

    assert excinfo.value.kind == "model_gone"


@pytest.mark.asyncio
async def test_an_ordinary_400_is_not_mistaken_for_a_gone_model(provider, monkeypatch):
    Client, calls = _always(_resp({"error": {"message": "response_format not supported"}}, 400))
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())

    with pytest.raises(LLMUnavailable) as excinfo:
        await provider.complete("hi", json_object=True)

    assert excinfo.value.kind != "model_gone"
    assert calls["n"] == 2, "the JSON-mode retry still happens"


# --- quota scope -----------------------------------------------------------
def test_groq_models_on_one_key_are_separate_buckets():
    url = "https://api.groq.com/openai/v1"
    assert _quota_key(url, "k", "a") != _quota_key(url, "k", "b")


def test_openrouter_models_on_one_key_share_a_bucket():
    url = "https://openrouter.ai/api/v1"
    assert _quota_key(url, "k", "a") == _quota_key(url, "k", "b")


def test_an_explicit_scope_overrides_the_host_default():
    url = "https://openrouter.ai/api/v1"
    assert _quota_key(url, "k", "a", "model") != _quota_key(url, "k", "b", "model")
    groq = "https://api.groq.com/openai/v1"
    assert _quota_key(groq, "k", "a", "key") == _quota_key(groq, "k", "b", "key")


# --- the pool --------------------------------------------------------------
class Member:
    def __init__(self, model: str, *, answer: str | None = None, kind: str = "unknown"):
        self.model = model
        self.label = model
        self.answer = answer
        self.kind = kind
        self.calls = 0

    async def complete(self, prompt, **kwargs):
        self.calls += 1
        if self.answer is None:
            raise LLMUnavailable(f"{self.model} declined", kind=self.kind)
        return self.answer

    async def is_available(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_a_gone_model_sits_out_for_fifteen_minutes():
    gone = Member("gone", kind="model_gone")
    backup = Member("backup", answer="ok")
    pool = LLMPool([gone, backup])

    assert await pool.complete("hi") == "ok"
    assert await pool.complete("hi") == "ok"
    assert gone.calls == 1, "a retired model is not re-dialled on the next call"

    state = {m["label"]: m for m in pool.status()}
    assert state["gone"]["state"] == "model_gone"
    assert 890 <= state["gone"]["retry_in"] <= 900
    assert state["backup"] == {"label": "backup", "model": "backup", "state": "ok", "retry_in": 0}


@pytest.mark.asyncio
async def test_status_reports_a_spent_quota_from_the_member_breaker():
    class Spent(Member):
        def quota_cooldown_remaining(self) -> float:
            return 3600.0

    pool = LLMPool([Spent("dry"), Member("fresh", answer="ok")])

    state = {m["label"]: m for m in pool.status()}
    assert state["dry"]["state"] == "quota"
    assert state["dry"]["retry_in"] == 3600
    assert state["fresh"]["state"] == "ok"


def test_alternate_skips_the_same_weights_on_another_host():
    pool = LLMPool(
        [
            Member("openai/gpt-oss-120b"),
            Member("gpt-oss-120b"),
            Member("llama-3.3-70b-versatile"),
        ]
    )

    other = pool.alternate("openai/gpt-oss-120b")

    assert other is not None
    assert [m.model for m in other.members] == ["llama-3.3-70b-versatile"]


def test_alternate_is_none_with_only_one_model():
    assert LLMPool([Member("only")]).alternate("only") is None


def test_extra_body_merges_into_openrouter_reasoning_instead_of_replacing_it():
    p = OpenAICompatProvider(
        base_url="https://openrouter.ai/api/v1",
        model="m",
        api_key="k",
        extra_body={"reasoning": {"effort": "low"}},
    )

    payload = p._payload("hi", None, 0.0, False)

    assert payload["reasoning"] == {"exclude": True, "effort": "low"}
