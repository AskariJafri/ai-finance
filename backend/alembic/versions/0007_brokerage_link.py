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


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)

    op.alter_column("proposed_orders", "alpaca_order_id", new_column_name="broker_order_id")
    op.add_column(
        "proposed_orders",
        sa.Column("brokerage_account_id", sa.String(length=120), nullable=True),
    )
    # Existing rows are all hand-entered positions.
    op.add_column(
        "holdings",
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # SnapTrade registration lives per user, not per linked account.
    op.add_column(
        "user_settings",
        sa.Column("snaptrade_user_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "user_settings", sa.Column("snaptrade_secret_encrypted", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_settings", "snaptrade_secret_encrypted")
    op.drop_column("user_settings", "snaptrade_user_id")
    op.drop_column("holdings", "is_synced")
    op.drop_column("proposed_orders", "brokerage_account_id")
    op.alter_column("proposed_orders", "broker_order_id", new_column_name="alpaca_order_id")

    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(_TABLES)), checkfirst=True)
