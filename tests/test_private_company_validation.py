from __future__ import annotations

import copy
import re

import pytest

from goldroger.cli import _parse_sources_md
from goldroger.data.comparables import PeerData, PeerMultiples
from goldroger.data.fetcher import MarketData
from goldroger.data.private_triangulation import TriangulationResult, TriangulationSignal
from goldroger.data.source_selector import SourceSelectionResult
from goldroger.models import Fundamentals
from goldroger.pipelines.equity import run_analysis


def _stub_parse_with_retry(agent, company, company_type, context, model_class, fallback, **kwargs):
    if model_class is Fundamentals:
        return Fundamentals(
            company_name=company,
            description="Private company used for deterministic validation tests.",
            business_model="Private operating company",
            sector="Technology",
        )
    if hasattr(fallback, "model_copy"):
        return fallback.model_copy()
    return fallback


def _md(
    *,
    company: str,
    source: str,
    revenue_m: float | None,
    confidence: str,
    sector: str = "Technology",
    ebitda_margin: float | None = 0.20,
) -> MarketData:
    return MarketData(
        ticker=company.upper()[:6],
        company_name=company,
        sector=sector,
        revenue_ttm=revenue_m,
        ebitda_margin=ebitda_margin,
        confidence=confidence,
        data_source=source,
        additional_metadata={},
    )


def _peer_stub() -> PeerMultiples:
    peers = [
        PeerData(name="Adobe", ticker="ADBE", ev_ebitda=18.0, market_cap=450000.0, bucket="software_services_platform", role="adjacent valuation peer", weight=0.30),
        PeerData(name="Oracle", ticker="ORCL", ev_ebitda=15.0, market_cap=330000.0, bucket="software_services_platform", role="adjacent valuation peer", weight=0.25),
        PeerData(name="SAP", ticker="SAP", ev_ebitda=17.0, market_cap=250000.0, bucket="software_services_platform", role="adjacent valuation peer", weight=0.25),
        PeerData(name="Intuit", ticker="INTU", ev_ebitda=20.0, market_cap=180000.0, bucket="software_services_platform", role="adjacent valuation peer", weight=0.20),
    ]
    return PeerMultiples(
        peers=peers,
        ev_ebitda_median=17.5,
        ev_ebitda_raw_median=17.5,
        ev_ebitda_weighted=17.4,
        ev_revenue_median=5.0,
        ev_ebitda_low=15.0,
        ev_ebitda_high=20.0,
        ev_revenue_low=4.0,
        ev_revenue_high=7.0,
        n_peers=4,
        n_valuation_peers=4,
        n_qualitative_peers=0,
        effective_peer_count=3.6,
        pure_peer_weight_share=0.80,
        adjacent_peer_weight_share=0.20,
        peer_set_type="mixed_comps_ok",
        source="yfinance_peers",
    )


def _qual_only_peer_stub() -> PeerMultiples:
    peers = [
        PeerData(name="PayPal", ticker="PYPL", ev_ebitda=6.0, market_cap=40_000.0, bucket="fintech_payments", role="qualitative peer only", weight=0.0),
        PeerData(name="Nubank", ticker="NU", ev_ebitda=None, market_cap=60_000.0, bucket="fintech_digital_bank", role="qualitative peer only", weight=0.0),
    ]
    return PeerMultiples(
        peers=peers,
        n_peers=2,
        n_valuation_peers=0,
        n_qualitative_peers=2,
        effective_peer_count=0.0,
        pure_peer_weight_share=0.0,
        adjacent_peer_weight_share=1.0,
        peer_set_type="adjacent_reference_set",
        source="yfinance_peers_low_confidence",
    )


