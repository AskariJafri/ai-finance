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
from app.db.migration_utils import (
    add_column_if_missing,
    drop_column_if_present,
    rename_column_if_needed,
)
from app.db.models import BrokerageConnection

revision = "0007_brokerage_link"
down_revision = "0006_committee_paper"
branch_labels = None
depends_on = None

_TABLES = [BrokerageConnection.__table__]


# Every operation below is guarded: a fresh database is created from current ORM
# metadata by 0001's create_all, so it already carries broker_order_id and the
# columns added here, while a step-by-step database still has the old shape.
# See app/db/migration_utils.py.


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)

    # Alpaca was never wired up and does not serve Canadian residents, so the
    # column only ever held NULLs.
    rename_column_if_needed("proposed_orders", "alpaca_order_id", "broker_order_id")

    add_column_if_missing(
        "proposed_orders",
        sa.Column("brokerage_account_id", sa.String(length=120), nullable=True),
    )
    # Existing rows are all hand-entered positions.
    add_column_if_missing(
        "holdings",
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # SnapTrade registration lives per user, not per linked account.
    add_column_if_missing(
        "user_settings",
        sa.Column("snaptrade_user_id", sa.String(length=120), nullable=True),
    )
    add_column_if_missing(
        "user_settings", sa.Column("snaptrade_secret_encrypted", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    drop_column_if_present("user_settings", "snaptrade_secret_encrypted")
    drop_column_if_present("user_settings", "snaptrade_user_id")
    drop_column_if_present("holdings", "is_synced")
    drop_column_if_present("proposed_orders", "brokerage_account_id")
    rename_column_if_needed("proposed_orders", "broker_order_id", "alpaca_order_id")

    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(_TABLES)), checkfirst=True)
