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
    assert "rejected" in out.field_sources["EV/EBITDA (peer range)"][0]
    assert out.comps is not None
    assert out.comps.mid == 0.0


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


def test_mega_cap_weight_rules_follow_peer_count_buckets():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()

    out_2 = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "peer_count": 2,
            "peer_quality": "weak",
            "ev_ebitda_range": [20.0, 28.0],
        },
        market_data=market_data,
        sector="Technology",
    )
    assert out_2.weights_used["comps"] <= 0.10

    out_3 = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "peer_count": 3,
            "peer_quality": "mixed",
            "ev_ebitda_range": [20.0, 28.0],
        },
        market_data=market_data,
        sector="Technology",
    )
    assert 0.20 <= out_3.weights_used["comps"] <= 0.25

    out_6 = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "peer_count": 6,
            "peer_quality": "normal",
            "ev_ebitda_range": [20.0, 28.0],
        },
        market_data=market_data,
        sector="Technology",
    )
    assert 0.35 <= out_6.weights_used["comps"] <= 0.40

    out_9 = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "peer_count": 9,
            "peer_quality": "strong",
            "ev_ebitda_range": [15.0, 20.0],
        },
        market_data=market_data,
        sector="Technology",
    )
    assert 0.35 <= out_9.weights_used["comps"] <= 0.50
    if out_9.weights_used["comps"] <= 0.35:
        assert any("dispersion" in n.lower() for n in out_9.notes)


def test_live_multiple_vs_applied_peer_multiple_note_present():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "mega_cap_tech": True,
            "peer_count": 6,
            "peer_quality": "normal",
            "ev_ebitda_range": [20.0, 28.0],
            "ev_ebitda_median": 24.0,
            "ev_ebitda_weighted": 24.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    assert any("Live EV/EBITDA check:" in n for n in out.notes)


def test_high_dispersion_caps_comps_weight_for_mega_cap():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "peer_count": 9,
            "peer_quality": "strong",
            "ev_ebitda_range": [60.0, 80.0],
            "ev_ebitda_median": 70.0,
            "ev_ebitda_weighted": 70.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    assert out.weights_used["comps"] <= 0.35
    assert any("High pre-blend DCF/comps dispersion" in n for n in out.notes)


def test_low_effective_peer_count_caps_comps_weight_aggressively():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "peer_count": 4,
            "effective_peer_count": 2.2,
            "quick_mode": True,
            "peer_quality": "mixed",
            "ev_ebitda_range": [18.0, 24.0],
            "ev_ebitda_median": 20.0,
            "ev_ebitda_weighted": 20.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    assert out.weights_used["comps"] <= 0.15
    assert any("Weak peer diversification guardrail" in n for n in out.notes)


def test_mega_cap_dcf_cross_checks_and_status_tiering():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "mega_cap_tech": True,
            "peer_count": 6,
            "peer_quality": "normal",
            "ev_ebitda_range": [20.0, 28.0],
            "ev_ebitda_median": 24.0,
            "ev_ebitda_weighted": 24.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    assert any("Multiple cross-check:" in n for n in out.notes)
    assert any("Normalized terminal multiple cross-check:" in n for n in out.notes)

    implied = None
    for n in out.notes:
        if str(n).startswith("DCF implied exit EV/EBITDA:"):
            try:
                implied = float(str(n).split(":", 1)[1].replace("x.", "").replace("x", "").strip())
            except Exception:
                implied = None
            break
    assert implied is not None
    dcf_status = out.field_sources.get("DCF Status", ("normal", "", ""))[0]
    if implied < 10.0:
        assert dcf_status == "materially conservative / degraded"
    elif implied < 12.0:
        assert dcf_status in {"conservative / degraded", "materially conservative / degraded"}


def test_materially_conservative_dcf_sets_indicative_note_and_comps_floor():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "mega_cap_tech": True,
            "peer_count": 6,
            "peer_quality": "mixed",
            "low_confidence_comps": True,
            "ev_ebitda_range": [20.0, 28.0],
            "ev_ebitda_median": 24.0,
            "ev_ebitda_weighted": 24.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    dcf_status = out.field_sources.get("DCF Status", ("normal", "", ""))[0]
    if dcf_status == "materially conservative / degraded":
        assert out.weights_used.get("comps", 0.0) <= 0.40
        assert any("Point estimate is indicative only" in n for n in out.notes)


def test_materially_conservative_rule_reweights_toward_comps_when_peers_usable():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "mega_cap_tech": True,
            "peer_count": 6,
            "peer_quality": "normal",
            "low_confidence_comps": False,
            "ev_ebitda_range": [20.0, 28.0],
            "ev_ebitda_median": 24.0,
            "ev_ebitda_weighted": 24.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    dcf_status = out.field_sources.get("DCF Status", ("normal", "", ""))[0]
    if dcf_status == "materially conservative / degraded":
        assert out.weights_used.get("dcf", 1.0) <= 0.50
        assert out.weights_used.get("comps", 0.0) >= 0.50
        assert any("reweighted DCF/Comps" in n for n in out.notes)


def test_low_confidence_mega_cap_never_uses_5050_when_effective_peers_below_5():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={
            "_assumption_source": "system",
            "mega_cap_tech": True,
            "peer_count": 6,
            "effective_peer_count": 4.66,
            "peer_quality": "mixed",
            "low_confidence_comps": True,
            "ev_ebitda_range": [20.0, 28.0],
            "ev_ebitda_median": 24.0,
            "ev_ebitda_weighted": 24.0,
        },
        market_data=market_data,
        sector="Technology",
    )
    assert out.weights_used.get("dcf", 0.0) >= 0.60
    assert out.weights_used.get("comps", 1.0) <= 0.40


def test_financial_sector_uses_pe_pb_valuation_path():
    svc = ValuationService()
    financials = _base_financials()
    market_data = _base_market_data()
    market_data.sector = "Financial Services"
    out = svc.run_full_valuation(
        financials=financials,
        assumptions={"_assumption_source": "system"},
        market_data=market_data,
        sector="Financial Services",
    )
    assert out.valuation_path == "pe_pb"