def _run_private_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    company: str,
    registry_md: MarketData | None,
    selected_providers: list[str] | None = None,
    provider_results: dict[str, MarketData | None] | None = None,
    triangulation_result: TriangulationResult | None = None,
    country_hint: str = "",
    skipped_missing_credentials: list[str] | None = None,
    manual_revenue: float | None = None,
    manual_revenue_currency: str = "USD",
    manual_revenue_year: int | None = None,
    manual_revenue_source_note: str = "",
    manual_ebitda_margin: float | None = None,
    manual_growth: float | None = None,
    manual_net_debt: float | None = None,
    manual_identity_confirmed: bool = False,
    company_identifier: str = "",
    peer_multiples_override: PeerMultiples | None = None,
    quick_mode: bool = True,
):
    import goldroger.data.private_triangulation as tri_mod
    import goldroger.data.source_selector as selector_mod
    import goldroger.pipelines.equity as eq

    selected = list(selected_providers or [])
    provider_map = dict(provider_results or {})

    monkeypatch.setattr(eq, "_client", lambda llm=None: object())
    monkeypatch.setattr(eq, "_parse_with_retry", _stub_parse_with_retry)
    monkeypatch.setattr(eq, "find_peers_deterministic_quick", lambda **kwargs: ["ADBE", "ORCL", "SAP", "INTU"])
    monkeypatch.setattr(eq, "build_peer_multiples", lambda *args, **kwargs: (peer_multiples_override or _peer_stub()))
    monkeypatch.setattr(eq.DEFAULT_REGISTRY, "fetch_by_name", lambda _company, country_hint="": copy.deepcopy(registry_md))
    monkeypatch.setattr(
        selector_mod,
        "resolve_source_selection",
        lambda requested=None, country_hint="": SourceSelectionResult(
            selected_providers=list(selected),
            manual_revenue_usd_m=None,
            country_iso=(country_hint or None),
            unknown_sources=[],
            skipped_missing_credentials=list(skipped_missing_credentials or []),
            requested_sources=list(requested or []),
        ),
    )
    monkeypatch.setattr(
        eq,
        "_fetch_provider",
        lambda provider_name, _company, siren=None: copy.deepcopy(provider_map.get(provider_name)),
    )
    monkeypatch.setattr(
        tri_mod,
        "triangulate_revenue",
        lambda company_name, sector="", country="", crunchbase_data=None: triangulation_result,
    )

    return run_analysis(
        company=company,
        company_type="private",
        quick_mode=quick_mode,
        cli_mode=True,
        country_hint=country_hint,
        company_identifier=company_identifier,
        data_sources=selected if selected else ["auto"],
        manual_revenue=manual_revenue,
        manual_revenue_currency=manual_revenue_currency,
        manual_revenue_year=manual_revenue_year,
        manual_revenue_source_note=manual_revenue_source_note,
        manual_ebitda_margin=manual_ebitda_margin,
        manual_growth=manual_growth,
        manual_net_debt=manual_net_debt,
        manual_identity_confirmed=manual_identity_confirmed,
    )


def _summarize_private_validation(input_query: str, country: str, analysis) -> dict:
    ps = ((analysis.data_quality or {}).get("pipeline_status") or {}) if isinstance(analysis.data_quality, dict) else {}
    src = _parse_sources_md(getattr(analysis, "sources_md", ""))
    rev_entry = src.get("Revenue TTM", {})
    rev_value = str(rev_entry.get("value", "") or "")
    rev_conf = str(rev_entry.get("confidence", "unknown") or "unknown").lower()
    rev_source = str(rev_entry.get("source", "unknown") or "unknown")
    rev_currency = "USD"
    m = re.match(r"^([A-Z]{3})\s+", rev_value)
    if m:
        rev_currency = m.group(1)
    elif rev_value.startswith("$"):
        rev_currency = "USD"
    legal_id = (
        (src.get("Company Number (GB)", {}) or {}).get("value")
        or (src.get("SIREN", {}) or {}).get("value")
        or ""
    )
    valuation_status = str(ps.get("valuation", "UNKNOWN") or "UNKNOWN")
    recommendation = str(getattr(analysis.valuation, "recommendation", "") or "")
    confidence = str(ps.get("confidence", "unknown") or "unknown")
    tri_used = bool(ps.get("private_triangulation_used"))
    revenue_status = str(ps.get("private_revenue_status", rev_conf or "unknown") or "unknown").lower()
    provenance_complete = all(
        [
            "Revenue TTM" in src,
            "Data Quality Score" in src,
            "Private Revenue Status" in src,
        ]
    )
    suppressed_or_degraded = bool(
        recommendation.upper().startswith("INCONCLUSIVE")
        or valuation_status in {"FAILED", "DEGRADED"}
    )
    exports_safe = not recommendation.upper().startswith("INCONCLUSIVE")
    return {
        "input_query": input_query,
        "resolved_name": str(getattr(analysis.fundamentals, "company_name", "") or ""),
        "country": country,
        "legal_identifier": str(legal_id),
        "revenue_source": rev_source,
        "revenue_value": rev_value,
        "revenue_currency": rev_currency,
        "revenue_confidence": rev_conf,
        "revenue_status": revenue_status,
        "triangulation_used": tri_used,
        "valuation_status": valuation_status,
        "private_recommendation": recommendation,
        "confidence_level": confidence,
        "suppressed_or_degraded": suppressed_or_degraded,
        "exports_safe": exports_safe,
        "provenance_completeness": provenance_complete,
    }


