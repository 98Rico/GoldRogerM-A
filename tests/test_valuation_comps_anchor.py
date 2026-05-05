from goldroger.data.fetcher import MarketData
from goldroger.finance.core.valuation_service import ValuationService


def _base_financials() -> dict:
    return {
        "revenue_current": 100_000.0,
        "ebitda_margin": 0.30,
        "tax_rate": 0.20,
        "nwc_pct": 0.02,
    }


def _base_market_data() -> MarketData:
    return MarketData(
        ticker="NVDA",
        company_name="NVIDIA",
        sector="Technology",
        revenue_ttm=100_000.0,
        revenue_history=[60_000.0, 70_000.0, 80_000.0, 90_000.0, 100_000.0],
        ebitda_margin=0.30,
        beta=1.2,
        market_cap=2_000_000.0,
        shares_outstanding=20_000.0,
        current_price=100.0,
        total_debt=10_000.0,
        cash_and_equivalents=5_000.0,
        net_debt=5_000.0,
        effective_tax_rate=0.20,
        capex_ttm=4_000.0,
        da_ttm=3_000.0,
        ev_ebitda_market=30.0,
        data_source="yfinance",
    )


def test_comps_mid_stable_when_market_anchor_present():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()

    out_wide = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "ev_ebitda_range": [20.0, 50.0],
        },
        market_data=market_data,
        sector="Technology",
    )
    out_shifted = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "ev_ebitda_range": [10.0, 80.0],
        },
        market_data=market_data,
        sector="Technology",
    )

    assert out_wide.comps is not None
    assert out_shifted.comps is not None
    assert out_wide.blended is not None
    assert out_shifted.blended is not None

    # With market anchor enabled, comps mid should remain tied to market EV/EBITDA.
    assert abs(out_wide.comps.mid - out_shifted.comps.mid) <= 1e-6

    # Regression guarantee: blended EV should not materially move across peer ranges.
    pct_diff = abs(out_wide.blended.blended - out_shifted.blended.blended) / out_wide.blended.blended
    assert pct_diff <= 0.05

    assert "EV/EBITDA (peer range)" in out_wide.field_sources
    assert out_wide.field_sources["EV/EBITDA (peer range)"][1] == "validated_peers"
