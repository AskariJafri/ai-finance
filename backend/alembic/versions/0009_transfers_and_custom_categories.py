"""Transfer flags on transactions + budget buckets on categories

Two related changes, shipped as one migration so there is a single deploy step:

* ``transactions.is_transfer`` / ``transactions.transfer_group_id`` — the
  persisted verdict "this row is a value-neutral movement between the user's own
  accounts (incl. a credit-card payment)". Every money aggregate now excludes
  it, which is what stops a card payment from being counted as spending on the
  chequing side and as income on the card side.
* ``categories.budget_bucket`` — makes the 50/30/20 needs/wants split
  data-driven so user-created categories can be assigned a bucket instead of
  silently defaulting to "wants". Unused until the custom-category feature reads
  it; the backfill below is inert until then.

Revision ID: 0009_transfers_and_custom_categories
Revises: 0008_chat
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_transfers_categories"
down_revision = "0008_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default stays in place after the backfill (rather than being
    # dropped) so any raw INSERT path that predates the ORM column is still safe.
    op.add_column(
        "transactions",
        sa.Column("is_transfer", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("transactions", sa.Column("transfer_group_id", sa.Uuid(), nullable=True))
    op.create_index("ix_transactions_is_transfer", "transactions", ["is_transfer"])
    op.create_index("ix_transactions_transfer_group_id", "transactions", ["transfer_group_id"])
    op.add_column("categories", sa.Column("budget_bucket", sa.String(length=10), nullable=True))

    # Backfill 1: anything already sitting in a TRANSFER-group category was
    # already being excluded from spending by the old category-group guard, so
    # flagging it changes no number — it just moves the decision onto the column.
    # "group" is a reserved word; it must stay quoted.
    op.execute(
        """
        UPDATE transactions t SET is_transfer = true
        FROM categories c
        WHERE t.category_id = c.id AND c."group" = 'transfer'
        """
    )

    # Backfill 2: seed the system categories' buckets from the frozenset that is
    # about to stop being the source of truth (domain/budget_rule.py::NEEDS_SLUGS).
    op.execute(
        """
        UPDATE categories SET budget_bucket = 'needs'
        WHERE user_id IS NULL
          AND slug IN ('groceries', 'transport', 'utilities', 'rent', 'health')
        """
    )
    op.execute(
        """
        UPDATE categories SET budget_bucket = 'wants'
        WHERE user_id IS NULL AND budget_bucket IS NULL AND "group" = 'expense'
        """
    )

    # Rows whose transfer-ness only text rules or pair-matching can establish are
    # NOT repaired here — that needs Python. Run once after deploying:
    #     docker compose exec api python -m app.scripts.rederive_transfers


def downgrade() -> None:
    op.drop_column("categories", "budget_bucket")
    op.drop_index("ix_transactions_transfer_group_id", table_name="transactions")
    op.drop_index("ix_transactions_is_transfer", table_name="transactions")
    op.drop_column("transactions", "transfer_group_id")
    op.drop_column("transactions", "is_transfer")
