"""Research cache: company-news snapshots + the daily macro brief

Revision ID: 0010_research_cache
Revises: 0009_transfers_categories
Create Date: 2026-08-23

Both tables are global (no user_id), like ``price_bars``: they cache metered
third-party calls that would otherwise be repeated per user.

``checkfirst=True`` keeps this idempotent against a database that already has
the tables — the convention established when the earlier migrations were made
safe to run against a fresh database.
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import MacroBriefCache, NewsSnapshot

revision = "0010_research_cache"
down_revision = "0009_transfers_categories"
branch_labels = None
depends_on = None

_TABLES = [
    NewsSnapshot.__table__,
    MacroBriefCache.__table__,
]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)