def test_private_no_verified_revenue_is_inconclusive(monkeypatch):
    md = _md(company="NoRevenueCo", source="infogreffe", revenue_m=None, confidence="inferred")
    analysis = _run_private_case(
        monkeypatch,
        company="NoRevenueCo",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert analysis.valuation.recommendation == "INCONCLUSIVE"
    assert analysis.valuation.target_price in {None, "N/A"}
    assert str(ps.get("private_revenue_status")) == "unavailable"
    assert str(ps.get("private_revenue_quality")) == "UNAVAILABLE"
    assert str(ps.get("private_valuation_mode")) == "SCREEN_ONLY"
    assert analysis.football_field is None


def test_private_triangulated_revenue_is_tagged_and_capped(monkeypatch):
    md = _md(company="TriangulatedCo", source="infogreffe", revenue_m=None, confidence="inferred")
    tri = TriangulationResult(
        revenue_estimate_m=420.0,
        confidence="estimated",
        signals=[
            TriangulationSignal(estimate_m=430.0, confidence=0.55, source="press_nlp"),
            TriangulationSignal(estimate_m=410.0, confidence=0.60, source="wikipedia"),
        ],
        notes=["triangulation test"],
    )
    analysis = _run_private_case(
        monkeypatch,
        company="TriangulatedCo",
        registry_md=md,
        selected_providers=[],
        triangulation_result=tri,
        country_hint="FR",
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert bool(ps.get("private_triangulation_used")) is True
    assert str(ps.get("private_revenue_status")) == "estimated"
    assert str(ps.get("private_revenue_quality")) == "LOW_CONFIDENCE_ESTIMATE"
    assert str(ps.get("private_valuation_mode")) == "SCREEN_ONLY"
    assert str(analysis.valuation.recommendation) == "INCONCLUSIVE"
    assert analysis.valuation.target_price in {None, "N/A"}
    assert analysis.football_field is None
    assert "triangulation" in (analysis.sources_md or "").lower()


def test_private_verified_revenue_uses_private_label_taxonomy(monkeypatch):
    md = _md(company="VerifiedCo", source="pappers", revenue_m=620.0, confidence="verified")
    analysis = _run_private_case(
        monkeypatch,
        company="VerifiedCo",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
    )
    rec = str(analysis.valuation.recommendation or "")
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert not rec.startswith(("BUY", "SELL", "HOLD"))
    assert any(
        rec.startswith(lbl)
        for lbl in ("ATTRACTIVE ENTRY", "CONDITIONAL GO", "SELECTIVE BUY", "FULL PRICE", "NEUTRAL", "INCONCLUSIVE")
    )
    assert str(ps.get("private_revenue_quality")) in {"VERIFIED", "HIGH_CONFIDENCE_ESTIMATE"}
    assert str(ps.get("private_valuation_mode")) in {"VALUATION_GRADE", "SCREEN_ONLY"}


def test_private_provider_conflict_notes_are_in_provenance(monkeypatch):
    base = _md(company="ConflictCo", source="crunchbase", revenue_m=1200.0, confidence="estimated")
    providers = {
        "pappers": _md(company="ConflictCo", source="pappers", revenue_m=220.0, confidence="verified"),
        "companies_house": _md(company="ConflictCo", source="companies_house", revenue_m=210.0, confidence="verified"),
    }
    analysis = _run_private_case(
        monkeypatch,
        company="ConflictCo",
        registry_md=base,
        selected_providers=["pappers", "companies_house"],
        provider_results=providers,
        triangulation_result=None,
        country_hint="FR",
    )
    md_text = analysis.sources_md or ""
    assert "Private Data Merge Note" in md_text
    assert "Dropped outlier revenue candidates" in md_text
    assert "Private Revenue Status" in md_text


def test_private_unresolved_identity_is_not_high_conviction(monkeypatch):
    md = _md(company="WeakIdentityCo", source="crunchbase", revenue_m=300.0, confidence="estimated")
    analysis = _run_private_case(
        monkeypatch,
        company="WeakIdentityCo",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="",
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    rec = str(analysis.valuation.recommendation or "")
    assert bool(ps.get("private_identity_resolved")) is False
    assert str(ps.get("private_identity_status")) == "UNRESOLVED"
    assert str(ps.get("private_valuation_mode")) == "SCREEN_ONLY"
    assert analysis.football_field is None
    assert analysis.valuation.target_price in {None, "N/A"}
    assert rec.startswith("INCONCLUSIVE")


def test_private_missing_pappers_key_is_logged_as_limitation(monkeypatch):
    md = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred")
    analysis = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
        skipped_missing_credentials=["pappers"],
    )
    md_text = analysis.sources_md or ""
    assert "Private Revenue Limitation" in md_text
    assert "Pappers key is not configured" in md_text


def test_private_manual_revenue_enables_valuation_with_resolved_identity(monkeypatch):
    md = _md(company="Revolut Ltd", source="companies_house", revenue_m=None, confidence="inferred", sector="Technology")
    md.additional_metadata = {"company_number": "08804411", "financial_currency": "GBP"}
    analysis = _run_private_case(
        monkeypatch,
        company="Revolut Ltd",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="GB",
        company_identifier="08804411",
        manual_revenue=300.0,
        manual_revenue_currency="EUR",
        manual_revenue_year=2025,
        manual_revenue_source_note="prototype user estimate",
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("private_revenue_quality")) == "MANUAL"
    assert str(ps.get("private_valuation_mode")) == "INDICATIVE_MANUAL"
    assert str(ps.get("private_state")) == "VALUATION_READY_MANUAL_REVENUE"
    assert str(ps.get("private_identity_status")) == "RESOLVED_STRONG"
    assert bool(ps.get("private_manual_revenue_used")) is True
    assert str(analysis.valuation.recommendation).upper().startswith("INCONCLUSIVE")
    assert str(ps.get("confidence")).lower() in {"low", "medium"}
    assert "manual user-provided" in (analysis.sources_md or "").lower()


def test_private_manual_revenue_can_unlock_with_manual_identity_confirmation(monkeypatch):
    weak_md = _md(company="Personio", source="crunchbase", revenue_m=None, confidence="inferred", sector="Technology")
    analysis = _run_private_case(
        monkeypatch,
        company="Personio",
        registry_md=weak_md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="DE",
        manual_revenue=300.0,
        manual_revenue_currency="EUR",
        manual_identity_confirmed=True,
        manual_revenue_source_note="prototype user estimate",
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("private_revenue_quality")) == "MANUAL"
    assert bool(ps.get("private_manual_revenue_used")) is True
    assert str(ps.get("private_valuation_mode")) == "INDICATIVE_MANUAL"
    assert str(ps.get("private_state")) == "VALUATION_READY_MANUAL_REVENUE"
    assert str(ps.get("private_identity_status")) == "RESOLVED_MANUAL"
    assert str(ps.get("private_identity_source_state")) == "manual confirmed (unverified)"
    assert str(ps.get("confidence")).lower() in {"low", "medium"}
    assert str(analysis.valuation.recommendation).upper() in {"INDICATIVE / LOW CONVICTION", "INCONCLUSIVE"}
    assert "manual_user_input" in (analysis.sources_md or "").lower()


def test_private_manual_revenue_without_identity_confirmation_stays_screen_only(monkeypatch):
    weak_md = _md(company="Personio", source="crunchbase", revenue_m=None, confidence="inferred", sector="Technology")
    analysis = _run_private_case(
        monkeypatch,
        company="Personio",
        registry_md=weak_md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="DE",
        manual_revenue=300.0,
        manual_revenue_currency="EUR",
        manual_identity_confirmed=False,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("private_revenue_quality")) == "MANUAL"
    assert str(ps.get("private_identity_status")) == "UNRESOLVED"
    assert str(ps.get("private_valuation_mode")) == "SCREEN_ONLY"
    assert str(ps.get("private_state")) == "IDENTITY_UNRESOLVED"
    assert str(analysis.valuation.recommendation).upper().startswith("INCONCLUSIVE")


def test_private_manual_revenue_with_weak_identity_is_indicative_only(monkeypatch):
    weak_md = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred", sector="Healthcare")
    weak_md.additional_metadata = {"country": "France"}
    analysis = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=weak_md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
        manual_revenue=280.0,
        manual_revenue_currency="EUR",
        manual_revenue_year=2025,
        manual_revenue_source_note="prototype user estimate",
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("private_identity_status")) == "RESOLVED_WEAK"
    assert str(ps.get("private_revenue_quality")) == "MANUAL"
    assert str(ps.get("private_valuation_mode")) == "INDICATIVE_MANUAL"
    assert str(ps.get("private_state")) == "VALUATION_READY_MANUAL_REVENUE"
    assert str(ps.get("confidence")).lower() in {"low", "medium"}
    assert "LOW CONVICTION" in str(analysis.valuation.recommendation or "").upper()


def test_private_screen_only_still_surfaces_qualitative_peers(monkeypatch):
    md = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred", sector="Healthcare")
    qualitative = PeerMultiples(
        peers=[
            PeerData(name="Teladoc", ticker="TDOC", ev_ebitda=None, market_cap=4_500.0, bucket="healthcare_medtech", role="qualitative peer only", weight=0.0),
            PeerData(name="Doximity", ticker="DOCS", ev_ebitda=None, market_cap=9_000.0, bucket="software_services_platform", role="qualitative peer only", weight=0.0),
            PeerData(name="Veeva", ticker="VEEV", ev_ebitda=None, market_cap=28_000.0, bucket="software_services_platform", role="qualitative peer only", weight=0.0),
        ],
        n_peers=3,
        n_valuation_peers=0,
        n_qualitative_peers=3,
        effective_peer_count=0.0,
        pure_peer_weight_share=0.0,
        adjacent_peer_weight_share=0.0,
        peer_set_type="adjacent_reference_set",
        source="yfinance_peers_low_confidence",
    )
    analysis = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
        peer_multiples_override=qualitative,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("private_valuation_mode")) == "SCREEN_ONLY"
    assert analysis.peer_comps is not None
    assert len(analysis.peer_comps.peers) >= 1
    assert all((p.role or "").lower() == "qualitative peer only" for p in analysis.peer_comps.peers)


