"""Committee: one LLM call must yield a full board, however ragged the reply.

The whole deliberation is a single request now, so a malformed or partial
response can no longer be absorbed by "the other five calls worked". These pin
that a user always sees five seats and a verdict.
"""

from __future__ import annotations

import json

import pytest

from app.ai.committee import ROLES, run_committee
from app.analytics.indicators import Indicators

QUOTE = {"price": 100.0, "currency": "CAD"}
IND = Indicators(
    last=100.0,
    sma20=99.0,
    sma50=95.0,
    rsi14=55.0,
    ret_1m=0.02,
    ret_3m=0.05,
    ret_6m=0.10,
    max_drawdown=-0.08,
    vol_annual=0.18,
    trend="up",
)


class ScriptedLLM:
    model = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_json_object: bool | None = None

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False) -> str:
        self.calls += 1
        self.last_json_object = json_object
        return self.reply

    async def is_available(self) -> bool:
        return True


def _full_reply(action: str = "BUY") -> str:
    return json.dumps(
        {
            "agents": [
                {
                    "agent": key,
                    "stance": "bullish",
                    "confidence": 0.7,
                    "thesis": f"{name} likes the trend.",
                }
                for key, name, _ in ROLES
            ],
            "action": action,
            "confidence": 0.8,
            "rationale": "Trend and momentum both support a starter position.",
        }
    )


@pytest.mark.asyncio
async def test_whole_committee_costs_one_call():
    llm = ScriptedLLM(_full_reply())
    result = await run_committee("VFV.TO", QUOTE, IND, llm)

    assert llm.calls == 1
    assert llm.last_json_object is True
    assert result["action"] == "BUY"
    assert result["source"] == "ai"
    assert [a["agent"] for a in result["agents"]] == [key for key, _, _ in ROLES]


@pytest.mark.asyncio
async def test_short_roster_is_padded_not_dropped():
    reply = json.dumps(
        {
            "agents": [
                {"agent": "research", "stance": "bearish", "confidence": 0.9, "thesis": "x"}
            ],
            "action": "SKIP",
            "confidence": 0.6,
            "rationale": "Momentum has rolled over.",
        }
    )
    result = await run_committee("VFV.TO", QUOTE, IND, ScriptedLLM(reply))

    assert len(result["agents"]) == len(ROLES)
    assert result["agents"][0]["stance"] == "bearish"
    # The seats that never spoke are neutral placeholders, not invented opinions.
    assert all(a["stance"] == "neutral" for a in result["agents"][1:])
    assert result["action"] == "SKIP"


@pytest.mark.asyncio
async def test_unlabelled_agents_map_by_position():
    reply = json.dumps(
        {
            "agents": [
                {"stance": "bullish", "confidence": 0.6, "thesis": f"seat {i}"}
                for i in range(len(ROLES))
            ],
            "action": "HOLD",
            "confidence": 0.5,
            "rationale": "Mixed but stable.",
        }
    )
    result = await run_committee("VFV.TO", QUOTE, IND, ScriptedLLM(reply))
    assert [a["agent"] for a in result["agents"]] == [key for key, _, _ in ROLES]
    assert all(a["stance"] == "bullish" for a in result["agents"])


@pytest.mark.asyncio
async def test_truncated_json_is_salvaged():
    truncated = _full_reply()[: _full_reply().rfind('"rationale"')].rstrip().rstrip(",")
    result = await run_committee("VFV.TO", QUOTE, IND, ScriptedLLM(truncated))

    # No rationale survived, so the verdict falls back to the transparent read.
    assert result["source"] == "rules"
    assert result["agents"][0]["stance"] == "bullish"


@pytest.mark.asyncio
async def test_unusable_reply_falls_back_to_rules_entirely():
    result = await run_committee("VFV.TO", QUOTE, IND, ScriptedLLM("I cannot help with that."))

    assert result["source"] == "rules"
    assert len(result["agents"]) == len(ROLES)
    assert "AI offline" in result["agents"][0]["thesis"]


@pytest.mark.asyncio
async def test_invalid_action_does_not_reach_the_client():
    reply = json.dumps(
        {
            "agents": [
                {"agent": key, "stance": "bullish", "confidence": 0.7, "thesis": "ok"}
                for key, _, _ in ROLES
            ],
            "action": "STRONG BUY",
            "confidence": 0.9,
            "rationale": "Very bullish.",
        }
    )
    result = await run_committee("VFV.TO", QUOTE, IND, ScriptedLLM(reply))

    assert result["action"] in {"BUY", "SKIP", "HOLD"}
    assert result["source"] == "rules"
