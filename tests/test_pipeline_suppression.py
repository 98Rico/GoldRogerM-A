from __future__ import annotations

from goldroger.data.fetcher import MarketData
from goldroger.data.filings import FilingsPack
from goldroger.data.market_context import MarketContextItem, MarketContextPack
from goldroger.data.comparables import PeerData, PeerMultiples
from goldroger.finance.core.valuation_service import (
    FullValuationOutput,
    RecommendationOutput,
)
from goldroger.finance.valuation.aggregator import ValuationResult
from goldroger.finance.valuation.comps import CompsOutput
from goldroger.finance.valuation.dcf import DCFOutput
from goldroger.finance.valuation.transactions import TransactionOutput
from goldroger.cli import print_result
from goldroger.models import Fundamentals, InvestmentThesis
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
    assert str(ps.get("report_mode")) == "quick"


def test_standard_report_mode_hides_long_scenario_narratives(monkeypatch):
    import goldroger.pipelines.equity as eq

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
    monkeypatch.setattr(
        eq,
        "build_market_context_pack",
        lambda **kwargs: MarketContextPack(
            source_backed=True,
            source_count=1,
            trends=[MarketContextItem(text="Demand trend", source="example", date="2026-05-10", confidence="medium", url="https://example.com/a")],
            catalysts=[],
            risks=[],
            fallback_used=False,
            note="",
        ),
    )

    def _parse_with_thesis(agent, company, company_type, context, model_class, fallback, **kwargs):
        if model_class is Fundamentals:
            return Fundamentals(
                company_name=company,
                description="Stub fundamentals",
                business_model="Stub business model",
                sector="Technology",
            )
        if model_class is InvestmentThesis:
            return InvestmentThesis(
                thesis="Stub thesis",
                bull_case="Bull narrative with specifics",
                base_case="Base narrative with specifics",
                bear_case="Bear narrative with specifics",
                catalysts=["Catalyst A"],
            )
        if hasattr(fallback, "model_copy"):
            return fallback.model_copy()
        return fallback

    monkeypatch.setattr(eq, "_parse_with_retry", _parse_with_thesis)

    analysis = run_analysis(
        "AAPL",
        company_type="public",
        quick_mode=False,
        full_report=False,
        cli_mode=False,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("report_mode")) == "standard"
    assert analysis.thesis.bull_case in {"", None}
    assert analysis.thesis.base_case in {"", None}
    assert analysis.thesis.bear_case in {"", None}


def test_full_report_mode_keeps_scenario_narratives(monkeypatch):
    import goldroger.pipelines.equity as eq

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
    monkeypatch.setattr(
        eq,
        "build_market_context_pack",
        lambda **kwargs: MarketContextPack(
            source_backed=True,
            source_count=1,
            trends=[MarketContextItem(text="Demand trend", source="example", date="2026-05-10", confidence="medium", url="https://example.com/a")],
            catalysts=[],
            risks=[],
            fallback_used=False,
            note="",
        ),
    )

    def _parse_with_thesis(agent, company, company_type, context, model_class, fallback, **kwargs):
        if model_class is Fundamentals:
            return Fundamentals(
                company_name=company,
                description="Stub fundamentals",
                business_model="Stub business model",
                sector="Technology",
            )
        if model_class is InvestmentThesis:
            return InvestmentThesis(
                thesis="Stub thesis",
                bull_case="Bull narrative with specifics",
                base_case="Base narrative with specifics",
                bear_case="Bear narrative with specifics",
                catalysts=["Catalyst A"],
            )
        if hasattr(fallback, "model_copy"):
            return fallback.model_copy()
        return fallback

    monkeypatch.setattr(eq, "_parse_with_retry", _parse_with_thesis)

    analysis = run_analysis(
        "AAPL",
        company_type="public",
        quick_mode=False,
        full_report=True,
        cli_mode=False,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("report_mode")) == "full"
    assert analysis.thesis.bull_case == "Bull narrative with specifics"
    assert analysis.thesis.base_case == "Base narrative with specifics"
    assert analysis.thesis.bear_case == "Bear narrative with specifics"


