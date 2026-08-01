"""watchlist items

Revision ID: 0005_watchlist
Revises: 0004_holdings
Create Date: 2026-07-19
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import WatchlistItem

revision = "0005_watchlist"
down_revision = "0004_holdings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=[WatchlistItem.__table__], checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=[WatchlistItem.__table__], checkfirst=True)
