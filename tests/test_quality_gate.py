from goldroger.data.fetcher import MarketData
from goldroger.data.quality_gate import assess_data_quality


def test_public_quality_gate_high_score_with_complete_data():
    md = MarketData(
        ticker="NVDA",
        company_name="NVIDIA",
        sector="Technology",
        revenue_ttm=100000.0,
        ebitda_margin=0.35,
        market_cap=3000000.0,
        ev_ebitda_market=30.0,
        beta=1.2,
    )
    out = assess_data_quality("public", md, {"revenue_current": 100000.0})
    assert out.score >= 80
    assert out.tier in {"A", "B"}
    assert out.is_blocked is False


def test_private_quality_gate_blocks_when_revenue_missing():
    md = MarketData(
        ticker="",
        company_name="PrivateCo",
        sector="HealthTech",
        confidence="inferred",
    )
    out = assess_data_quality("private", md, {"revenue_current": None})
    assert out.is_blocked is True
    assert "Missing revenue" in out.blockers
    assert out.tier in {"C", "D"}


def test_public_quality_penalized_when_market_context_missing():
    md = MarketData(
        ticker="AAPL",
        company_name="Apple",
        sector="Technology",
        revenue_ttm=400000.0,
        ebitda_margin=0.34,
        market_cap=3000000.0,
        ev_ebitda_market=25.0,
        beta=1.0,
    )
    out = assess_data_quality(
        "public",
        md,
        {"revenue_current": 400000.0},
        market_analysis={
            "market_size": "Not available",
            "market_growth": "Not available",
            "market_segment": "",
            "key_trends": [],
        },
    )
    assert out.score <= 80
    assert out.checks.get("market_size") == "missing"
    assert out.checks.get("market_growth") == "missing"
