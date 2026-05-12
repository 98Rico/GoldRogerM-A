from __future__ import annotations

from goldroger.data.fetcher import MarketData
from goldroger.data.filings import FilingsPack
from goldroger.data.market_context import MarketContextPack
from goldroger.data.comparables import PeerData, PeerMultiples
from goldroger.models import Fundamentals
from goldroger.pipelines.equity import run_analysis


def _stub_parse_with_retry(agent, company, company_type, context, model_class, fallback, **kwargs):
    # Deterministic parser stub for pipeline-level regression checks.
    if model_class is Fundamentals:
        return Fundamentals(
            company_name=company,
            description="Stub fundamentals",
            business_model="Stub business model",
            sector="Technology",
        )
    if hasattr(fallback, "model_copy"):
        return fallback.model_copy()
    return fallback


def _install_public_stubs(monkeypatch, ticker: str, md: MarketData) -> None:
    import goldroger.pipelines.equity as eq

    monkeypatch.setattr(eq, "_client", lambda llm=None: object())
    monkeypatch.setattr(eq, "_parse_with_retry", _stub_parse_with_retry)
    monkeypatch.setattr(eq, "resolve_ticker", lambda company: ticker)
    monkeypatch.setattr(
        eq,
        "resolve_ticker_with_context",
        lambda company: {
            "selected_symbol": ticker,
            "primary_listing_symbol": md.additional_metadata.get("underlying_symbol", ticker),
            "selected_exchange": md.additional_metadata.get("exchange", "NMS"),
            "selected_quote_type": md.additional_metadata.get("quote_type", "EQUITY"),
            "selected_region": "US",
            "reason": "test_stub",
        },
    )
    monkeypatch.setattr(eq, "fetch_market_data", lambda _t: md)
    monkeypatch.setattr(
        eq,
        "find_peers_deterministic_quick",
        lambda **kwargs: ["MSFT", "ORCL", "CSCO", "NVDA", "AVGO", "MU"],
    )
    def _stub_peer_multiples(*args, **kwargs):
        peers = [
            PeerData(
                name="Microsoft",
                ticker="MSFT",
                ev_ebitda=18.0,
                market_cap=3200000.0,
                role="adjacent valuation peer",
                bucket="software_services_platform",
                weight=0.30,
            ),
            PeerData(
                name="Cisco",
                ticker="CSCO",
                ev_ebitda=14.0,
                market_cap=350000.0,
                role="adjacent valuation peer",
                bucket="networking_infrastructure",
                weight=0.18,
            ),
            PeerData(
                name="NVIDIA",
                ticker="NVDA",
                ev_ebitda=28.0,
                market_cap=5000000.0,
                role="adjacent valuation peer",
                bucket="semiconductors",
                weight=0.12,
            ),
        ]
        return PeerMultiples(
            peers=peers,
            ev_ebitda_median=23.0,
            ev_ebitda_raw_median=23.0,
            ev_ebitda_weighted=22.5,
            ev_revenue_median=6.0,
            ev_ebitda_low=18.0,
            ev_ebitda_high=28.0,
            ev_revenue_low=4.0,
            ev_revenue_high=8.0,
            n_peers=6,
            n_valuation_peers=6,
            n_qualitative_peers=0,
            effective_peer_count=4.8,
            pure_peer_weight_share=0.0,
            adjacent_peer_weight_share=1.0,
            peer_set_type="adjacent_reference_set",
            source="yfinance_peers",
        )
    monkeypatch.setattr(eq, "build_peer_multiples", _stub_peer_multiples)
    monkeypatch.setattr(
        eq,
        "build_filings_pack",
        lambda **kwargs: FilingsPack(
            company=str(kwargs.get("company") or "Test Co"),
            ticker=str(kwargs.get("ticker") or ticker),
            source_backed=False,
            source_count=0,
            records=[],
            fallback_used=True,
            note="test stub",
        ),
    )
    monkeypatch.setattr(
        eq,
        "build_market_context_pack",
        lambda **kwargs: MarketContextPack(
            source_backed=False,
            source_count=0,
            trends=[],
            catalysts=[],
            risks=[],
            fallback_used=True,
            note="test stub",
        ),
    )


def test_pipeline_suppresses_recommendation_when_normalization_fails(monkeypatch):
    md = MarketData(
        ticker="NHYDY",
        company_name="Norsk Hydro ASA",
        sector="Materials",
        current_price=6.5,
        market_cap=22261.0,
        shares_outstanding=100.0,  # force unresolved share-basis consistency
        total_debt=18000.0,
        cash_and_equivalents=5000.0,
        net_debt=13000.0,
        enterprise_value=35261.0,
        revenue_ttm=201266.0,  # NOK millions
        ebitda_ttm=26000.0,  # NOK millions
        ebitda_margin=0.13,
        fcf_ttm=22200.0,
        ev_ebitda_market=0.9,  # triggers sanity breaker in addition to normalization failure
        additional_metadata={
            "industry": "Aluminum",
            "country": "Norway",
            "exchange": "OTC",
            "quote_currency": "USD",
            "financial_currency": "NOK",
            "market_cap_currency": "USD",
            "quote_type": "EQUITY",
            "underlying_symbol": "NHY.OL",
            "is_adr_hint": True,
            "adr_ratio": None,
        },
    )
    _install_public_stubs(monkeypatch, "NHYDY", md)

    analysis = run_analysis(
        "Norsk Hydro",
        company_type="public",
        quick_mode=True,
        cli_mode=True,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert analysis.valuation.recommendation == "INCONCLUSIVE"
    assert analysis.valuation.target_price in {None, "N/A"}
    assert str(ps.get("normalization_status")) == "FAILED"
    assert bool(ps.get("sanity_breaker_triggered")) is True
    assert str(ps.get("recommendation")) == "INCONCLUSIVE"
    assert str(ps.get("valuation")) == "FAILED"


def test_pipeline_keeps_aapl_actionable_when_normalization_ok(monkeypatch):
    md = MarketData(
        ticker="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        current_price=200.0,
        market_cap=4200000.0,
        shares_outstanding=21000.0,
        total_debt=120000.0,
        cash_and_equivalents=60000.0,
        net_debt=60000.0,
        enterprise_value=4260000.0,
        revenue_ttm=400000.0,
        ebitda_ttm=130000.0,
        ebitda_margin=0.325,
        fcf_ttm=95000.0,
        ev_ebitda_market=25.0,
        additional_metadata={
            "industry": "Consumer Electronics",
            "country": "United States",
            "exchange": "NMS",
            "quote_currency": "USD",
            "financial_currency": "USD",
            "market_cap_currency": "USD",
            "quote_type": "EQUITY",
            "underlying_symbol": "AAPL",
            "is_adr_hint": False,
        },
    )
    _install_public_stubs(monkeypatch, "AAPL", md)

    analysis = run_analysis(
        "AAPL",
        company_type="public",
        quick_mode=True,
        cli_mode=True,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("normalization_status")) == "OK"
    assert bool(ps.get("sanity_breaker_triggered")) is False
    assert analysis.valuation.recommendation != "INCONCLUSIVE"
    assert str(ps.get("research_enrichment")) == "RESEARCH_SKIPPED_QUICK_MODE"