def test_private_manual_integrity_failure_marks_ev_unavailable_and_ebitda_not_manual(monkeypatch):
    import goldroger.pipelines.equity as eq

    def _boom(*args, **kwargs):
        raise ValueError("scenario ordering failed (blended low/base/high invariant violated)")

    monkeypatch.setattr(eq, "run_scenarios", _boom)
    md = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred", sector="Healthcare")
    md.additional_metadata = {"siren": "794598813", "country": "France"}
    analysis = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
        company_identifier="794598813",
        manual_revenue=350.0,
        manual_revenue_currency="EUR",
        manual_revenue_year=2024,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(analysis.valuation.recommendation).upper().startswith("INCONCLUSIVE")
    assert analysis.football_field is None
    assert str(ps.get("private_valuation_mode")) == "INDICATIVE_MANUAL"
    assert str(ps.get("private_valuation_output_status")) == "SUPPRESSED_INTEGRITY_FAILURE"
    th = str((analysis.thesis.thesis if analysis.thesis else "") or "").lower()
    assert "valuation suppressed due to scenario integrity failure" in th
    assert "final recommendation is inconclusive" in th
    assert "final recommendation is indicative / low conviction" not in th
    src = _parse_sources_md(analysis.sources_md or "")
    assert "Sensitivity (WACC ±100bps)" not in src
    assert src.get("Indicative Manual EV Range", {}).get("source") == "valuation_suppressed_integrity_failure"
    assert src.get("Indicative Manual EV Base", {}).get("source") == "valuation_suppressed_integrity_failure"
    assert src.get("EBITDA Margin", {}).get("source") != "manual_user_input"


