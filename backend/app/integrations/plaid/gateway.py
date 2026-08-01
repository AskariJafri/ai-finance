"""Plaid gateway interface + framework-free DTOs.

Services depend only on this abstraction, never on ``plaid-python`` types.
That keeps the Plaid SDK at the edge of the system, makes the whole sync
pipeline unit-testable with ``MockPlaidClient``, and means swapping SDK
versions (or providers) touches exactly one adapter.

All monetary values crossing this boundary are already normalized to integer
**cents** with the Plaid sign convention (positive = outflow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PlaidAccount:
    account_id: str
    name: str
    official_name: str | None
    mask: str | None
    type: str | None
    subtype: str | None
    currency: str
    current_balance_cents: int
    available_balance_cents: int | None


@dataclass(frozen=True)
class PlaidTransaction:
    transaction_id: str
    account_id: str
    name: str
    merchant_name: str | None
    amount_cents: int  # positive = outflow
    currency: str
    date: date
    pending: bool
    plaid_category: str | None = None  # personal_finance_category.primary
    plaid_category_detailed: str | None = None  # personal_finance_category.detailed


@dataclass(frozen=True)
class PlaidExchangeResult:
    access_token: str
    item_id: str
    institution_id: str | None = None
    institution_name: str | None = None


@dataclass
class PlaidSyncResult:
    added: list[PlaidTransaction] = field(default_factory=list)
    modified: list[PlaidTransaction] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


@runtime_checkable
class PlaidGateway(Protocol):
    async def create_link_token(self, user_id: str) -> tuple[str, str | None]:
        """Return (link_token, expiration_iso)."""
        ...

    async def exchange_public_token(self, public_token: str) -> PlaidExchangeResult: ...

    async def get_accounts(self, access_token: str) -> list[PlaidAccount]: ...

    async def sync_transactions(self, access_token: str, cursor: str | None) -> PlaidSyncResult: ...
