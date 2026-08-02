"""Idempotent DDL helpers for Alembic revisions.

Why these exist
---------------
``0001_initial`` builds the schema with ``Base.metadata.create_all``, and
``0003``/``0006``/``0007`` do the same for their own tables with
``checkfirst=True``. That is convenient, but it means a *fresh* database is
created from **today's** models — already carrying every column that later
revisions were written to add.

So on a clean database:

* ``0007``'s rename of ``proposed_orders.alpaca_order_id`` failed, because that
  column had never existed there; and
* ``0009``'s ``add_column("transactions", "is_transfer")`` failed, because
  ``create_all`` had already added it.

Both worked fine on a developer machine that had been migrated step by step,
which is exactly why the CI job that runs ``alembic upgrade head`` against an
empty Postgres is worth having.

Rather than assume either history, these helpers inspect the live database and
only perform work that is actually outstanding. That keeps ``upgrade head``
correct for both a fresh install and a long-lived database.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def column_names(table: str) -> set[str]:
    """Column names currently present on ``table``."""
    return {c["name"] for c in _inspector().get_columns(table)}


def index_names(table: str) -> set[str]:
    """Index names currently present on ``table``."""
    return {i["name"] for i in _inspector().get_indexes(table)}


def add_column_if_missing(table: str, column: sa.Column) -> None:
    if column.name not in column_names(table):
        op.add_column(table, column)


def drop_column_if_present(table: str, name: str) -> None:
    if name in column_names(table):
        op.drop_column(table, name)


def rename_column_if_needed(table: str, old: str, new: str) -> None:
    """Rename ``old`` to ``new`` only when that is still outstanding.

    A no-op when the rename already happened (``new`` present) or when the
    database was created straight from current metadata (``old`` absent).
    """
    cols = column_names(table)
    if old in cols and new not in cols:
        op.alter_column(table, old, new_column_name=new)


def create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    if name not in index_names(table):
        op.create_index(name, table, columns)


def drop_index_if_present(name: str, table: str) -> None:
    if name in index_names(table):
        op.drop_index(name, table_name=table)
