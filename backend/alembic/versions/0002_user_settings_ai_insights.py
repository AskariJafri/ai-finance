"""user_settings and ai_insights tables

Created from ORM metadata like 0001 (models remain the single source of
truth). ``checkfirst`` makes this a no-op on fresh installs where 0001's
``create_all`` already created these tables.

Revision ID: 0002_user_settings_ai_insights
Revises: 0001_initial
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import AIInsight, UserSettings

revision = "0002_user_settings_ai_insights"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_TABLES = [UserSettings.__table__, AIInsight.__table__]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=_TABLES, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=_TABLES, checkfirst=True)
