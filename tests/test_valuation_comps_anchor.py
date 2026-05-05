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


def test_comps_mid_tracks_peer_range_without_market_anchor():
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

    # No market-anchor: peer range changes should move comps output.
    assert abs(out_wide.comps.mid - out_shifted.comps.mid) > 1e-6

    assert "EV/EBITDA (peer range)" in out_wide.field_sources
    assert out_wide.field_sources["EV/EBITDA (peer range)"][1] == "validated_peers"


def test_mega_cap_tech_rejects_low_peer_range():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "ev_ebitda_range": [8.0, 12.0],
        },
        market_data=market_data,
        sector="Technology",
    )
    assert out.field_sources["EV/EBITDA (peer range)"][1] == "peer_quality_gate"
    assert "fallback" in out.field_sources["EV/EBITDA (peer range)"][0]


def test_forward_growth_source_and_mega_cap_normalisation_note():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    market_data.forward_revenue_growth = 0.22
    market_data.forward_revenue_1y = None  # force proxy classification

    out = svc.run_full_valuation(
        financials=financials,
        assumptions={"_assumption_source": "system"},
        market_data=market_data,
        sector="Technology",
    )

    assert "Forward Revenue Growth" in out.field_sources
    assert out.field_sources["Forward Revenue Growth"][1] == "yfinance_earnings_proxy"
    assert out.field_sources["Forward Revenue Growth"][2] == "estimated"
    assert any("normalised for mega-cap maturity" in n for n in out.notes)


def test_recommendation_guardrail_caps_sell_on_high_dispersion():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "ev_ebitda_range": [60.0, 80.0],  # force large comps-vs-dcf dispersion
        },
        market_data=market_data,
        sector="Technology",
    )
    assert out.recommendation.recommendation in {"HOLD", "BUY"}
