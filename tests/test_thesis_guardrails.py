from goldroger.pipelines.equity import (
    _build_fallback_thesis,
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


def test_sanitize_catalyst_rewrites_stale_product_labels():
    out = _sanitize_catalysts(
        ["iPhone 18 demand update expected in Q4 2025"],
        run_year=2026,
    )
    assert out
    joined = " ".join(out).lower()
    assert "current iphone cycle" not in joined
    assert "latest iphone cycle" in joined


def test_placeholder_trend_detection():
    assert _trend_is_placeholder("No market trend data available")
    assert _trend_is_placeholder("Not available from current queries")
    assert not _trend_is_placeholder("Demand trend: mature smartphone category remains replacement-cycle driven.")


def test_soften_unsourced_scenario_specificity():
    raw = "Services expansion (10-12% CAGR) while gross margins remain resilient (~38-40%)."
    out = _soften_unsourced_scenario_specificity(raw)
    assert "10-12% CAGR" not in out
    assert "38-40%" not in out


def test_fallback_thesis_is_conservative_and_non_numeric():
    th = _build_fallback_thesis(
        company="AAPL",
        sector="Technology",
        recommendation="HOLD / LOW CONVICTION",
        reason="research fallback mode",
        model_signal="SELL / NEGATIVE VALUATION SIGNAL",
    )
    assert "%" not in (th.thesis or "")
    assert "CAGR" not in (th.thesis or "")
    assert "model signal is SELL / NEGATIVE VALUATION SIGNAL" in (th.thesis or "")
    assert "final recommendation is HOLD / LOW CONVICTION" in (th.thesis or "")
