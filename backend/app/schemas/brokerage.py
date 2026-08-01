from __future__ import annotations

from pydantic import BaseModel

BROKERAGE_DISCLAIMER = (
    "Orders are placed at your own brokerage through SnapTrade and execute with "
    "real money. Every order requires your explicit approval — nothing is "
    "submitted automatically. Verify each order at your broker; this app is not "
    "a broker and does not guarantee execution, price or timing."
)


class BrokerageLinkIn(BaseModel):
    """Optional client deep link the portal returns to when the user finishes.

    Passed through to SnapTrade as ``customRedirect`` so the portal's "Done"
    button sends the user back to the app instead of landing on a dead page.
    """

    redirect_uri: str | None = None


class BrokerageLinkOut(BaseModel):
    """One-time URL where the user authorizes their broker on SnapTrade."""

    portal_url: str
    disclaimer: str = BROKERAGE_DISCLAIMER


class BrokerageConnectionOut(BaseModel):
    id: str
    brokerage_account_id: str
    account_name: str | None = None
    institution_name: str | None = None
    number_masked: str | None = None
    currency: str = "CAD"
    cash: float
    total_value: float
    trading_enabled: bool
    status: str
    last_synced_at: str | None = None


class BrokerageSyncOut(BaseModel):
    positions_synced: int
    positions_removed: int
    cash_cents: int
