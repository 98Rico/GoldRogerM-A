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
    assert str(audit["selected_listing"]) == "BTI"
    md2, audit2, fx_applied = _apply_currency_normalization(md, audit)
    assert fx_applied is True
    assert str(audit2["status"]).upper() == "OK_FX_NORMALIZED"
    assert str(audit2.get("fx_source")) in {"frankfurter", "static_fx_table"}
    assert str(audit2.get("fx_confidence")) in {"high", "medium", "low"}
    _fx = None
    if md2 and isinstance(md2.additional_metadata, dict):
        _fx = md2.additional_metadata.get("fx_rate_used_fin_to_quote")
    assert _fx is not None and float(_fx) > 0
    assert md2.revenue_ttm is not None and abs(md2.revenue_ttm - (25600.0 * float(_fx))) < 1e-6
    assert md2.fcf_ttm is not None and abs(md2.fcf_ttm - (3000.0 * float(_fx))) < 1e-6


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


def test_nhykf_foreign_ordinary_otc_is_not_forced_as_adr():
    md = MarketData(
        ticker="NHYKF",
        company_name="Norsk Hydro ASA",
        sector="Materials",
        current_price=6.5,
        market_cap=23000.0,
        shares_outstanding=3600.0,
        revenue_ttm=201266.0,  # NOK millions
        fcf_ttm=22200.0,  # NOK millions
        ebitda_ttm=26000.0,  # NOK millions
        net_debt=13000.0,  # NOK millions
        additional_metadata={
            "quote_currency": "USD",
            "financial_currency": "NOK",
            "market_cap_currency": "USD",
            "country": "Norway",
            "quote_type": "EQUITY",
            "underlying_symbol": "NHY.OL",
            "adr_ratio": None,
            "is_adr_hint": False,
        },
    )
    audit = _build_data_normalization_audit(md)
    assert str(audit["listing_type"]) == "foreign_ordinary_otc_likely"
    assert bool(audit["depository_receipt_detected"]) is False
    md2, audit2, fx_applied = _apply_currency_normalization(md, audit)
    assert fx_applied is True
    assert str(audit2["status"]).upper() == "OK_FX_NORMALIZED"
    assert md2.revenue_ttm is not None and md2.revenue_ttm > 0


def test_nhydy_unresolved_depositary_ratio_blocks_normalization():
    md = MarketData(
        ticker="NHYDY",
        company_name="Norsk Hydro ASA",
        sector="Materials",
        current_price=6.5,
        market_cap=23000.0,
        shares_outstanding=100.0,  # force inconsistency vs market cap
        revenue_ttm=201266.0,  # NOK millions
        fcf_ttm=22200.0,  # NOK millions
        ebitda_ttm=26000.0,  # NOK millions
        net_debt=13000.0,  # NOK millions
        additional_metadata={
            "quote_currency": "USD",
            "financial_currency": "NOK",
            "market_cap_currency": "USD",
            "country": "Norway",
            "quote_type": "EQUITY",
            "underlying_symbol": "NHY.OL",
            "adr_ratio": None,
            "is_adr_hint": True,
        },
    )
    audit = _build_data_normalization_audit(md)
    assert str(audit["listing_type"]) == "depositary_receipt_likely_unconfirmed"
    assert str(audit["share_count_basis"]) == "unknown_depositary_ratio"
    assert str(audit["status"]).upper() == "FAILED"
    assert "share-basis normalization unresolved" in str(audit["reason"]).lower()
    _, audit2, fx_applied = _apply_currency_normalization(md, audit)
    assert fx_applied is False
    assert str(audit2["status"]).upper() == "FAILED"


def test_nhy_local_listing_same_currency_is_ok_without_fx():
    md = MarketData(
        ticker="NHY.OL",
        company_name="Norsk Hydro ASA",
        sector="Materials",
        current_price=70.0,
        market_cap=232260.0,  # NOK millions
        shares_outstanding=3320.0,
        revenue_ttm=201266.0,  # NOK millions
        ebitda_ttm=26000.0,  # NOK millions
        additional_metadata={
            "quote_currency": "NOK",
            "financial_currency": "NOK",
            "market_cap_currency": "NOK",
            "country": "Norway",
            "quote_type": "EQUITY",
        },
    )
    audit = _build_data_normalization_audit(md)
    assert str(audit["status"]).upper() == "OK"
    assert str(audit["listing_type"]) == "ordinary_listing"
    assert str(audit["share_count_basis"]) == "ordinary"
    _, audit2, fx_applied = _apply_currency_normalization(md, audit)
    assert fx_applied is False
    assert str(audit2["status"]).upper() == "OK"
