"""50/30/20 rule: which spending categories count as needs vs wants.

``Category.budget_bucket`` is now the authoritative source (users can create
their own categories, so a fixed slug set can no longer cover the taxonomy).
``NEEDS_SLUGS`` survives only as the migration/seed source and a fallback for
rows a backfill missed. Savings is not a bucket here — it is the monthly
leftover (income - needs - wants), computed in the reports service.
"""

from __future__ import annotations

from enum import StrEnum


class Bucket(StrEnum):
    NEEDS = "needs"
    WANTS = "wants"


NEEDS_SLUGS: frozenset[str] = frozenset({"groceries", "transport", "utilities", "rent", "health"})


def bucket_for(slug: str | None, budget_bucket: str | None) -> Bucket:
    """Bucket for a category, given its slug and its ``budget_bucket`` column.

    ``budget_bucket`` wins whenever it's set — including for user-created
    categories, which have no slug in ``NEEDS_SLUGS`` at all. Unset (legacy
    rows a backfill missed, or ``None`` for uncategorized) falls back to the
    slug set; everything else — including uncategorized and any unrecognized
    slug — counts as a want. Over-flagging discretionary spend is the safe
    direction for a budgeting nudge, and it nudges the user to categorize.
    """
    if budget_bucket == Bucket.NEEDS.value:
        return Bucket.NEEDS
    if budget_bucket == Bucket.WANTS.value:
        return Bucket.WANTS
    return Bucket.NEEDS if slug in NEEDS_SLUGS else Bucket.WANTS
