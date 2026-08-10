"""Finance chat assistant: grounding, degradation ladder, thread isolation."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.ai.llm.base import LLMProvider
from app.core.config import settings as app_settings
from app.db.models import Account, PlaidItem, Transaction
from app.integrations.marketdata.mock_client import MockMarketData
from app.repositories.categories import CategoryRepository
from app.repositories.users import UserRepository
from app.schemas.invest import HoldingIn
from app.services import glossary
from app.services.chat_service import ChatService
from app.services.invest_service import InvestService
from tests.test_advice import FailingLLM, FakeLLM


class RecordingLLM(FakeLLM):
    """FakeLLM that keeps the last prompt so grounding can be asserted."""

    def __init__(self, response: str = "Here's what your numbers say.") -> None:
        super().__init__(response)
        self.last_prompt: str | None = None
        self.last_system: str | None = None

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False) -> str:
        self.last_prompt = prompt
        self.last_system = system
        return await super().complete(prompt, system=system, temperature=temperature)


async def _user_with_money(session) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"c{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    item = PlaidItem(
        user_id=user.id,
        plaid_item_id=f"i-{uuid.uuid4().hex[:8]}",
        access_token_encrypted="enc",
    )
    session.add(item)
    await session.flush()
    account = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"a-{uuid.uuid4().hex[:8]}",
        name="Chequing",
        current_balance_cents=4200_00,
    )
    session.add(account)
    await session.flush()
    slugs = await CategoryRepository(session).ensure_seed()
    today = date.today()
    session.add_all(
        [
            Transaction(
                user_id=user.id,
                account_id=account.id,
                name="pay",
                amount_cents=-3000_00,
                date=today,
                category_id=slugs["salary"].id,
            ),
            Transaction(
                user_id=user.id,
                account_id=account.id,
                name="rent",
                amount_cents=1500_00,
                date=today,
                category_id=slugs["rent"].id,
            ),
        ]
    )
    await session.commit()
    return user.id


def _service(session, llm: LLMProvider) -> ChatService:
    return ChatService(session, llm, MockMarketData())


@pytest.mark.asyncio
async def test_reply_is_grounded_in_the_users_own_figures(seeded_session):
    user_id = await _user_with_money(seeded_session)
    llm = RecordingLLM()
    svc = _service(seeded_session, llm)

    thread, user_msg, reply = await svc.send(
        user_id, message="How am I doing this month?", session_id=None
    )

    assert reply.source == "ai"
    assert reply.content == "Here's what your numbers say."
    assert user_msg.role == "user"
    # The prompt carries SQL-computed figures, not a request to invent them.
    assert "YOUR DATA" in llm.last_prompt
    assert "$4,200.00" in llm.last_prompt  # account balance
    assert "cashflow" in reply.context_used
    # The system prompt forbids inventing numbers and placing trades.
    assert "never invent" in llm.last_system.lower()
    # A thread is created and named from the first question.
    assert thread.title.startswith("How am I doing")


@pytest.mark.asyncio
async def test_portfolio_context_reaches_the_prompt(seeded_session, monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)
    user_id = await _user_with_money(seeded_session)
    invest = InvestService(seeded_session, MockMarketData())
    await invest.upsert_holding(user_id, HoldingIn(symbol="VFV.TO", qty=100, avg_cost=60))
    await invest.sync(user_id)

    llm = RecordingLLM()
    reply = (
        await _service(seeded_session, llm).send(
            user_id, message="Is my portfolio too risky?", session_id=None
        )
    )[2]

    assert "INVESTMENT PORTFOLIO" in llm.last_prompt
    assert "VFV.TO" in llm.last_prompt
    assert "portfolio" in reply.context_used


@pytest.mark.asyncio
async def test_positions_are_ranked_by_size_before_truncation(seeded_session, monkeypatch):
    """The prompt has room for 8 positions, so the 8 that matter must survive.

    Holdings come back sorted by symbol, which would otherwise truncate
    alphabetically and drop the largest holding purely because of its ticker.
    """
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)
    monkeypatch.setattr("app.services.finance_context._MAX_POSITIONS", 2)
    user_id = await _user_with_money(seeded_session)
    invest = InvestService(seeded_session, MockMarketData())
    # ZAG.TO sorts last alphabetically but dwarfs the others by value, so an
    # unsorted truncation would drop the one position worth mentioning.
    for symbol, qty in [
        ("ZAG.TO", 1000),
        ("AAPL", 0.01),
        ("VFV.TO", 0.01),
        ("XEQT.TO", 0.01),
    ]:
        await invest.upsert_holding(user_id, HoldingIn(symbol=symbol, qty=qty, avg_cost=10))
    await invest.sync(user_id)

    llm = RecordingLLM()
    await _service(seeded_session, llm).send(
        user_id, message="How is my portfolio doing?", session_id=None
    )

    assert "ZAG.TO" in llm.last_prompt
    assert llm.last_prompt.index("ZAG.TO") < llm.last_prompt.index("AAPL")
    # The truncated tail is still accounted for, so the totals stay explicable.
    assert "and 2 smaller positions worth" in llm.last_prompt


@pytest.mark.asyncio
async def test_include_finances_false_keeps_data_out_of_the_prompt(seeded_session):
    user_id = await _user_with_money(seeded_session)
    llm = RecordingLLM()

    reply = (
        await _service(seeded_session, llm).send(
            user_id,
            message="What is a limit order?",
            session_id=None,
            include_finances=False,
        )
    )[2]

    assert "$4,200.00" not in llm.last_prompt
    assert "cashflow" not in reply.context_used


@pytest.mark.asyncio
async def test_definition_question_injects_the_reviewed_explainer(seeded_session):
    user_id = await _user_with_money(seeded_session)
    llm = RecordingLLM()

    reply = (
        await _service(seeded_session, llm).send(
            user_id, message="What is the Sharpe ratio?", session_id=None
        )
    )[2]

    assert "REFERENCE" in llm.last_prompt
    assert "Sharpe Ratio" in llm.last_prompt
    assert "glossary:sharpe" in reply.context_used


@pytest.mark.asyncio
async def test_llm_down_still_answers_definition_questions(seeded_session):
    user_id = await _user_with_money(seeded_session)
    svc = _service(seeded_session, FailingLLM())

    reply = (await svc.send(user_id, message="explain max drawdown", session_id=None))[2]

    assert reply.source == "glossary"
    assert "Maximum Drawdown" in reply.content
    assert reply.model is None


@pytest.mark.asyncio
async def test_llm_down_with_no_matching_term_says_so(seeded_session):
    user_id = await _user_with_money(seeded_session)
    svc = _service(seeded_session, FailingLLM())

    reply = (await svc.send(user_id, message="how am I doing?", session_id=None))[2]

    assert reply.source == "fallback"
    assert "isn't reachable" in reply.content


@pytest.mark.asyncio
async def test_ai_disabled_never_calls_the_llm(seeded_session, monkeypatch):
    user_id = await _user_with_money(seeded_session)
    llm = RecordingLLM()
    monkeypatch.setattr("app.services.chat_service.settings.ai_enabled", False)

    reply = (
        await _service(seeded_session, llm).send(user_id, message="what is beta?", session_id=None)
    )[2]

    assert llm.calls == 0
    assert reply.source == "glossary"


@pytest.mark.asyncio
async def test_history_is_replayed_and_scoped_to_the_thread(seeded_session):
    user_id = await _user_with_money(seeded_session)
    llm = RecordingLLM()
    svc = _service(seeded_session, llm)

    thread, _, _ = await svc.send(user_id, message="What is beta?", session_id=None)
    await svc.send(user_id, message="And why does it matter?", session_id=thread.id)

    assert "EARLIER IN THIS CONVERSATION" in llm.last_prompt
    assert "What is beta?" in llm.last_prompt
    # The new question must not be duplicated into the history block.
    assert llm.last_prompt.count("And why does it matter?") == 1

    messages = await svc.messages(user_id, thread.id)
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]

    # A fresh thread starts with no history.
    await svc.send(user_id, message="Unrelated question", session_id=None)
    assert "EARLIER IN THIS CONVERSATION" not in llm.last_prompt


@pytest.mark.asyncio
async def test_threads_are_private_to_their_owner(seeded_session):
    owner = await _user_with_money(seeded_session)
    stranger = await _user_with_money(seeded_session)
    svc = _service(seeded_session, RecordingLLM())

    thread, _, _ = await svc.send(owner, message="my money", session_id=None)

    assert await svc.messages(stranger, thread.id) is None
    assert await svc.send(stranger, message="hi", session_id=thread.id) is None
    assert await svc.delete_session(stranger, thread.id) is False
    assert await svc.messages(owner, thread.id) is not None


@pytest.mark.asyncio
async def test_sessions_list_and_delete(seeded_session):
    user_id = await _user_with_money(seeded_session)
    svc = _service(seeded_session, RecordingLLM())

    thread, _, _ = await svc.send(user_id, message="first question", session_id=None)
    sessions = await svc.list_sessions(user_id)
    assert [(s.id, n) for s, n in sessions] == [(thread.id, 2)]

    assert await svc.delete_session(user_id, thread.id) is True
    assert await svc.list_sessions(user_id) == []


@pytest.mark.asyncio
async def test_suggestions_reflect_available_data(seeded_session):
    user_id = await _user_with_money(seeded_session)
    svc = _service(seeded_session, RecordingLLM())

    labels = [s.label for s in await svc.suggestions(user_id)]
    assert "Where did my money go?" in labels
    # No holdings yet, so the invest starter is the getting-started one.
    assert "How do I start investing?" in labels
    assert "Explain my portfolio risk" not in labels


def test_glossary_matching_prefers_the_longest_phrase():
    assert glossary.match_terms("what is my value at risk?")[0] == "var_95"
    assert glossary.match_terms("explain dollar cost averaging")[0] == "dca"
    assert glossary.match_terms("is my portfolio diversified?")[0] == "hhi"
    assert glossary.match_terms("what's for dinner") == []


@pytest.mark.asyncio
async def test_every_starter_prompt_survives_the_llm_being_down(seeded_session):
    """The app puts these questions in the user's mouth, so the ones that are
    definitional must be answerable from reviewed copy alone.

    The two data questions ("where did my money go", "explain my risk") are
    excluded on purpose: with no model there is nothing honest to say beyond
    pointing at the tabs that still work.
    """
    user_id = await _user_with_money(seeded_session)
    svc = _service(seeded_session, FailingLLM())
    data_questions = {"Where did my money go?", "Explain my portfolio risk"}

    for starter in await svc.suggestions(user_id):
        if starter.label in data_questions:
            continue
        reply = (await svc.send(user_id, message=starter.prompt, session_id=None))[2]
        assert reply.source == "glossary", f"{starter.label!r} fell through to offline"


def test_money_terms_resolve_to_their_own_explainers():
    assert glossary.match_terms("what's the difference between a TFSA and an RRSP") == [
        "tfsa",
        "rrsp",
    ]
    assert set(glossary.match_terms("market order vs limit order — when do I use each?")) == {
        "market_order",
        "limit_order",
    }
    assert glossary.match_terms("am I hitting 50/30/20?")[0] == "rule_503020"
    assert glossary.match_terms("how big should my emergency fund be")[0] == ("emergency_fund")
    # Common words must not hijack a question that is really about something else.
    assert "needs_vs_wants" not in glossary.match_terms("what is beta?")


@pytest.mark.asyncio
async def test_api_round_trip(client, monkeypatch):
    # Pin the degraded path: a dev box may well have Ollama running, and this
    # test is about the HTTP surface, not about inference.
    monkeypatch.setattr(app_settings, "ai_enabled", False)

    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": f"chat{uuid.uuid4().hex[:6]}@test.com", "password": "pw123456"},
    )
    assert reg.status_code == 201, reg.text
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    status = await client.get("/api/v1/chat/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["suggestions"]
    assert status.json()["disclaimer"]

    sent = await client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "What is the Sharpe ratio?"},
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["reply"]["source"] == "glossary"
    assert "Sharpe" in body["reply"]["content"]

    session_id = body["session_id"]
    listed = await client.get("/api/v1/chat/sessions", headers=headers)
    assert [s["id"] for s in listed.json()] == [session_id]

    msgs = await client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=headers)
    assert [m["role"] for m in msgs.json()] == ["user", "assistant"]

    gone = await client.delete(f"/api/v1/chat/sessions/{session_id}", headers=headers)
    assert gone.status_code == 204
    assert (
        await client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=headers)
    ).status_code == 404
