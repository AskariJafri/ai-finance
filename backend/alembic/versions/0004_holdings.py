"""manual holdings + invest cash; drop alpaca_credentials

Alpaca does not serve Canadian residents, so brokerage integration was replaced
by user-entered holdings priced from public market data (Yahoo), which also
supports TSX listings.

Revision ID: 0004_holdings
Revises: 0003_invest
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.base import Base
from app.db.models import Holding

revision = "0004_holdings"
down_revision = "0003_invest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[Holding.__table__], checkfirst=True)

    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("user_settings")}
    if "invest_cash_cents" not in columns:
        with op.batch_alter_table("user_settings") as batch:
            batch.add_column(
                sa.Column(
                    "invest_cash_cents",
                    sa.BigInteger(),
                    nullable=False,
                    server_default="0",
                )
            )

    if inspector.has_table("alpaca_credentials"):
        op.drop_table("alpaca_credentials")


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("user_settings") as batch:
        batch.drop_column("invest_cash_cents")
    Base.metadata.drop_all(bind=bind, tables=[Holding.__table__], checkfirst=True)
