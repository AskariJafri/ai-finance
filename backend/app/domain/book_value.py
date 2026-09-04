"""The CAD book value of a position, carried forward across syncs.

A book value is what something cost, and what something cost does not change
when the exchange rate does. The app used to convert a USD cost basis at
today's rate, which made the reported cost move every day and — because the
market value was converted at the same rate — cancelled the currency out of the
return entirely. A Canadian holding US stocks then saw a USD return labelled
CAD, disagreeing with their brokerage statement by the whole FX move.

Nothing upstream supplies the figure. SnapTrade's position payload carries a
per-share ``cost_basis`` in the listing's currency and no book value in account
currency, so it has to be accumulated here, one sync at a time:

* buying adds what the new shares cost **at the rate on the day they were
  bought**, which is today's rate at the moment we observe the increase;
* selling removes a proportional slice of the book, leaving the rate that the
  remaining shares were bought at untouched — the same convention every broker
  uses for average-cost accounting.

Framework-free and pure so the arithmetic can be tested on its own.
"""

from __future__ import annotations


def next_book_value_cad_cents(
    *,
    previous_cad_cents: int | None,
    previous_native_cost_cents: int,
    native_cost_cents: int,
    fx_rate: float,
) -> int:
    """The position's CAD book after this observation.

    ``*_native_cost_cents`` are whole-position costs (per-share average times
    quantity) in the listing's currency; ``fx_rate`` converts that currency to
    CAD and is 1.0 for a CAD listing.

    With no previous book — a position first seen today, or one that predates
    this bookkeeping — today's rate is the only one available, so the result is
    an estimate. The caller is responsible for saying so.
    """
    if native_cost_cents <= 0:
        return 0
    if previous_cad_cents is None or previous_native_cost_cents <= 0:
        return round(native_cost_cents * fx_rate)
    if native_cost_cents > previous_native_cost_cents:
        # Bought more. Only the added cost converts at today's rate; what was
        # already held keeps the rate it was bought at.
        added = native_cost_cents - previous_native_cost_cents
        return previous_cad_cents + round(added * fx_rate)
    if native_cost_cents < previous_native_cost_cents:
        # Sold some. The remaining shares cost what they always cost, so the
        # book shrinks in proportion rather than being re-converted.
        return round(previous_cad_cents * (native_cost_cents / previous_native_cost_cents))
    return previous_cad_cents
