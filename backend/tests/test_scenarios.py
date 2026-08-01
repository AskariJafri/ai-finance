from __future__ import annotations

import pytest

from app.analytics.scenarios import SCENARIOS, apply_scenario


def _scenario(key: str):
    return next(s for s in SCENARIOS if s.key == key)


def test_beta_scales_equity_shock():
    gfc = _scenario("gfc_2008")
    positions = [
        {"symbol": "AAPL", "market_value": 10_000.0, "beta": 1.2},
        {"symbol": "MSFT", "market_value": 10_000.0, "beta": 0.8},
    ]
    result = apply_scenario(gfc, positions)
    per = {p["symbol"]: p for p in result["per_position"]}
    assert per["AAPL"]["impact"] == pytest.approx(10_000 * -0.57 * 1.2, abs=0.01)
    assert per["MSFT"]["impact"] == pytest.approx(10_000 * -0.57 * 0.8, abs=0.01)
    assert result["portfolio_impact"]["pct"] == pytest.approx(-0.57, abs=1e-6)


def test_bond_etf_uses_bond_shock():
    gfc = _scenario("gfc_2008")
    positions = [{"symbol": "BND", "market_value": 10_000.0, "beta": 1.5}]
    result = apply_scenario(gfc, positions)
    # BND classified as a bond: +5% in 2008, beta ignored.
    assert result["portfolio_impact"]["amount"] == pytest.approx(500.0, abs=0.01)


def test_missing_beta_defaults_to_one():
    covid = _scenario("covid_2020")
    positions = [{"symbol": "XYZ", "market_value": 1_000.0, "beta": None}]
    result = apply_scenario(covid, positions)
    assert result["portfolio_impact"]["amount"] == pytest.approx(-340.0, abs=0.01)


def test_cad_swing_leaves_usd_untouched():
    fx = _scenario("cad_swing")
    positions = [{"symbol": "AAPL", "market_value": 10_000.0, "beta": 1.0}]
    result = apply_scenario(fx, positions)
    assert result["portfolio_impact"]["amount"] == pytest.approx(0.0)
    assert fx.fx_shock != 0.0  # the CAD effect is applied by the service layer


def test_shock_cannot_exceed_total_loss():
    # A high-beta name in a -57% scenario would imply -85% via naive scaling;
    # a long position can never lose more than 100%, so extreme betas clamp.
    gfc = _scenario("gfc_2008")
    positions = [{"symbol": "TSLA", "market_value": 1_000.0, "beta": 2.5}]
    result = apply_scenario(gfc, positions)
    assert result["per_position"][0]["shock_pct"] == pytest.approx(-1.0)
    assert result["portfolio_impact"]["amount"] == pytest.approx(-1_000.0)
    assert result["portfolio_impact"]["pct"] >= -1.0


def test_every_scenario_has_source_and_description():
    for s in SCENARIOS:
        assert s.source and s.description and s.name
