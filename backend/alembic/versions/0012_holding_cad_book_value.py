"""Holdings carry a CAD book value that stops moving with the exchange rate

Revision ID: 0012_holding_cad_book_value
Revises: 0011_holding_broker_value
Create Date: 2026-08-26

The cost basis of a USD position was converted with *today's* rate, the same
rate used for its market value, so the currency cancelled out of the
subtraction. The app reported a USD return with a CAD label on it while the
brokerage statement reported the real CAD return. SnapTrade does not supply a
book value in account currency (captured Wealthsimple payloads carry only a
per-share ``cost_basis`` in the listing's own currency), so the app has to
maintain one.

Nullable with no backfill: a rate that was never recorded cannot be invented,
and guessing one would freeze a wrong basis in place while looking
authoritative. NULL keeps the previous behaviour and is surfaced to the user as
an estimate; rows become exact from their first trade after this ships.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_holding_cad_book_value"
down_revision = "0011_holding_broker_value"
branch_labels = None
depends_on = None

_COLUMN = "book_value_cad_cents"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    if "holdings" not in inspector.get_table_names():
        return False
    return _COLUMN in {c["name"] for c in inspector.get_columns("holdings")}


def upgrade() -> None:
    if not _has_column():
        op.add_column("holdings", sa.Column(_COLUMN, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("holdings", _COLUMN)
