from goldroger.data.fetcher import MarketData
from goldroger.pipelines.equity import _apply_currency_normalization, _build_data_normalization_audit


def _md_bti_like(shares_outstanding: float = 2200.0) -> MarketData:
    return MarketData(
        ticker="BTI",
        company_name="British American Tobacco p.l.c.",
        sector="Consumer Staples",
        current_price=57.0,
        market_cap=125700.0,
        shares_outstanding=shares_outstanding,
        revenue_ttm=25600.0,  # GBP millions
        fcf_ttm=3000.0,  # GBP millions
        ebitda_ttm=8500.0,  # GBP millions
        net_debt=42000.0,  # GBP millions
        additional_metadata={
            "quote_currency": "USD",
            "financial_currency": "GBP",
            "market_cap_currency": "USD",
            "country": "United Kingdom",
            "quote_type": "EQUITY",
            "underlying_symbol": "BTI.L",
            "is_adr_hint": True,
            "adr_ratio": None,
        },
    )


def test_bti_like_currency_mismatch_fx_normalizes_and_unblocks_with_share_heuristic():
    md = _md_bti_like()
    audit = _build_data_normalization_audit(md)
    assert str(audit["status"]).upper() == "FAILED"
    assert "currency mismatch" in str(audit["reason"]).lower()
    assert str(audit["listing_type"]) == "foreign_issuer_us_listing_unresolved"
    assert str(audit["share_count_basis"]) == "foreign_us_listing_unverified_share_basis"
    md2, audit2, fx_applied = _apply_currency_normalization(md, audit)
    assert fx_applied is True
    assert str(audit2["status"]).upper() == "OK_FX_NORMALIZED"
    assert str(audit2.get("fx_source")) == "static_fx_table"
    assert str(audit2.get("fx_confidence")) == "low"
    assert "FX confidence low" in str(audit2.get("reason"))
    # GBP->USD deterministic table = 1.26
    assert md2.revenue_ttm is not None and abs(md2.revenue_ttm - (25600.0 * 1.26)) < 1e-6
    assert md2.fcf_ttm is not None and abs(md2.fcf_ttm - (3000.0 * 1.26)) < 1e-6


def test_unresolved_share_basis_keeps_normalization_failed():
    # Force share inconsistency so heuristic cannot be used.
    md = _md_bti_like(shares_outstanding=100.0)
    md.additional_metadata["quote_type"] = "ADR"
    audit = _build_data_normalization_audit(md)
    assert str(audit["status"]).upper() == "FAILED"
    assert "share-basis normalization unresolved" in str(audit["reason"]).lower()
    _, audit2, fx_applied = _apply_currency_normalization(md, audit)
    assert fx_applied is False
    assert str(audit2["status"]).upper() == "FAILED"