def test_source_backed_context_without_quant_inputs_stays_qualitative_only(monkeypatch):
    import goldroger.pipelines.equity as eq

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
    monkeypatch.setattr(
        eq,
        "build_market_context_pack",
        lambda **kwargs: MarketContextPack(
            source_backed=True,
            source_count=2,
            trends=[
                MarketContextItem(
                    text="Demand trend (source-backed)",
                    source="example",
                    date="2026-05-10",
                    confidence="medium",
                    url="https://example.com/a",
                )
            ],
            catalysts=[],
            risks=[],
            fallback_used=False,
            note="",
        ),
    )

    analysis = run_analysis(
        "AAPL",
        company_type="public",
        quick_mode=False,
        full_report=False,
        cli_mode=False,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("market_context_source_backed")) == "yes"
    assert str(ps.get("research_source")) == "source_backed"
    assert bool(ps.get("source_backed_market_context_available")) is True
    # Qualitative context is available, but quant assumptions still unresolved in this stub.
    assert bool(ps.get("source_backed_quant_market_inputs_available")) is False
    assert bool(ps.get("source_backed_quant_market_inputs_used_in_valuation")) is False


def test_reportwriter_timeout_uses_fast_structured_fallback(monkeypatch):
    import time
    import goldroger.pipelines.equity as eq

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
    monkeypatch.setattr(eq, "_REPORT_WRITER_TIMEOUT_STANDARD", 1)
    monkeypatch.setattr(
        eq,
        "build_market_context_pack",
        lambda **kwargs: MarketContextPack(
            source_backed=True,
            source_count=2,
            fetched_source_count=3,
            relevant_source_count=2,
            trends=[MarketContextItem(text="Apple demand trend", source="example", date="2026-05-10", confidence="medium", url="https://example.com/apple")],
            catalysts=[],
            risks=[],
            fallback_used=False,
            note="",
        ),
    )

    def _slow_parse(agent, company, company_type, context, model_class, fallback, **kwargs):
        if model_class is Fundamentals:
            return Fundamentals(
                company_name=company,
                description="Stub fundamentals",
                business_model="Stub business model",
                sector="Technology",
            )
        if model_class is InvestmentThesis:
            time.sleep(3)
            return InvestmentThesis(thesis="slow thesis")
        if hasattr(fallback, "model_copy"):
            return fallback.model_copy()
        return fallback

    monkeypatch.setattr(eq, "_parse_with_retry", _slow_parse)
    t0 = time.perf_counter()
    analysis = run_analysis(
        "AAPL",
        company_type="public",
        quick_mode=False,
        full_report=False,
        cli_mode=False,
    )
    elapsed = time.perf_counter() - t0
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    timings = (analysis.data_quality or {}).get("timings_s", {})
    assert str(ps.get("thesis")) == "TIMEOUT"
    assert elapsed < 3.2
    assert float(timings.get("thesis") or 0.0) <= 1.8
    assert "device upgrade cycles" in str((analysis.thesis.thesis or "")).lower()