def test_private_manual_dcf_only_has_explicit_final_row_and_reference_peers_status(monkeypatch):
    import goldroger.pipelines.equity as eq
    from goldroger.finance.core.scenarios import ScenariosOutput, ScenarioResult

    def _good_scenarios(*args, **kwargs):
        return ScenariosOutput(
            bear=ScenarioResult(
                name="Bear",
                label="Downside",
                dcf_ev=1800.0,
                comps_ev_low=0.0,
                comps_ev_mid=0.0,
                comps_ev_high=0.0,
                tx_ev=0.0,
                blended_ev=1800.0,
                wacc_used=0.12,
                terminal_growth_used=0.02,
                ebitda_margin_used=0.12,
                revenue_year1=2000.0,
            ),
            base=ScenarioResult(
                name="Base",
                label="Base",
                dcf_ev=4100.0,
                comps_ev_low=0.0,
                comps_ev_mid=0.0,
                comps_ev_high=0.0,
                tx_ev=0.0,
                blended_ev=4100.0,
                wacc_used=0.11,
                terminal_growth_used=0.02,
                ebitda_margin_used=0.12,
                revenue_year1=2200.0,
            ),
            bull=ScenarioResult(
                name="Bull",
                label="Upside",
                dcf_ev=8000.0,
                comps_ev_low=0.0,
                comps_ev_mid=0.0,
                comps_ev_high=0.0,
                tx_ev=0.0,
                blended_ev=8000.0,
                wacc_used=0.10,
                terminal_growth_used=0.02,
                ebitda_margin_used=0.12,
                revenue_year1=2400.0,
            ),
        )

    monkeypatch.setattr(eq, "run_scenarios", _good_scenarios)
    md = _md(company="Revolut Ltd", source="companies_house", revenue_m=None, confidence="inferred", sector="Technology")
    md.additional_metadata = {"company_number": "08804411"}
    analysis = _run_private_case(
        monkeypatch,
        company="Revolut Ltd",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="GB",
        company_identifier="08804411",
        manual_revenue=2200.0,
        manual_revenue_currency="GBP",
        manual_revenue_year=2024,
        manual_revenue_source_note="manual validation",
        peer_multiples_override=_qual_only_peer_stub(),
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("peers")) in {"REFERENCE_PEERS_ONLY", "NO_VALUATION_PEERS_REFERENCE_ONLY"}
    methods = analysis.valuation.methods or []
    dcf = next((m for m in methods if m.name == "DCF"), None)
    final = next((m for m in methods if m.name == "Final Indicative Manual EV"), None)
    assert dcf is not None
    if final is None:
        # If scenario integrity suppresses valuation, final row is not rendered.
        assert str(analysis.valuation.recommendation).upper().startswith("INCONCLUSIVE")
        assert analysis.football_field is None
    else:
        assert int(dcf.weight or 0) == 100
        assert str(dcf.mid) == str(final.mid)
        assert str(dcf.low) == str(final.low)
        assert str(dcf.high) == str(final.high)
        th = str((analysis.thesis.thesis if analysis.thesis else "") or "").lower()
        assert "valuation reference (canonical): indicative manual ev range" in th
        assert "base ev" in th
        assert str(ps.get("private_valuation_output_status")) in {"AVAILABLE", ""}


