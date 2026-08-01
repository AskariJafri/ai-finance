"""Match the two legs of a transfer against each other.

This is the signal of last resort for the hardest row in the whole model: the
*outflow* leg of a credit-card payment. On a chequing account it is a positive
amount with an opaque description — structurally identical to a debit-card
purchase — so neither the account type nor the sign can distinguish it. If Plaid
returns no ``personal_finance_category`` (older items, some institutions, pending
rows) and the description doesn't match a narrow text rule, pairing is the only
thing left.

Deliberately dumb, because the failure modes are not symmetric: a false positive
silently erases real spending, while a miss merely leaves a number slightly high.
So the predicate is exact-amount, same-currency, ≤3 days, different accounts, and
**any ambiguity is skipped rather than guessed**.

Pure functions over plain tuples — no session, no ORM — so the rules can be
tested without a database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date as date_type

DEFAULT_MAX_DAY_GAP = 3


@dataclass(frozen=True)
class Leg:
    """One side of a candidate transfer."""

    id: uuid.UUID
    account_id: uuid.UUID
    amount_cents: int  # + outflow, - inflow
    date: date_type
    currency: str
    # Already part of a matched pair — leave it alone (keeps re-runs idempotent).
    has_group: bool = False
    # The user categorized this themselves into something that is not a transfer.
    # Their verdict outranks ours.
    pinned_non_transfer: bool = False


def _eligible(leg: Leg) -> bool:
    return (
        not leg.has_group
        and not leg.pinned_non_transfer
        # A zero-amount row would "match" itself in sign terms and carries no
        # information anyway.
        and leg.amount_cents != 0
    )


def find_pairs(
    rows: list[Leg], *, max_day_gap: int = DEFAULT_MAX_DAY_GAP
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Return ``[(outflow_id, inflow_id)]`` for confidently matched transfers.

    Greedy, nearest-date-first, strictly one-to-one. Deterministic: legs are
    processed in ``(date, id)`` order, so the same input always pairs the same way.
    """
    eligible = sorted(
        (leg for leg in rows if _eligible(leg)), key=lambda leg: (leg.date, str(leg.id))
    )
    outflows = [leg for leg in eligible if leg.amount_cents > 0]
    inflows = [leg for leg in eligible if leg.amount_cents < 0]

    used: set[uuid.UUID] = set()
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []

    for out in outflows:
        if out.id in used:
            continue
        candidates = [
            leg
            for leg in inflows
            if leg.id not in used
            and leg.account_id != out.account_id
            and leg.amount_cents == -out.amount_cents
            and leg.currency == out.currency
            and abs((leg.date - out.date).days) <= max_day_gap
        ]
        if not candidates:
            continue

        candidates.sort(key=lambda leg: (abs((leg.date - out.date).days), str(leg.id)))
        best = candidates[0]
        best_gap = abs((best.date - out.date).days)
        # Two equally-close candidates means we cannot tell which leg this pays.
        # Never guess: a wrong pairing erases a real expense.
        if sum(1 for leg in candidates if abs((leg.date - out.date).days) == best_gap) > 1:
            continue

        used.add(out.id)
        used.add(best.id)
        pairs.append((out.id, best.id))

    return pairs
