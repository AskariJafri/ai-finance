"""Factor-risk decomposition, verified against constructions with known answers.

Series are built so the right answer is known by construction rather than
copied from the implementation: an asset defined as ``1.5*equity + 0.5*ir``
must regress back to those loadings, and a portfolio whose only driver is one
factor must attribute ~all of its variance there.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analytics import factor_risk as fr

N = 500  # comfortably above MIN_DAYS


def _factor_panel(seed: int = 7) -> dict[str, np.ndarray]:
    """Independent-ish daily proxy returns for every factor proxy."""
    rng = np.random.default_rng(seed)
    return {p: rng.normal(0.0, 0.01, N) for p in fr.PROXY_SYMBOLS}


def test_proxy_symbols_cover_every_factor():
    for factor in fr.FACTORS:
        for proxy in factor.proxies:
            assert proxy in fr.PROXY_SYMBOLS
    # De-duplicated: a proxy shared by two factors is synced once.
    assert len(fr.PROXY_SYMBOLS) == len(set(fr.PROXY_SYMBOLS))


def test_spread_factor_is_long_minus_short():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)
    assert unavailable == []
    # Credit is defined as HYG - LQD.
    assert f_returns["credit"] == pytest.approx(returns["HYG"] - returns["LQD"])
    # Single-proxy factors pass through untouched.
    assert f_returns["equity"] == pytest.approx(returns["SPY"])


def test_missing_proxy_drops_the_factor_rather_than_faking_it():
    returns = _factor_panel()
    del returns["DBC"]
    f_returns, unavailable = fr.build_factor_returns(returns)
    assert "commodity" in unavailable
    assert "commodity" not in f_returns
    # A dropped factor must not appear as a zero series — that would read as
    # "no exposure" instead of "not measured".
    assert all(len(v) == N for v in f_returns.values())


def test_exposures_recover_known_loadings():
    returns = _factor_panel()
    f_returns, _ = fr.build_factor_returns(returns)
    keys = [f.key for f in fr.FACTORS if f.key in f_returns]
    matrix = np.column_stack([f_returns[k] for k in keys])

    true_betas = np.array([1.5, 0.5, -0.25, 0.1, 0.0])
    asset = matrix @ true_betas + 0.0003  # exact construction + constant drift

    got = fr.exposures_for(asset, matrix)
    assert got is not None
    assert got == pytest.approx(true_betas, abs=1e-9)


def test_exposures_refuse_a_series_too_short_to_solve():
    returns = {p: np.random.default_rng(1).normal(0, 0.01, 20) for p in fr.PROXY_SYMBOLS}
    f_returns, _ = fr.build_factor_returns(returns)
    matrix = np.column_stack(list(f_returns.values()))
    assert fr.exposures_for(matrix[:, 0], matrix) is None


def test_to_reporting_currency_compounds_the_fx_leg():
    r_usd = np.array([0.01, -0.02, 0.005])
    r_fx = np.array([0.002, 0.003, -0.001])
    out = fr.to_reporting_currency(
        {"AAPL": r_usd, "VFV.TO": r_usd},
        {"AAPL": "USD", "VFV.TO": "CAD"},
        r_fx,
    )
    # USD holding: what a CAD holder earns is the compounded product.
    assert out["AAPL"] == pytest.approx((1 + r_usd) * (1 + r_fx) - 1)
    # CAD holding is already in the reporting currency — untouched.
    assert out["VFV.TO"] == pytest.approx(r_usd)


def test_to_reporting_currency_passes_through_without_an_fx_series():
    r = np.array([0.01, -0.02])
    out = fr.to_reporting_currency({"AAPL": r}, {"AAPL": "USD"}, None)
    assert out["AAPL"] == pytest.approx(r)


def test_single_factor_portfolio_attributes_variance_to_that_factor():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)

    # An asset driven purely by equity, with no idiosyncratic noise.
    asset = 1.2 * f_returns["equity"]
    result = fr.decompose({"X": 1.0}, {"X": asset}, f_returns, unavailable)

    assert result is not None
    by_key = {e.key: e for e in result.exposures}
    assert by_key["equity"].exposure == pytest.approx(1.2, abs=1e-9)
    assert by_key["equity"].variance_share == pytest.approx(1.0, abs=0.02)
    assert result.r_squared == pytest.approx(1.0, abs=0.02)
    assert result.idiosyncratic_vol == pytest.approx(0.0, abs=1e-6)


def test_exposures_aggregate_linearly_by_weight():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)
    a = 2.0 * f_returns["equity"]
    b = 0.0 * f_returns["equity"]  # zero exposure

    result = fr.decompose({"A": 0.25, "B": 0.75}, {"A": a, "B": b}, f_returns, unavailable)
    assert result is not None
    by_key = {e.key: e for e in result.exposures}
    # 0.25*2.0 + 0.75*0.0
    assert by_key["equity"].exposure == pytest.approx(0.5, abs=1e-9)


def test_variance_contributions_sum_to_the_factor_variance():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)
    rng = np.random.default_rng(3)
    asset = 0.9 * f_returns["equity"] + 0.4 * f_returns["ir"] + rng.normal(0, 0.004, N)
    result = fr.decompose({"X": 1.0}, {"X": asset}, f_returns, unavailable)
    assert result is not None
    # variance_share is each factor's slice of b'Fb, so the slices sum to 1.
    assert sum(e.variance_share for e in result.exposures) == pytest.approx(1.0, abs=1e-9)


def test_factor_and_idiosyncratic_variance_reconstruct_the_total():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)
    rng = np.random.default_rng(11)
    asset = 0.8 * f_returns["equity"] + rng.normal(0, 0.006, N)
    result = fr.decompose({"X": 1.0}, {"X": asset}, f_returns, unavailable)
    assert result is not None
    # Vols add in quadrature, never linearly.
    total = np.sqrt(result.factor_vol**2 + result.idiosyncratic_vol**2)
    assert total == pytest.approx(result.total_vol, rel=1e-9)


def test_ir_exposure_is_reported_as_duration_years():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)
    asset = 1.0 * f_returns["ir"]
    result = fr.decompose({"X": 1.0}, {"X": asset}, f_returns, unavailable)
    assert result is not None
    ir = next(e for e in result.exposures if e.key == "ir")
    assert ir.duration_years == pytest.approx(ir.exposure * fr.IR_PROXY_DURATION_YEARS, abs=1e-9)
    # Only the rate factor carries a duration reading.
    assert all(e.duration_years is None for e in result.exposures if e.key != "ir")


def test_correlated_factors_produce_a_diversification_benefit():
    """Aggregation must use the covariance, not a naive sum of standalones."""
    rng = np.random.default_rng(5)
    base = rng.normal(0, 0.01, N)
    returns = {p: rng.normal(0, 0.01, N) for p in fr.PROXY_SYMBOLS}
    # Make equity and commodity strongly negatively correlated, so holding both
    # is genuinely less risky than adding their standalone risks.
    returns["SPY"] = base
    returns["DBC"] = -base + rng.normal(0, 0.001, N)

    f_returns, unavailable = fr.build_factor_returns(returns)
    asset = f_returns["equity"] + f_returns["commodity"]
    result = fr.decompose({"X": 1.0}, {"X": asset}, f_returns, unavailable)
    assert result is not None
    # Offsetting exposures: combined factor vol is far below the naive sum.
    standalone_sum = sum(e.standalone_vol for e in result.exposures)
    assert result.factor_vol < standalone_sum
    assert result.diversification_benefit < 0


def test_decompose_returns_none_without_enough_history():
    short = {p: np.random.default_rng(2).normal(0, 0.01, 10) for p in fr.PROXY_SYMBOLS}
    f_returns, unavailable = fr.build_factor_returns(short)
    result = fr.decompose({"X": 1.0}, {"X": short["SPY"]}, f_returns, unavailable)
    assert result is None


def test_unsolvable_holding_is_skipped_not_fatal():
    returns = _factor_panel()
    f_returns, unavailable = fr.build_factor_returns(returns)
    good = 1.0 * f_returns["equity"]
    bad = np.array([0.01, 0.02])  # too short to regress
    result = fr.decompose(
        {"GOOD": 0.5, "BAD": 0.5}, {"GOOD": good, "BAD": bad}, f_returns, unavailable
    )
    assert result is not None
    # BAD's weight is redistributed, so GOOD carries its full loading.
    by_key = {e.key: e for e in result.exposures}
    assert by_key["equity"].exposure == pytest.approx(1.0, abs=1e-9)