def test_cyclical_and_extreme_signal_guardrails_cap_recommendation(monkeypatch):
    import goldroger.pipelines.equity as eq

    md = MarketData(
        ticker="NHY.OL",
        company_name="Norsk Hydro ASA",
        sector="Materials",
        current_price=60.0,
        market_cap=70000.0,
        shares_outstanding=2000.0,
        total_debt=18000.0,
        cash_and_equivalents=5000.0,
        net_debt=13000.0,
        enterprise_value=35000.0,
        revenue_ttm=200000.0,
        ebitda_ttm=20000.0,
        ebitda_margin=0.10,
        fcf_ttm=2500.0,
        ev_ebitda_market=8.0,
        revenue_history=[],  # force cyclical-review-required path
        additional_metadata={
            "industry": "Aluminum",
            "country": "Norway",
            "exchange": "OSL",
            "quote_currency": "NOK",
            "financial_currency": "NOK",
            "market_cap_currency": "NOK",
            "quote_type": "EQUITY",
            "underlying_symbol": "NHY.OL",
            "is_adr_hint": False,
        },
    )
    _install_public_stubs(monkeypatch, "NHY.OL", md)
    def _parse_materials(agent, company, company_type, context, model_class, fallback, **kwargs):
        if model_class is Fundamentals:
            return Fundamentals(
                company_name=company,
                description="Stub fundamentals",
                business_model="Aluminum producer",
                sector="Materials",
            )
        if hasattr(fallback, "model_copy"):
            return fallback.model_copy()
        return fallback
    monkeypatch.setattr(eq, "_parse_with_retry", _parse_materials)

    def _stub_high_upside(self, financials, assumptions, market_data=None, sector="", company_type="public"):
        dcf = DCFOutput(
            free_cash_flows=[1000.0, 1100.0, 1200.0],
            discounted_cash_flows=[900.0, 850.0, 800.0],
            terminal_value=15000.0,
            enterprise_value=48000.0,
            terminal_value_pct=0.72,
        )
        comps = CompsOutput(low=42000.0, mid=46000.0, high=50000.0)
        tx = TransactionOutput(implied_value=30000.0)
        blended = ValuationResult(low=43000.0, mid=47000.0, high=51000.0, blended=47000.0)
        rec = RecommendationOutput(
            recommendation="BUY",
            upside_pct=0.95,
            intrinsic_price=120.0,
            current_price=60.0,
            market_cap=70000.0,
            ev_blended=47000.0,
        )
        return FullValuationOutput(
            dcf=dcf,
            comps=comps,
            transactions=tx,
            blended=blended,
            lbo=None,
            recommendation=rec,
            sensitivity=None,
            wacc_used=0.09,
            terminal_growth_used=0.02,
            data_confidence="verified",
            sector=sector or "Materials",
            valuation_path="ev_ebitda",
            has_revenue=True,
            weights_used={"dcf": 0.6, "comps": 0.4, "transactions": 0.0},
            notes=[],
            field_sources={"DCF Status": ("normal", "valuation_engine", "inferred")},
        )

    monkeypatch.setattr(eq.ValuationService, "run_full_valuation", _stub_high_upside)
    analysis = run_analysis(
        "Norsk Hydro",
        company_type="public",
        quick_mode=True,
        cli_mode=True,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert bool(ps.get("cyclical_review_required")) is True
    assert bool(ps.get("extreme_signal_review")) is True
    assert str(analysis.valuation.recommendation) in {"WATCH / REVIEW REQUIRED", "WATCH / LOW CONVICTION", "HOLD / LOW CONVICTION"}
    assert "extreme_signal_review" in str(ps.get("confidence_reason", ""))


def test_mature_extreme_upside_signal_gets_review_cap(monkeypatch):
    import goldroger.pipelines.equity as eq

    md = MarketData(
        ticker="BATS.L",
        company_name="British American Tobacco p.l.c.",
        sector="Consumer Staples",
        current_price=32.0,
        market_cap=100000.0,
        shares_outstanding=3100.0,
        total_debt=42000.0,
        cash_and_equivalents=9000.0,
        net_debt=33000.0,
        enterprise_value=133000.0,
        revenue_ttm=26000.0,
        ebitda_ttm=12000.0,
        ebitda_margin=0.46,
        fcf_ttm=3000.0,
        ev_ebitda_market=9.5,
        additional_metadata={
            "industry": "Tobacco",
            "country": "United Kingdom",
            "exchange": "LSE",
            "quote_currency": "GBP",
            "financial_currency": "GBP",
            "market_cap_currency": "GBP",
            "quote_type": "EQUITY",
            "underlying_symbol": "BATS.L",
            "is_adr_hint": False,
        },
    )
    _install_public_stubs(monkeypatch, "BATS.L", md)

    def _stub_extreme(self, financials, assumptions, market_data=None, sector="", company_type="public"):
        dcf = DCFOutput(
            free_cash_flows=[1000.0, 1000.0, 1000.0],
            discounted_cash_flows=[900.0, 820.0, 750.0],
            terminal_value=15000.0,
            enterprise_value=150000.0,
            terminal_value_pct=0.70,
        )
        comps = CompsOutput(low=90000.0, mid=100000.0, high=110000.0)
        tx = TransactionOutput(implied_value=0.0)
        blended = ValuationResult(low=98000.0, mid=132000.0, high=154000.0, blended=132000.0)
        rec = RecommendationOutput(
            recommendation="BUY",
            upside_pct=0.99,
            intrinsic_price=64.0,
            current_price=32.0,
            market_cap=100000.0,
            ev_blended=132000.0,
        )
        return FullValuationOutput(
            dcf=dcf,
            comps=comps,
            transactions=tx,
            blended=blended,
            lbo=None,
            recommendation=rec,
            sensitivity=None,
            wacc_used=0.09,
            terminal_growth_used=0.02,
            data_confidence="verified",
            sector=sector or "Consumer Staples",
            valuation_path="ev_ebitda",
            has_revenue=True,
            weights_used={"dcf": 0.6, "comps": 0.4, "transactions": 0.0},
            notes=[],
            field_sources={"DCF Status": ("normal", "valuation_engine", "inferred")},
        )

    monkeypatch.setattr(eq.ValuationService, "run_full_valuation", _stub_extreme)
    analysis = run_analysis(
        "British American Tobacco",
        company_type="public",
        quick_mode=True,
        cli_mode=True,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert bool(ps.get("extreme_signal_review")) is True
    assert "extreme_signal_review" in str(ps.get("confidence_reason", ""))
    assert str(ps.get("model_signal_detail", "")).lower().startswith("positive")
    assert str(analysis.valuation.recommendation) in {
        "HOLD / LOW CONVICTION",
        "WATCH / REVIEW REQUIRED",
        "INCONCLUSIVE",
    }
    assert str(ps.get("recommendation")) != str(ps.get("model_signal_detail"))
    assert str(ps.get("recommendation_cap_reason", "")).strip()
    assert "missing:" in str(ps.get("confidence_reason", "")).lower()


def test_fallback_market_context_banner_not_duplicated_in_trend_rows(monkeypatch):
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
        quick_mode=False,
        full_report=False,
        cli_mode=True,
    )
    trend_rows = [str(x) for x in (analysis.market.key_trends or [])]
    assert all(
        not row.lower().startswith("fallback market context — sector profile only")
        for row in trend_rows
    )


def test_archetype_market_segment_backfill_avoids_missing_segment_warning(monkeypatch):
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
    segment_txt = str((analysis.market.market_segment or "")).lower()
    warnings = [str(w) for w in ((analysis.data_quality or {}).get("warnings") or [])]
    assert "consumer hardware and services ecosystem" in segment_txt
    assert all("missing market segment definition" not in w.lower() for w in warnings)


def test_extreme_signal_capped_recommendation_renders_diagnostic_headline(monkeypatch, capsys):
    import goldroger.pipelines.equity as eq

    md = MarketData(
        ticker="BATS.L",
        company_name="British American Tobacco p.l.c.",
        sector="Consumer Staples",
        current_price=32.0,
        market_cap=100000.0,
        shares_outstanding=3100.0,
        total_debt=42000.0,
        cash_and_equivalents=9000.0,
        net_debt=33000.0,
        enterprise_value=133000.0,
        revenue_ttm=26000.0,
        ebitda_ttm=12000.0,
        ebitda_margin=0.46,
        fcf_ttm=3000.0,
        ev_ebitda_market=9.5,
        additional_metadata={
            "industry": "Tobacco",
            "country": "United Kingdom",
            "exchange": "LSE",
            "quote_currency": "GBP",
            "financial_currency": "GBP",
            "market_cap_currency": "GBP",
            "quote_type": "EQUITY",
            "underlying_symbol": "BATS.L",
            "is_adr_hint": False,
        },
    )
    _install_public_stubs(monkeypatch, "BATS.L", md)

    def _stub_extreme(self, financials, assumptions, market_data=None, sector="", company_type="public"):
        dcf = DCFOutput(
            free_cash_flows=[1000.0, 1000.0, 1000.0],
            discounted_cash_flows=[900.0, 820.0, 750.0],
            terminal_value=15000.0,
            enterprise_value=150000.0,
            terminal_value_pct=0.70,
        )
        comps = CompsOutput(low=90000.0, mid=100000.0, high=110000.0)
        tx = TransactionOutput(implied_value=0.0)
        blended = ValuationResult(low=98000.0, mid=132000.0, high=154000.0, blended=132000.0)
        rec = RecommendationOutput(
            recommendation="BUY",
            upside_pct=0.99,
            intrinsic_price=64.0,
            current_price=32.0,
            market_cap=100000.0,
            ev_blended=132000.0,
        )
        return FullValuationOutput(
            dcf=dcf,
            comps=comps,
            transactions=tx,
            blended=blended,
            lbo=None,
            recommendation=rec,
            sensitivity=None,
            wacc_used=0.09,
            terminal_growth_used=0.02,
            data_confidence="verified",
            sector=sector or "Consumer Staples",
            valuation_path="ev_ebitda",
            has_revenue=True,
            weights_used={"dcf": 0.6, "comps": 0.4, "transactions": 0.0},
            notes=[],
            field_sources={"DCF Status": ("normal", "valuation_engine", "inferred")},
        )

    monkeypatch.setattr(eq.ValuationService, "run_full_valuation", _stub_extreme)
    analysis = run_analysis(
        "British American Tobacco",
        company_type="public",
        quick_mode=True,
        cli_mode=True,
    )
    print_result(analysis)
    out = capsys.readouterr().out.lower()
    assert "diagnostic model value" in out
    assert "capped pending corroboration" in out
