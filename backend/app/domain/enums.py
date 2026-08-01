"""Domain enums — framework-free, shared across models, services and schemas."""

from __future__ import annotations

from enum import StrEnum


class CategoryGroup(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class CategorySource(StrEnum):
    """How a transaction's category was assigned (drives confidence + learning)."""

    UNCATEGORIZED = "uncategorized"
    PLAID = "plaid"
    RULE = "rule"
    AI = "ai"
    USER = "user"


class RuleMatchType(StrEnum):
    MERCHANT = "merchant"  # exact match on normalized merchant key
    KEYWORD = "keyword"  # substring match on normalized text


class PlaidItemStatus(StrEnum):
    ACTIVE = "active"
    LOGIN_REQUIRED = "login_required"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BrokerageConnectionStatus(StrEnum):
    ACTIVE = "active"
    LOGIN_REQUIRED = "login_required"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class ProposedOrderStatus(StrEnum):
    """Recommend-only trading: every order starts PROPOSED and moves only on
    explicit user action (approve/reject). SUBMITTED..FAILED mirror the
    broker-side lifecycle reported through the SnapTrade gateway."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    FILLED = "filled"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
