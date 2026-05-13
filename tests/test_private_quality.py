"""Tests for deterministic private-company data quality merge."""

from goldroger.data.fetcher import MarketData
from goldroger.data.private_quality import merge_private_market_data


def _md(
    source: str,
    revenue: float | None,
    confidence: str = "estimated",
    sector: str = "",
    ebitda_margin: float | None = None,
) -> MarketData:
    return MarketData(
        ticker="TEST",
        company_name="Test Co",
        sector=sector,
        revenue_ttm=revenue,
        confidence=confidence,
        data_source=source,
        ebitda_margin=ebitda_margin,
    )


def test_merge_prefers_verified_cluster_and_drops_outlier():
    base = _md("crunchbase", 1200.0, confidence="estimated", sector="Healthcare")
    extra = [
        _md("pappers", 220.0, confidence="verified", sector="Healthcare"),
        _md("companies_house", 210.0, confidence="verified"),
    ]

    merged = merge_private_market_data(base, extra)

    assert merged.market_data is not None
    assert merged.market_data.confidence == "verified"
    assert 210.0 <= float(merged.market_data.revenue_ttm or 0) <= 220.0
    assert any(c.source == "crunchbase" for c in merged.dropped_outliers)


def test_merge_keeps_best_structural_data_when_no_revenue():
    base = _md("infogreffe", None, confidence="inferred", sector="")
    extra = [_md("handelsregister", None, confidence="inferred", sector="Technology")]

    merged = merge_private_market_data(base, extra)

    assert merged.market_data is not None
    assert merged.market_data.revenue_ttm is None
    assert merged.market_data.sector == "Technology"
    assert merged.candidates == []


def test_manual_source_wins_when_present():
    base = _md("pappers", 180.0, confidence="verified", sector="Consumer")
    extra = [
        _md("manual (user input)", 300.0, confidence="verified", sector="Consumer"),
        _md("companies_house", 190.0, confidence="verified", sector="Consumer"),
    ]

    merged = merge_private_market_data(base, extra)

    assert merged.market_data is not None
    assert merged.market_data.data_source == "manual_user_input"
    assert merged.market_data.confidence == "manual"
    assert float(merged.market_data.revenue_ttm or 0) == 300.0


def test_merge_fills_margin_from_best_record():
    base = _md("crunchbase", 250.0, confidence="estimated", sector="Software")
    extra = [
        _md("companies_house", 260.0, confidence="verified", sector="Software", ebitda_margin=0.18),
    ]

    merged = merge_private_market_data(base, extra)

    assert merged.market_data is not None
    assert merged.market_data.ebitda_margin == 0.18
