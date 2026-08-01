from __future__ import annotations

from pydantic import BaseModel


class LinkTokenResponse(BaseModel):
    link_token: str
    expiration: str | None = None


class ExchangeRequest(BaseModel):
    public_token: str
    # Optional metadata Plaid Link returns on the client; used for display only.
    institution_id: str | None = None
    institution_name: str | None = None


class ExchangeResponse(BaseModel):
    item_id: str
    institution_name: str | None = None
    accounts_synced: int
    transactions_synced: int


class SyncResponse(BaseModel):
    added: int
    modified: int
    removed: int
    accounts_synced: int
    categorized_by_ai: int
