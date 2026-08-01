"""initial schema

Baseline migration. Creates the Phase 1 schema directly from the ORM metadata
so the models remain the single source of truth. Subsequent changes should be
generated incrementally with ``alembic revision --autogenerate``.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-06
"""

from __future__ import annotations

# Ensure all models are imported and registered on the metadata.
import app.db.models  # noqa: F401
from alembic import op
from app.db.base import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