def test_private_manual_sanity_inputs_are_available_for_valid_indicative_ev(monkeypatch):
    md = _md(company="Revolut Ltd", source="companies_house", revenue_m=None, confidence="inferred", sector="Technology")
    md.additional_metadata = {"company_number": "08804411"}
    analysis = _run_private_case(
        monkeypatch,
        company="Revolut Ltd",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="GB",
        company_identifier="08804411",
        manual_revenue=2200.0,
        manual_revenue_currency="GBP",
        manual_revenue_year=2024,
        manual_ebitda_margin=12.0,
        manual_growth=30.0,
        peer_multiples_override=_qual_only_peer_stub(),
        quick_mode=False,
    )
    ps = (analysis.data_quality or {}).get("pipeline_status", {})
    assert str(ps.get("private_valuation_mode")) == "INDICATIVE_MANUAL"
    assert str(ps.get("private_valuation_output_status")) in {"AVAILABLE", ""}
    src = _parse_sources_md(analysis.sources_md or "")
    assert src.get("Revenue TTM", {}).get("source") == "manual_user_input"
    assert src.get("EBITDA Margin", {}).get("source") == "manual_user_input"
    methods = analysis.valuation.methods or []
    dcf = next((m for m in methods if m.name == "DCF"), None)
    final = next((m for m in methods if m.name == "Final Indicative Manual EV"), None)
    assert dcf is not None and final is not None
    base_ev_m = float(final.mid or 0.0)
    revenue_m = 2200.0
    ebitda_m = revenue_m * 0.12
    assert base_ev_m / revenue_m == pytest.approx(1.86, rel=0.2)
    assert base_ev_m / ebitda_m == pytest.approx(15.5, rel=0.2)


