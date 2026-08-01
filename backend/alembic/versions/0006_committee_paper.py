"""AI committee sessions + paper-trading sandbox + auto-trader

Revision ID: 0006_committee_paper
Revises: 0005_watchlist
Create Date: 2026-07-19
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import (
    AnalysisSession,
    AutoTraderConfig,
    AutoTradeRun,
    PaperAccount,
    PaperPosition,
    PaperTrade,
)

revision = "0006_committee_paper"
down_revision = "0005_watchlist"
branch_labels = None
depends_on = None

_TABLES = [
    AnalysisSession.__table__,
    PaperAccount.__table__,
    PaperPosition.__table__,
    PaperTrade.__table__,
    AutoTraderConfig.__table__,
    AutoTradeRun.__table__,
]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(_TABLES)), checkfirst=True)
