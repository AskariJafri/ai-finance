"""SQLAlchemy ORM models (Phase 1 subset).

Design notes
------------
* Money is stored as **integer minor units** (cents) in ``BigInteger`` columns
  plus an ISO currency code — never floats — to avoid rounding error.
* Amount sign convention follows Plaid: **positive = outflow (spending)**,
  **negative = inflow (income/refund)** for depository accounts.
* ``category_source`` records *how* a transaction was categorized
  (plaid / rule / ai / user) which drives the learning loop and lets the UI
  show confidence.
* Later-phase tables (Budget, Goal, Investment, NetWorthSnapshot, AIInsight,
  Notification, AuditLog, …) attach to these via ``user_id`` when their phase
  lands; the schema here is intentionally normalized to extend cleanly.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime as datetime_type

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.enums import (
    BrokerageConnectionStatus,
    CategoryGroup,
    CategorySource,
    OrderSide,
    PlaidItemStatus,
    ProposedOrderStatus,
    RuleMatchType,
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    plaid_items: Mapped[list[PlaidItem]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    accounts: Mapped[list[Account]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_categories_user_slug"),)

    # NULL user_id => system/default category shared across users.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(80), index=True)
    group: Mapped[CategoryGroup] = mapped_column(
        SAEnum(CategoryGroup, native_enum=False, length=20), default=CategoryGroup.EXPENSE
    )
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # 50/30/20 bucket for EXPENSE categories: 'needs' | 'wants' | None. This is
    # the authoritative source now that users can create their own categories —
    # ``domain.budget_rule.NEEDS_SLUGS`` survives only as a legacy fallback.
    # NULL for income/transfer groups, which are not spend buckets at all.
    budget_bucket: Mapped[str | None] = mapped_column(String(10), nullable=True)


class Merchant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "merchants"

    # Normalized (uppercased, punctuation-stripped) merchant key for matching.
    normalized_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    default_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )


class PlaidItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plaid_items"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plaid_item_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    institution_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    institution_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Fernet-encrypted access token — never stored in plaintext.
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    # Cursor for incremental transactions/sync.
    transactions_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PlaidItemStatus] = mapped_column(
        SAEnum(PlaidItemStatus, native_enum=False, length=20),
        default=PlaidItemStatus.ACTIVE,
    )

    user: Mapped[User] = relationship(back_populates="plaid_items")
    accounts: Mapped[list[Account]] = relationship(
        back_populates="plaid_item", cascade="all, delete-orphan"
    )


class Account(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plaid_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plaid_items.id", ondelete="CASCADE"), index=True
    )
    plaid_account_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    official_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mask: Mapped[str | None] = mapped_column(String(20), nullable=True)
    type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subtype: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    current_balance_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    available_balance_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user: Mapped[User] = relationship(back_populates="accounts")
    plaid_item: Mapped[PlaidItem] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Transaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transactions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    # Unique Plaid id enables idempotent dedup on repeated syncs.
    plaid_transaction_id: Mapped[str | None] = mapped_column(
        String(120), unique=True, index=True, nullable=True
    )
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(400))
    merchant_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)  # + outflow, - inflow
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    date: Mapped[date_type] = mapped_column(Date, index=True)
    pending: Mapped[bool] = mapped_column(Boolean, default=False)

    category_source: Mapped[CategorySource] = mapped_column(
        SAEnum(CategorySource, native_enum=False, length=20),
        default=CategorySource.UNCATEGORIZED,
    )
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # True => value-neutral movement between the user's own accounts (including
    # a credit-card payment). Excluded from BOTH spending and income.
    #
    # It has to be a persisted flag rather than a query-time predicate: the
    # outflow leg of a card payment (depository, positive amount) is
    # structurally identical to a debit-card purchase, so no per-row SQL
    # predicate can tell them apart. Derived at write time from Plaid's
    # personal_finance_category, then text rules, then transfer-pair matching;
    # a user correction into a TRANSFER-group category sets it and
    # category_source=USER pins it against re-sync.
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # Both legs of a matched transfer share this id, so the UI can explain the
    # flag ("this is the other side of that payment") instead of just hiding a
    # row from the totals.
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)

    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped[User] = relationship(back_populates="transactions")
    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()
    merchant: Mapped[Merchant | None] = relationship()


class CategoryRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A merchant/keyword -> category rule.

    Seed rules ship with the app (``source=SEED``); every user correction
    creates or promotes a ``USER_CORRECTION`` rule, which is what makes the
    categorizer *learn*. Higher ``priority`` wins; user corrections outrank
    seed rules.
    """

    __tablename__ = "category_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "match_type", "pattern", name="uq_rule_user_type_pattern"),
    )

    # NULL user_id => global seed rule applied to everyone.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    match_type: Mapped[RuleMatchType] = mapped_column(
        SAEnum(RuleMatchType, native_enum=False, length=20), default=RuleMatchType.MERCHANT
    )
    pattern: Mapped[str] = mapped_column(String(200), index=True)  # normalized
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(30), default="seed")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)

    category: Mapped[Category] = relationship()


class PlaidCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-user Plaid API credentials, entered in-app via Settings.

    The ``secret`` is Fernet-encrypted at rest (same as access tokens). When a
    user has stored credentials, the Plaid client is built from them; otherwise
    the service falls back to environment credentials or the mock client. This
    lets a user bring their own Plaid keys without touching server config.
    """

    __tablename__ = "plaid_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    client_id: Mapped[str] = mapped_column(String(120))
    secret_encrypted: Mapped[str] = mapped_column(Text)
    plaid_env: Mapped[str] = mapped_column(String(20), default="sandbox")


class UserSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-user app preferences (one row per user).

    ``monthly_income_override_cents`` replaces the auto-detected monthly income
    in the 50/30/20 budget rule when set; NULL means "auto-detect".
    """

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    monthly_income_override_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Un-invested cash in the brokerage account (CAD), used by the rebalancer.
    # Overwritten from the broker once a brokerage account is linked.
    invest_cash_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # SnapTrade registration. Per *user*, not per account — one registration
    # can front several linked brokerage accounts, so it lives here rather than
    # being duplicated onto every BrokerageConnection row.
    snaptrade_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Fernet-encrypted; grants ongoing access to the linked accounts.
    snaptrade_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)


class Holding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A position the user holds at their own broker.

    Two provenances, distinguished by ``is_synced``:

    * **Manual** — user-entered, priced from public market data. Still the only
      option for brokers SnapTrade does not cover.
    * **Synced** — mirrored from a linked brokerage account via
      ``BrokerageConnection``. Overwritten on every sync, so manual edits to a
      synced holding would be silently discarded and are rejected instead.

    ``symbol`` is a Yahoo ticker, so Canadian listings work (e.g. "VFV.TO",
    priced in CAD).
    """

    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_holdings_user_symbol"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    qty: Mapped[float] = mapped_column(Float)
    avg_cost_cents: Mapped[int] = mapped_column(BigInteger)  # per share, native currency
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # True when this row is mirrored from a linked brokerage account.
    is_synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # What the broker itself said this position was worth, in the position's
    # own currency, and when it said so. The app otherwise re-derives value from
    # cached daily closes at its own FX rate, which is two sources of drift away
    # from the figure on the user's brokerage screen. Null for manual holdings
    # and for synced rows written before this column existed.
    broker_value_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    broker_valued_at: Mapped[datetime_type | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # What this position cost in CAD, fixed at the rate that applied when each
    # lot was bought — a *book* value, which by definition stops moving.
    #
    # ``avg_cost_cents`` is per share in the listing's currency, so converting
    # it for display used today's rate on both sides of the subtraction and the
    # FX cancelled out: the app reported a USD return under a CAD label, while
    # the brokerage statement reported the real CAD return including the
    # currency move. For a Canadian holding US stocks those are different
    # numbers, and neither the purchase-date rate nor a CAD book value was
    # anywhere in the schema to reconcile them.
    #
    # Maintained incrementally on sync (a buy adds its cost at that day's rate,
    # a sell reduces the book proportionally), so it is exact from the first
    # trade after this column existed. NULL means the basis predates it and the
    # CAD return is still an estimate — reported as such rather than implied.
    book_value_cad_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # True when any part of that book was converted at a rate we *assumed*
    # rather than one observed when the shares were bought — a position that
    # already existed the first time it was synced, or a cost typed in by hand
    # with no purchase date attached. The value still stops drifting, which is
    # the point; but calling an assumption a book value would be the same
    # mistake as the bug it replaces, so the row says which it is. Sticky: once
    # part of a basis is assumed, adding to the position does not make it exact.
    book_value_estimated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WatchlistItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A ticker the user tracks without holding it — shown with a live quote.

    ``symbol`` is a Yahoo ticker, so Canadian listings ("VFV.TO") work alongside
    US ones. One row per (user, symbol).
    """

    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class AnalysisSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A saved AI-committee run on a ticker. ``payload`` holds the full result
    (each agent's take + indicators); the top columns make listing cheap."""

    __tablename__ = "analysis_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(8))  # BUY | SKIP | HOLD
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(String(600), default="")
    source: Mapped[str] = mapped_column(String(8), default="ai")  # ai | rules
    payload: Mapped[dict] = mapped_column(JSON)


class PaperAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cash balance for the paper-trading sandbox (CAD). Seeded on first use."""

    __tablename__ = "paper_accounts"
    __table_args__ = (UniqueConstraint("user_id", name="uq_paper_account_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    cash_cents: Mapped[int] = mapped_column(BigInteger, default=10_000_000)  # $100k


class PaperPosition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A holding in the paper-trading sandbox (fake money). Separate from real
    recommend-only ``Holding`` rows. ``opened_by`` = 'user' or 'autotrader'."""

    __tablename__ = "paper_positions"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_paper_pos_user_symbol"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    qty: Mapped[float] = mapped_column(Float)
    avg_price_cents: Mapped[int] = mapped_column(BigInteger)  # per share, CAD
    opened_by: Mapped[str] = mapped_column(String(12), default="user")


class PaperTrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable log of a paper buy/sell."""

    __tablename__ = "paper_trades"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    qty: Mapped[float] = mapped_column(Float)
    price_cents: Mapped[int] = mapped_column(BigInteger)  # per share, CAD
    source: Mapped[str] = mapped_column(String(12), default="user")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AutoTraderConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-user auto-trader settings. Defaults are SAFE: disabled + dry-run."""

    __tablename__ = "autotrader_configs"
    __table_args__ = (UniqueConstraint("user_id", name="uq_autotrader_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    max_positions: Mapped[int] = mapped_column(default=5)
    per_trade_cents: Mapped[int] = mapped_column(BigInteger, default=100_000)  # $1000
    universe: Mapped[str] = mapped_column(String(400), default="")  # CSV of tickers


class AutoTradeRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One scan of the universe. ``decisions`` holds the per-ticker calls."""

    __tablename__ = "autotrade_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    scanned: Mapped[int] = mapped_column(default=0)
    executed: Mapped[int] = mapped_column(default=0)
    summary: Mapped[str] = mapped_column(String(400), default="")
    decisions: Mapped[dict] = mapped_column(JSON)


class PriceBar(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Daily close cache (global market data, shared across users — no user_id).

    ``close`` is a Float, deliberately breaking the integer-cents convention:
    this is market data, not user money — dividend/split-adjusted closes are
    fractional and feed float return math directly. Also holds the USDCAD FX
    rate under symbol "USDCAD".
    """

    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_price_bars_symbol_date"),)

    symbol: Mapped[str] = mapped_column(String(12), index=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float)
    adjustment: Mapped[str] = mapped_column(String(10), default="all")


class NewsSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One day's company-news pull for one symbol.

    Global market data — no user_id, exactly like ``price_bars``: the news
    about AAPL is the same news for every user, and making it per-user would
    multiply a metered API call by the user count.

    One row per (symbol, day) with the articles in ``payload``, rather than a
    row per article. The state that MUST be cached is "we asked and the
    provider had nothing" — a row-per-article table cannot express it, and the
    tickers that produce it (TSX listings, which the free tier barely covers)
    are exactly the ones a wide scan would otherwise re-ask about on every
    single run, forever.
    """

    __tablename__ = "news_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "as_of", name="uq_news_snapshots_symbol_as_of"),)

    symbol: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[date_type] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(16), default="finnhub")
    # covered | none | fund. "none" is a real, cacheable answer and is NEVER
    # evidence of good news — the guardrail only fires on "covered". "error"
    # is deliberately never stored: a transient 500 must not lock in a day of
    # silence for a symbol.
    coverage: Mapped[str] = mapped_column(String(12), default="covered")
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    # Comma-joined flag keys, denormalized so the auto-trader guardrail is a
    # single column read rather than a JSON parse per symbol.
    flags: Mapped[str] = mapped_column(String(200), default="")
    worst_severity: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MacroBriefCache(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The daily world/market read, fetched ONCE for the whole application.

    This row is the main cost control in the research layer: every user, every
    surface and every ticker on a given day share it, so a 65-ticker scan
    costs one web search at most and usually zero.

    Named ``MacroBriefCache`` because ``MacroBrief`` is the analytics DTO in
    app/analytics/decision_context.py; the two are deliberately distinct.
    """

    __tablename__ = "macro_briefs"
    __table_args__ = (UniqueConstraint("as_of", "topic", name="uq_macro_briefs_as_of_topic"),)

    as_of: Mapped[date_type] = mapped_column(Date, index=True)
    topic: Mapped[str] = mapped_column(String(24), default="markets")
    source: Mapped[str] = mapped_column(String(16), default="tavily")
    regime: Mapped[str] = mapped_column(String(12), default="mixed")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class PortfolioTarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Target allocation weights the rebalance proposer compares against."""

    __tablename__ = "portfolio_targets"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_targets_user_symbol"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(12))
    weight: Mapped[float] = mapped_column(Float)  # 0..1; service validates sum == 1


class ProposedOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Recommend-only order flow: the engine proposes, the user approves.

    Approval is always a human action — no engine path may set APPROVED. Once
    approved, the order is submitted to the linked broker through the SnapTrade
    gateway; without a linked account the user marks it executed by hand and
    the flow degrades to its original advisory behaviour.

    ``client_order_id`` sent to the broker is str(self.id) so re-approving
    after a network failure cannot double-submit. Fractional shares only work
    for market orders via ``notional_cents``; sells and limit orders use whole
    ``qty``.
    """

    __tablename__ = "proposed_orders"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))  # rebalance | dca | manual
    symbol: Mapped[str] = mapped_column(String(12))
    side: Mapped[OrderSide] = mapped_column(SAEnum(OrderSide, native_enum=False, length=10))
    order_type: Mapped[str] = mapped_column(String(10), default="market")
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    limit_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    est_value_cents: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[ProposedOrderStatus] = mapped_column(
        SAEnum(ProposedOrderStatus, native_enum=False, length=20),
        default=ProposedOrderStatus.PROPOSED,
    )
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Account the order was routed to; null for hand-executed orders.
    brokerage_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class BrokerageConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A brokerage account linked through SnapTrade.

    Wealthsimple and Questrade publish no API of their own; SnapTrade fronts
    them and is the only path here that both reads positions and places orders
    without this app ever handling broker credentials — the user authorizes on
    SnapTrade's portal.

    The credentials that grant access (``snaptrade_user_id`` and the encrypted
    user secret) live on ``UserSettings``, since one registration fronts every
    account the user links.
    """

    __tablename__ = "brokerage_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "brokerage_account_id", name="uq_brokerage_user_account"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    brokerage_account_id: Mapped[str] = mapped_column(String(120))
    account_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    institution_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    number_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    cash_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_value_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # False when the broker granted read-only access, which makes every order
    # submission fail fast with a clear message instead of at the broker.
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[BrokerageConnectionStatus] = mapped_column(
        SAEnum(BrokerageConnectionStatus, native_enum=False, length=20),
        default=BrokerageConnectionStatus.ACTIVE,
    )
    last_synced_at: Mapped[datetime_type | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AIInsight(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cached LLM output (e.g. monthly advice) — generating is slow on a local
    model, so successful generations are stored per (user, kind, period)."""

    __tablename__ = "ai_insights"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "period", name="uq_ai_insights_user_kind_period"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    period: Mapped[str] = mapped_column(String(7))  # "YYYY-MM"
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ChatSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One conversation thread with the finance assistant.

    ``title`` is derived from the first user message (no LLM call) so the
    history list is readable without generating a summary.
    """

    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120), default="New chat")

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single turn. ``role`` is 'user' or 'assistant'.

    ``source`` records how an assistant turn was produced ('ai' | 'glossary' |
    'fallback') so the UI can be honest about when the model was unavailable
    and the reply came from the hand-written explainers instead.
    """

    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Which context blocks were injected for this answer, e.g.
    # ["spending", "portfolio", "glossary:sharpe"] — lets the UI show
    # "answered using your data" and makes bad answers debuggable.
    context_used: Mapped[list | None] = mapped_column(JSON, nullable=True)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
