"""SnapTrade payload -> domain DTO mapping, against real API responses.

Every other brokerage test runs through ``MockSnapTradeClient``, which hands
back domain objects directly and therefore exercises none of the parsing. That
gap is not theoretical: SnapTrade moved list responses into a ``{"results": [...]}"
envelope and renamed the position fields, and because iterating a dict yields
its keys rather than raising, a real account's positions parsed to zero rows in
complete silence — no error, no log, an empty portfolio.

The payloads below are captured verbatim from the live API (account identifiers
scrubbed), so a future shape change fails here instead of on someone's phone.
"""

from __future__ import annotations

import pytest

from app.integrations.snaptrade.real_client import (
    RealSnapTradeClient,
    _normalize_ticker,
    _results,
    _to_cents,
)

# --- captured live payloads -------------------------------------------------

POSITIONS_BODY = {
    "results": [
        {
            "instrument": {
                "kind": "stock",
                "id": "scrubbed-instrument-1",
                "symbol": "NVDA",
                "raw_symbol": "NVDA",
                "description": "NVIDIA Corporation",
                "currency": "USD",
                "exchange": "XNAS",
            },
            "units": "0.6793",
            "price": "217.2",
            "cost_basis": "220.7994093545444428087737377",
            "currency": "USD",
        },
        {
            "instrument": {
                "kind": "etf",
                "id": "scrubbed-instrument-2",
                "symbol": "SPY",
                "raw_symbol": "SPY",
                "description": "SPDR S&P 500 ETF Trust",
                "currency": "USD",
                "exchange": "ARCX",
            },
            "units": "0.2581",
            "price": "763.1588",
            "cost_basis": "774.7913082787454862456412243",
            "currency": "USD",
        },
    ],
    "data_freshness": {"as_of": "2026-08-20T22:54:26Z"},
}

ACCOUNTS_BODY = [
    {
        "id": "scrubbed-closed",
        "name": "Wealthsimple Trade TFSA",
        "institution_name": "Wealthsimple Trade",
        "currency": None,
        "status": "closed",
        "meta": {"type": "tfsa", "status": "closed", "currency": "CAD"},
        "balance": {"total": {"amount": 0.0, "currency": "CAD"}},
    },
    {
        "id": "scrubbed-open",
        "name": "Wealthsimple Trade TFSA",
        "institution_name": "Wealthsimple Trade",
        "number": "tfsa-scrubbed",
        "currency": None,
        "status": "open",
        "meta": {"type": "tfsa", "status": "open", "currency": "CAD"},
        "balance": {"total": {"amount": 478.2292228, "currency": "CAD"}},
    },
]

BALANCES_BODY = [
    {
        "currency": {"code": "CAD", "name": "Canadian Dollar", "id": "scrubbed-currency"},
        "cash": 3.53,
        "buying_power": 3.53,
    }
]

# An older-generation payload, kept to prove the mapping still accepts both.
LEGACY_POSITIONS_BODY = [
    {
        "symbol": {
            "symbol": {
                "symbol": "VFV",
                "raw_symbol": "VFV",
                "description": "Vanguard S&P 500 Index ETF",
                "currency": {"code": "CAD"},
                "exchange": {"code": "TSX"},
            }
        },
        "units": 42,
        "average_purchase_price": 119.0,
        "market_value": 5985.0,
    }
]


class _Response:
    """The SDK wraps every payload in an object exposing ``.body``."""

    def __init__(self, body):
        self.body = body


class _StubSDK:
    """Stands in for ``snaptrade_client.SnapTrade``.

    Each entry is either a payload to wrap in a response or an exception to
    raise, which is enough to drive every mapping path.
    """

    def __init__(self, **endpoints):
        self.account_information = self
        self.reference_data = self
        self._endpoints = endpoints

    def __getattr__(self, name):
        if name not in self._endpoints:
            raise AttributeError(name)
        value = self._endpoints[name]

        def call(**kwargs):
            if isinstance(value, Exception):
                raise value
            return _Response(value)

        return call


def _client(**endpoints) -> RealSnapTradeClient:
    """A client with the SDK constructor bypassed.

    Only the mapping is under test here — building a real ``SnapTrade`` would
    need credentials and would reach the network.
    """
    client = RealSnapTradeClient.__new__(RealSnapTradeClient)
    client._personal = True  # no userId/userSecret on the wire
    client._client = _StubSDK(**endpoints)
    return client


