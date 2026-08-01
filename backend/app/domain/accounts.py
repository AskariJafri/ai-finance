"""Account-type facts shared by the money aggregates and the net-worth math.

This lives in the domain layer rather than in a repository so the income guard
(:mod:`app.repositories.transactions`) and net worth
(:mod:`app.services.dashboard_service`) can never drift apart: a type that is a
liability for one must be a liability for the other.
"""

from __future__ import annotations

# Account types that are liabilities, not sources of cash. Money moving INTO one
# of these (amount < 0 on a credit card / loan) is a bill/card *payment*, never
# income — counting it as income double-counts (the purchases were already
# spending) and inflates earnings. Income is only real on cash accounts.
LIABILITY_ACCOUNT_TYPES = ("credit", "loan")

# Account types holding spendable cash. Deliberately NOT "everything that isn't
# a liability": that would pull Plaid-linked *investment* accounts into the
# banking headline, double-counting against the Invest tab, which tracks
# holdings in its own tables. ``None`` is included because Plaid Sandbox and the
# mock client omit ``type`` on their depository accounts.
CASH_ACCOUNT_TYPES: tuple[str | None, ...] = ("depository", None)


def is_liability(account_type: str | None) -> bool:
    return (account_type or "") in LIABILITY_ACCOUNT_TYPES


def is_cash(account_type: str | None) -> bool:
    return account_type in CASH_ACCOUNT_TYPES
