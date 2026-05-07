from goldroger.pipelines.equity import (
    _sanitize_catalysts,
    _trend_is_placeholder,
    _soften_unsourced_scenario_specificity,
)


def test_sanitize_stale_catalyst_rewrites_to_recent():
    out = _sanitize_catalysts(
        ["FY2025 Q1 earnings release (January 2025) — upcoming demand signal"],
        run_year=2026,
    )
    assert out
    assert out[0].startswith("Historical context:")
    assert "upcoming" not in out[0].lower()


def test_placeholder_trend_detection():
    assert _trend_is_placeholder("No market trend data available")
    assert _trend_is_placeholder("Not available from current queries")
    assert not _trend_is_placeholder("Demand trend: mature smartphone category remains replacement-cycle driven.")


def test_soften_unsourced_scenario_specificity():
    raw = "Services expansion (10-12% CAGR) while gross margins remain resilient (~38-40%)."
    out = _soften_unsourced_scenario_specificity(raw)
    assert "10-12% CAGR" not in out
    assert "38-40%" not in out
