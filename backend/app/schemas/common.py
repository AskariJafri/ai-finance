from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.db.models import Account, Category, Transaction
from app.domain.enums import CategoryGroup, CategorySource


def cents_to_dollars(cents: int) -> float:
    return round(cents / 100, 2)


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    group: CategoryGroup
    icon: str | None = None
    color: str | None = None
    is_system: bool
    budget_bucket: str | None = None

    @classmethod
    def from_model(cls, c: Category) -> CategoryOut:
        return cls(
            id=c.id,
            name=c.name,
            slug=c.slug,
            group=c.group,
            icon=c.icon,
            color=c.color,
            is_system=c.is_system,
            budget_bucket=c.budget_bucket,
        )


class AccountOut(BaseModel):
    id: uuid.UUID
    name: str
    official_name: str | None = None
    mask: str | None = None
    type: str | None = None
    subtype: str | None = None
    currency: str
    current_balance: float
    available_balance: float | None = None

    @classmethod
    def from_model(cls, a: Account) -> AccountOut:
        return cls(
            id=a.id,
            name=a.name,
            official_name=a.official_name,
            mask=a.mask,
            type=a.type,
            subtype=a.subtype,
            currency=a.currency,
            current_balance=cents_to_dollars(a.current_balance_cents),
            available_balance=(
                cents_to_dollars(a.available_balance_cents)
                if a.available_balance_cents is not None
                else None
            ),
        )


class TransactionOut(BaseModel):
    id: uuid.UUID
    name: str
    merchant_name: str | None = None
    amount: float  # dollars; positive = outflow, negative = inflow
    currency: str
    date: str
    pending: bool
    category: CategoryOut | None = None
    category_source: CategorySource
    category_confidence: float
    # Both mirror the server's spending/income guards
    # (repositories.transactions._flows_where / sum_income's liability check)
    # so a client-side total is provably the same predicate as the server's.
    is_transfer: bool
    account_type: str | None = None

    @classmethod
    def from_model(cls, t: Transaction) -> TransactionOut:
        return cls(
            id=t.id,
            name=t.name,
            merchant_name=t.merchant_name,
            amount=cents_to_dollars(t.amount_cents),
            currency=t.currency,
            date=t.date.isoformat(),
            pending=t.pending,
            category=CategoryOut.from_model(t.category) if t.category else None,
            category_source=t.category_source,
            category_confidence=t.category_confidence,
            is_transfer=t.is_transfer,
            account_type=t.account.type if t.account else None,
        )


class UpdateCategoryRequest(BaseModel):
    category_id: uuid.UUID
