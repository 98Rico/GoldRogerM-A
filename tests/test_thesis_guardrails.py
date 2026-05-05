from goldroger.pipelines.equity import _sanitize_catalysts


def test_sanitize_stale_catalyst_rewrites_to_recent():
    out = _sanitize_catalysts(
        ["FY2025 Q1 earnings release (January 2025) — upcoming demand signal"],
        run_year=2026,
    )
    assert out
    assert out[0].startswith("Recent event context:")
    assert "upcoming" not in out[0].lower()
