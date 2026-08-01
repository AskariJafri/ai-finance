"""Finance assistant chat threads

Adds ``chat_sessions`` and ``chat_messages`` for the conversational finance
assistant. Nothing else is touched, so this is a pure additive migration.

Revision ID: 0008_chat
Revises: 0007_brokerage_link
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.db.models import ChatMessage, ChatSession

revision = "0008_chat"
down_revision = "0007_brokerage_link"
branch_labels = None
depends_on = None

# Order matters: chat_messages carries the FK to chat_sessions.
_TABLES = [ChatSession.__table__, ChatMessage.__table__]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_TABLES, checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(_TABLES)), checkfirst=True)