def test_private_manual_source_tagging_only_marks_explicit_manual_fields(monkeypatch):
    md = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred", sector="Healthcare")
    md.additional_metadata = {"siren": "794598813", "country": "France"}
    analysis = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
        company_identifier="794598813",
        manual_revenue=350.0,
        manual_revenue_currency="EUR",
        manual_revenue_year=2024,
    )
    src = _parse_sources_md(analysis.sources_md or "")
    assert src.get("Revenue TTM", {}).get("source") == "manual_user_input"
    assert src.get("Modeled Revenue Growth", {}).get("source") != "manual_user_input"
    assert src.get("EBITDA Margin", {}).get("source") != "manual_user_input"


def test_private_manual_source_tagging_with_growth_and_ebitda_overrides(monkeypatch):
    md = _md(company="Revolut Ltd", source="companies_house", revenue_m=None, confidence="inferred", sector="Financials")
    md.additional_metadata = {"company_number": "08804411"}
    analysis = _run_private_case(
        monkeypatch,
        company="Revolut Ltd",
        registry_md=md,
        selected_providers=[],
        triangulation_result=None,
        country_hint="GB",
        company_identifier="08804411",
        manual_revenue=2200.0,
        manual_revenue_currency="GBP",
        manual_revenue_year=2024,
        manual_ebitda_margin=12.0,
        manual_growth=30.0,
    )
    src = _parse_sources_md(analysis.sources_md or "")
    assert src.get("Revenue TTM", {}).get("source") == "manual_user_input"
    assert src.get("Modeled Revenue Growth", {}).get("source") == "manual_user_input"
    assert src.get("EBITDA Margin", {}).get("source") == "manual_user_input"


def test_private_identity_status_semantics_strong_weak_unresolved(monkeypatch):
    strong = _md(company="Revolut Ltd", source="companies_house", revenue_m=None, confidence="inferred", sector="Financials")
    strong.additional_metadata = {"company_number": "08804411"}
    strong_case = _run_private_case(
        monkeypatch,
        company="Revolut Ltd",
        registry_md=strong,
        selected_providers=[],
        triangulation_result=None,
        country_hint="GB",
        company_identifier="08804411",
    )
    strong_ps = (strong_case.data_quality or {}).get("pipeline_status", {})
    assert str(strong_ps.get("private_identity_status")) == "RESOLVED_STRONG"
    assert str(strong_ps.get("private_identity_source_state")) == "source-backed"
    assert str(strong_ps.get("private_state")) == "IDENTITY_RESOLVED_STRONG_NO_REVENUE"

    weak = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred", sector="Healthcare")
    weak.additional_metadata = {"country": "France"}
    weak_case = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=weak,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
    )
    weak_ps = (weak_case.data_quality or {}).get("pipeline_status", {})
    assert str(weak_ps.get("private_identity_status")) == "RESOLVED_WEAK"
    assert str(weak_ps.get("private_identity_source_state")) == "weak source-backed"
    assert str(weak_ps.get("private_state")) == "IDENTITY_RESOLVED_WEAK_NO_REVENUE"

    unresolved = _md(company="Personio", source="crunchbase", revenue_m=None, confidence="inferred", sector="Technology")
    unresolved.additional_metadata = {}
    unresolved_case = _run_private_case(
        monkeypatch,
        company="Personio",
        registry_md=unresolved,
        selected_providers=[],
        triangulation_result=None,
        country_hint="DE",
    )
    unresolved_ps = (unresolved_case.data_quality or {}).get("pipeline_status", {})
    assert str(unresolved_ps.get("private_identity_status")) == "UNRESOLVED"
    assert str(unresolved_ps.get("private_state")) == "IDENTITY_UNRESOLVED"


