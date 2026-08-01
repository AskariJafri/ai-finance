"""Selects/builds the Plaid adapter.

Two entry points:
* ``get_default_plaid_gateway`` — the process-wide default from environment
  config (mock when unconfigured). Used when a user has not supplied their own
  Plaid keys.
* ``build_plaid_gateway`` — build a client from explicit per-user credentials
  (entered in-app via Settings).
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.plaid.gateway import PlaidGateway

logger = get_logger(__name__)


@lru_cache
def get_default_plaid_gateway() -> PlaidGateway:
    if settings.use_mock_plaid:
        from app.integrations.plaid.mock_client import MockPlaidClient

        logger.info("Using MockPlaidClient (no credentials or PLAID_USE_MOCK set)")
        return MockPlaidClient()

    from app.integrations.plaid.real_client import RealPlaidClient

    logger.info("Using RealPlaidClient", extra={"extra": {"plaid_env": settings.plaid_env}})
    return RealPlaidClient()


def build_plaid_gateway(*, client_id: str, secret: str, plaid_env: str) -> PlaidGateway:
    """Build a real Plaid client from explicit user-supplied credentials."""
    from app.integrations.plaid.real_client import RealPlaidClient

    return RealPlaidClient(client_id=client_id, secret=secret, plaid_env=plaid_env)
