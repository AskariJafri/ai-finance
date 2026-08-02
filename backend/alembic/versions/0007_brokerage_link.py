"""Brokerage link via SnapTrade + live order execution

Adds ``brokerage_connections`` and the columns the order state machine needs to
route an approved order to a real broker. Renames ``alpaca_order_id`` to
``broker_order_id``: Alpaca was never wired up and does not serve Canadian
residents, so the column only ever held NULLs.

Revision ID: 0007_brokerage_link
Revises: 0006_committee_paper
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.base import Base
from app.db.models import BrokerageConnection

revision = "0007_brokerage_link"
down_revision = "0006_committee_paper"
branch_labels = None
depends_on = None

_TABLES = [BrokerageConnection.__table__]


def _columns(table: str) -> set[str]:
    """Column names currently present on ``table``."""
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


# Earlier revisions build their tables from live ORM metadata (0001's
# ``create_all``, and 0003/0006 with ``checkfirst=True``). On a *fresh* database
# that means the tables are created from today's models — already carrying
# ``broker_order_id`` and the columns added below — while a database that was
# migrated step by step still has the historical shape.
#
# So these operations have to be conditional. Renaming ``alpaca_order_id``
# unconditionally is what broke `alembic upgrade head` on a clean Postgres:
# the column had never existed there.


def _rename_if_needed(table: str, old: str, new: str) -> None:
    cols = _columns(table)
    if old in cols and new not in cols:
        op.alter_column(table, old, new_column_name=new)


def _add_if_missing(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _drop_if_present(table: str, name: str) -> None:
    if name in _columns(table):
        op.drop_column(table, name)


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)

    # Alpaca was never wired up and does not serve Canadian residents, so the
    # column only ever held NULLs.
    _rename_if_needed("proposed_orders", "alpaca_order_id", "broker_order_id")

    _add_if_missing(
        "proposed_orders",
        sa.Column("brokerage_account_id", sa.String(length=120), nullable=True),
    )
    # Existing rows are all hand-entered positions.
    _add_if_missing(
        "holdings",
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # SnapTrade registration lives per user, not per linked account.
    _add_if_missing(
        "user_settings",
        sa.Column("snaptrade_user_id", sa.String(length=120), nullable=True),
    )
    _add_if_missing(
        "user_settings", sa.Column("snaptrade_secret_encrypted", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    _drop_if_present("user_settings", "snaptrade_secret_encrypted")
    _drop_if_present("user_settings", "snaptrade_user_id")
    _drop_if_present("holdings", "is_synced")
    _drop_if_present("proposed_orders", "brokerage_account_id")
    _rename_if_needed("proposed_orders", "broker_order_id", "alpaca_order_id")

    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(_TABLES)), checkfirst=True)