def test_private_fr_siren_is_treated_as_strong_identity(monkeypatch):
    fr = _md(company="Doctolib", source="infogreffe", revenue_m=None, confidence="inferred", sector="Healthcare")
    fr.additional_metadata = {"siren": "794598813", "country": "France"}
    fr_case = _run_private_case(
        monkeypatch,
        company="Doctolib",
        registry_md=fr,
        selected_providers=[],
        triangulation_result=None,
        country_hint="FR",
        company_identifier="794598813",
    )
    fr_ps = (fr_case.data_quality or {}).get("pipeline_status", {})
    assert str(fr_ps.get("private_identity_status")) == "RESOLVED_STRONG"
    assert str(fr_ps.get("private_identity_source_state")) == "source-backed"


@pytest.mark.parametrize(
    ("company", "country", "scenario"),
    [
        ("Doctolib", "FR", "triangulated_estimated"),
        ("Sézane", "FR", "no_revenue"),
        ("Alan", "FR", "estimated_provider"),
        ("Contentsquare", "FR", "verified"),
        ("Revolut", "GB", "verified"),
        ("Monzo", "GB", "no_revenue"),
        ("Gymshark", "GB", "verified"),
        ("Personio", "DE", "estimated_provider"),
        ("Picnic", "NL", "triangulated_estimated"),
        ("Glovo", "ES", "no_revenue"),
    ],
)
def test_private_validation_harness_regression(monkeypatch, company, country, scenario):
    if scenario == "verified":
        md = _md(company=company, source="companies_house", revenue_m=550.0, confidence="verified", sector="Consumer")
        tri = None
    elif scenario == "estimated_provider":
        md = _md(company=company, source="crunchbase", revenue_m=340.0, confidence="estimated", sector="Technology")
        tri = None
    elif scenario == "triangulated_estimated":
        md = _md(company=company, source="infogreffe", revenue_m=None, confidence="inferred", sector="Technology")
        tri = TriangulationResult(
            revenue_estimate_m=260.0,
            confidence="estimated",
            signals=[
                TriangulationSignal(estimate_m=250.0, confidence=0.50, source="press_nlp"),
                TriangulationSignal(estimate_m=270.0, confidence=0.55, source="wikipedia"),
            ],
            notes=["validation harness triangulation"],
        )
    else:
        md = _md(company=company, source="infogreffe", revenue_m=None, confidence="inferred", sector="Technology")
        tri = None

    analysis = _run_private_case(
        monkeypatch,
        company=company,
        registry_md=md,
        selected_providers=[],
        triangulation_result=tri,
        country_hint=country,
    )
    summary = _summarize_private_validation(company, country, analysis)

    assert summary["input_query"] == company
    assert summary["resolved_name"]
    assert summary["provenance_completeness"] is True
    assert not summary["private_recommendation"].startswith(("BUY", "SELL", "HOLD"))
    if summary["revenue_status"] in {"estimated", "inferred", "unavailable"}:
        assert (
            "LOW CONVICTION" in summary["private_recommendation"]
            or summary["private_recommendation"].startswith("INCONCLUSIVE")
        )
    if summary["triangulation_used"]:
        assert "triangulation" in summary["revenue_source"].lower() or "triangulation" in (analysis.sources_md or "").lower()
