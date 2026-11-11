"""Background plan runs + per-day committee verdict cache

Revision ID: 0014_action_plan_runs
Revises: 0013_book_value_estimated
Create Date: 2026-11-11

``action_plan_runs`` lets "What should I do?" run as a background job the app
polls, instead of one multi-minute request that dies when the user leaves the
screen. ``committee_verdict_cache`` pins the committee's verdict on a ticker
for the trading day, so asking twice gets the same answer.

``checkfirst=True`` keeps this idempotent, as in 0010.
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import ActionPlanRun, CommitteeVerdictCache

revision = "0014_action_plan_runs"
down_revision = "0013_book_value_estimated"
branch_labels = None
depends_on = None

_TABLES = [
    ActionPlanRun.__table__,
    CommitteeVerdictCache.__table__,
]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)
