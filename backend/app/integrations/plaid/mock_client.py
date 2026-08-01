"""In-memory mock Plaid adapter for offline dev and tests.

Produces deterministic, sandbox-like accounts and transactions (dated into the
current month so the dashboard has data) without any network call. Includes an
unknown merchant so the AI categorization fallback gets exercised. Implements
cursor semantics: the first ``sync_transactions`` returns the seed batch, later
calls return nothing until state changes.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.integrations.plaid.gateway import (
    PlaidAccount,
    PlaidExchangeResult,
    PlaidGateway,
    PlaidSyncResult,
    PlaidTransaction,
)

_INITIAL_CURSOR = "mock-cursor-1"


def _mock_accounts(item_key: str) -> list[PlaidAccount]:
    return [
        PlaidAccount(
            account_id=f"{item_key}-checking",
            name="Mock Checking",
            official_name="Mock Bank Everyday Checking",
            mask="0000",
            type="depository",
            subtype="checking",
            currency="USD",
            current_balance_cents=842_355,
            available_balance_cents=812_355,
        ),
        PlaidAccount(
            account_id=f"{item_key}-credit",
            name="Mock Credit Card",
            official_name="Mock Bank Rewards Visa",
            mask="1111",
            type="credit",
            subtype="credit card",
            currency="USD",
            current_balance_cents=123_456,
            available_balance_cents=None,
        ),
    ]


def _mock_transactions(item_key: str) -> list[PlaidTransaction]:
    today = date.today()

    def d(days_ago: int) -> date:
        # Clamp into the current month so month-to-date dashboards always have
        # data regardless of what day of the month it is.
        return today.replace(day=max(1, today.day - days_ago))

    checking = f"{item_key}-checking"
    credit = f"{item_key}-credit"
    # (name, merchant_name, amount_dollars, account, days_ago, pfc_primary, pfc_detailed)
    #
    # PFC is populated on every row (including None -> None) so nothing in the
    # pipeline silently depends on personal_finance_category being absent — that
    # gap is exactly what let the dead isinstance(pfc, dict) guardrail hide for
    # so long. The credit-card payment carries BOTH legs: the outflow from
    # checking (positive, depository — indistinguishable from a debit purchase by
    # sign/account-type alone) and the matching inflow on the card (negative,
    # credit). Only PFC + text rules + pair-matching can tell the outflow leg
    # apart from real spending; this fixture is what regression-tests that.
    rows = [
        ("PAYROLL DIRECT DEPOSIT", "Payroll", -3200.00, checking, 2, "INCOME", "INCOME_WAGES"),
        (
            "PAYMENT TO MOCK VISA CARD",
            "Mock Bank",
            450.00,
            checking,
            3,
            "LOAN_PAYMENTS",
            "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
        ),
        (
            "PAYMENT - THANK YOU",
            None,
            -450.00,
            credit,
            3,
            "TRANSFER_IN",
            "TRANSFER_IN_ACCOUNT_TRANSFER",
        ),
        (
            "STARBUCKS #4821",
            "Starbucks",
            6.75,
            credit,
            1,
            "FOOD_AND_DRINK",
            "FOOD_AND_DRINK_COFFEE",
        ),
        (
            "TIM HORTONS 220 KING ST",
            "Tim Hortons",
            4.10,
            credit,
            3,
            "FOOD_AND_DRINK",
            "FOOD_AND_DRINK_COFFEE",
        ),
        (
            "UBER EATS",
            "Uber Eats",
            38.42,
            credit,
            4,
            "FOOD_AND_DRINK",
            "FOOD_AND_DRINK_FAST_FOOD",
        ),
        (
            "AMAZON.COM*RT4X9",
            "Amazon",
            74.99,
            credit,
            5,
            "GENERAL_MERCHANDISE",
            "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
        ),
        (
            "COSTCO WHOLESALE #128",
            "Costco",
            214.53,
            credit,
            6,
            "GENERAL_MERCHANDISE",
            "GENERAL_MERCHANDISE_SUPERSTORES",
        ),
        (
            "NETFLIX.COM",
            "Netflix",
            15.49,
            credit,
            7,
            "ENTERTAINMENT",
            "ENTERTAINMENT_TV_AND_MOVIES",
        ),
        (
            "SPOTIFY P1F2A",
            "Spotify",
            10.99,
            credit,
            8,
            "ENTERTAINMENT",
            "ENTERTAINMENT_MUSIC_AND_AUDIO",
        ),
        (
            "SHELL OIL 5723",
            "Shell",
            52.30,
            credit,
            9,
            "TRANSPORTATION",
            "TRANSPORTATION_GAS",
        ),
        (
            "UBER TRIP",
            "Uber",
            21.15,
            credit,
            10,
            "TRANSPORTATION",
            "TRANSPORTATION_TAXIS_AND_RIDE_SHARES",
        ),
        (
            "WHOLE FOODS MARKET",
            "Whole Foods",
            63.28,
            credit,
            11,
            "FOOD_AND_DRINK",
            "FOOD_AND_DRINK_GROCERIES",
        ),
        # Unknown merchant, no PFC -> exercises the AI categorization fallback.
        ("ACME ARTISAN BAKERY", "Acme Artisan Bakery", 12.40, credit, 2, None, None),
    ]
    return [
        PlaidTransaction(
            transaction_id=f"{item_key}-txn-{i}",
            account_id=account,
            name=name,
            merchant_name=merchant,
            amount_cents=int(round(amount * 100)),
            currency="USD",
            date=d(days_ago),
            pending=False,
            plaid_category=pfc_primary,
            plaid_category_detailed=pfc_detailed,
        )
        for i, (name, merchant, amount, account, days_ago, pfc_primary, pfc_detailed) in enumerate(
            rows
        )
    ]


class MockPlaidClient(PlaidGateway):
    def __init__(self) -> None:
        # access_token -> already-synced flag
        self._synced: dict[str, bool] = {}

    async def create_link_token(self, user_id: str) -> tuple[str, str | None]:
        return f"link-sandbox-mock-{uuid.uuid4().hex}", None

    async def exchange_public_token(self, public_token: str) -> PlaidExchangeResult:
        key = uuid.uuid4().hex[:12]
        return PlaidExchangeResult(
            access_token=f"access-mock-{key}",
            item_id=f"item-mock-{key}",
            institution_id="ins_mock",
            institution_name="Mock Bank",
        )

    def _item_key(self, access_token: str) -> str:
        return access_token.replace("access-mock-", "")

    async def get_accounts(self, access_token: str) -> list[PlaidAccount]:
        return _mock_accounts(self._item_key(access_token))

    async def sync_transactions(self, access_token: str, cursor: str | None) -> PlaidSyncResult:
        if cursor:  # already delivered the initial batch
            return PlaidSyncResult(next_cursor=cursor, has_more=False)
        self._synced[access_token] = True
        return PlaidSyncResult(
            added=_mock_transactions(self._item_key(access_token)),
            next_cursor=_INITIAL_CURSOR,
            has_more=False,
        )
