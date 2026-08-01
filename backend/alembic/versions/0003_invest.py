"""invest module: price_bars, portfolio_targets, proposed_orders

Created from ORM metadata like 0002; ``checkfirst`` makes this a no-op on
fresh installs where 0001's ``create_all`` already created these tables.

(The original revision also created ``alpaca_credentials``; Alpaca was dropped
in 0004 — it does not serve Canadian residents — so that table is created
there-and-then-dropped only for databases that already ran this revision.)

Revision ID: 0003_invest
Revises: 0002_user_settings_ai_insights
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import PortfolioTarget, PriceBar, ProposedOrder

revision = "0003_invest"
down_revision = "0002_user_settings_ai_insights"
branch_labels = None
depends_on = None

_TABLES = [
    PriceBar.__table__,
    PortfolioTarget.__table__,
    ProposedOrder.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=_TABLES, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=_TABLES, checkfirst=True)
