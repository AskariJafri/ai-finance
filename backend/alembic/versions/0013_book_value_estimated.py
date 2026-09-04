"""Mark a CAD book value whose exchange rate was assumed, not observed

Revision ID: 0013_book_value_estimated
Revises: 0012_holding_cad_book_value
Create Date: 2026-08-26

``book_value_cad_cents`` is exact when the app watched the position appear: the
shares were bought at a rate it could read that day. It is an assumption for a
position that already existed the first time it synced, and for a cost typed in
by hand with no purchase date. Both stop the basis drifting with the exchange
rate, which is the fix; only one of them is a real book value.

Defaults to true, which is the safe reading for every row already in the table
— all of them predate the bookkeeping, so none of their rates were observed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_book_value_estimated"
down_revision = "0012_holding_cad_book_value"
branch_labels = None
depends_on = None

_COLUMN = "book_value_estimated"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    if "holdings" not in inspector.get_table_names():
        return False
    return _COLUMN in {c["name"] for c in inspector.get_columns("holdings")}


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            "holdings",
            sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    if _has_column():
        op.drop_column("holdings", _COLUMN)