# --- envelope ---------------------------------------------------------------


def test_results_unwraps_the_results_envelope():
    assert _results(_Response(POSITIONS_BODY)) == POSITIONS_BODY["results"]


def test_results_passes_a_bare_list_through():
    assert _results(_Response(LEGACY_POSITIONS_BODY)) == LEGACY_POSITIONS_BODY


def test_results_is_empty_for_an_unexpected_shape():
    assert _results(_Response(None)) == []
    assert _results(_Response({"data_freshness": {}})) == []


# --- money ------------------------------------------------------------------


def test_to_cents_unwraps_a_money_object():
    """``balance.total`` is ``{"amount": ..., "currency": ...}``, not a number.

    Passing the dict straight through returned 0, which is how a $478 account
    came to display as empty.
    """
    assert _to_cents({"amount": 478.2292228, "currency": "CAD"}) == 47823
    assert _to_cents(3.53) == 353
    assert _to_cents("217.2") == 21720
    assert _to_cents(None) == 0


# --- tickers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("instrument", "expected"),
    [
        ({"symbol": "NVDA", "exchange": "XNAS"}, "NVDA"),
        ({"symbol": "SPY", "exchange": "ARCX"}, "SPY"),
        # MIC form, current payloads.
        ({"symbol": "VFV", "exchange": "XTSE"}, "VFV.TO"),
        ({"symbol": "CTS", "exchange": "XTSV"}, "CTS.V"),
        # Friendly-code form, older payloads.
        ({"symbol": "XEQT", "exchange": {"code": "TSX"}}, "XEQT.TO"),
        # Already suffixed — left alone.
        ({"symbol": "VFV.TO", "exchange": "XTSE"}, "VFV.TO"),
    ],
)
def test_normalize_ticker(instrument, expected):
    assert _normalize_ticker(instrument) == expected


# --- positions --------------------------------------------------------------


async def test_list_positions_maps_the_current_payload():
    client = _client(get_all_account_positions=POSITIONS_BODY)

    positions = await client.list_positions(
        snaptrade_user_id="u", user_secret="s", account_id="scrubbed-open"
    )

    assert [p.symbol for p in positions] == ["NVDA", "SPY"]
    nvda, spy = positions
    assert nvda.qty == pytest.approx(0.6793)
    assert nvda.avg_cost_cents == 22080  # from cost_basis, per share
    assert nvda.currency == "USD"
    assert nvda.name == "NVIDIA Corporation"
    # No market_value on the payload — derived from units x price.
    assert nvda.market_value_cents == pytest.approx(14754, abs=2)
    assert spy.qty == pytest.approx(0.2581)


async def test_list_positions_still_maps_the_legacy_payload():
    client = _client(get_all_account_positions=LEGACY_POSITIONS_BODY)

    (position,) = await client.list_positions(
        snaptrade_user_id="u", user_secret="s", account_id="a"
    )
    assert position.symbol == "VFV.TO"
    assert position.qty == 42
    assert position.avg_cost_cents == 11900
    assert position.currency == "CAD"


# --- accounts ---------------------------------------------------------------


async def test_list_accounts_maps_balances_and_open_state():
    client = _client(
        list_user_accounts=ACCOUNTS_BODY,
        get_user_account_balance=BALANCES_BODY,
    )

    closed, live = await client.list_accounts(snaptrade_user_id="u", user_secret="s")

    assert closed.is_open is False
    assert live.is_open is True
    # Top-level currency is null on this broker; meta carries the real one.
    assert live.currency == "CAD"
    assert live.total_value_cents == 47823
    # Cash is not on the accounts payload at all — it needs its own call.
    assert live.cash_cents == 353
    # A closed account is not worth a balance round trip.
    assert closed.cash_cents == 0


async def test_cash_falls_back_to_zero_when_balances_fail():
    """Losing the cash figure must not cost the caller the account list."""
    client = _client(
        list_user_accounts=[ACCOUNTS_BODY[1]],
        get_user_account_balance=RuntimeError("upstream 503"),
    )

    (account,) = await client.list_accounts(snaptrade_user_id="u", user_secret="s")
    assert account.cash_cents == 0
    assert account.total_value_cents == 47823
