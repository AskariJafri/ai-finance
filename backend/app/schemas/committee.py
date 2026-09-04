from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyzeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)


class AgentTakeOut(BaseModel):
    agent: str
    name: str
    stance: str
    confidence: float
    thesis: str


class CommitteeResult(BaseModel):
    id: str | None = None
    symbol: str
    price: float | None = None
    currency: str = "USD"
    action: str  # BUY | SKIP | HOLD
    confidence: float
    summary: str
    source: str  # ai | rules
    agents: list[AgentTakeOut]
    indicators: dict
    # How much the news/macro blocks moved the verdict, and what was in them.
    # Both additive: pre-research sessions replay with the defaults.
    news_weight: str = "absent"  # decisive | supporting | ignored | absent
    context: dict | None = None
    created_at: str | None = None
    disclaimer: str = (
        "Educational analysis from public market data — not investment advice. "
        "Runs against a paper (simulated) portfolio."
    )


class SessionSummary(BaseModel):
    id: str
    symbol: str
    action: str
    confidence: float
    source: str
    created_at: str
