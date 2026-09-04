"""Holdings remember what the broker itself said they were worth

Revision ID: 0011_holding_broker_value
Revises: 0010_research_cache
Create Date: 2026-08-26

``SnapTradePosition`` already carried ``market_value_cents`` — the number on the
user's brokerage screen — and the sync discarded it, re-deriving value from
cached daily closes at the app's own FX rate. Two independent sources of drift
on top of a figure we were already being handed.

Nullable and backfill-free: a manual holding has no broker value, and a synced
row gets one on the next sync. ``checkfirst``-style guards keep this idempotent
against a database that already has the columns, matching the convention the
earlier migrations established.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_holding_broker_value"
down_revision = "0010_research_cache"
branch_labels = None
depends_on = None

_COLUMNS = {
    "broker_value_cents": sa.Column("broker_value_cents", sa.BigInteger(), nullable=True),
    "broker_valued_at": sa.Column("broker_valued_at", sa.DateTime(timezone=True), nullable=True),
}


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "holdings" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("holdings")}


def upgrade() -> None:
    present = _existing()
    for name, column in _COLUMNS.items():
        if name not in present:
            op.add_column("holdings", column)


def downgrade() -> None:
    present = _existing()
    for name in _COLUMNS:
        if name in present:
            op.drop_column("holdings", name)
