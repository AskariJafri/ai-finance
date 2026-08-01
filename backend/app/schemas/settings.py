from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlaidCredentialIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    secret: str = Field(min_length=1, max_length=200)
    plaid_env: Literal["sandbox", "production"] = "sandbox"


class PlaidCredentialStatus(BaseModel):
    configured: bool
    source: Literal["user", "environment", "mock"]
    plaid_env: str
    client_id_masked: str | None = None


class BudgetSettings(BaseModel):
    # None = auto-detect income from deposits.
    monthly_income_override: float | None = Field(default=None, ge=0)
