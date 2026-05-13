from goldroger.pipelines.equity import (
    _build_fallback_thesis,
    _enforce_profile_context_guard,
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


def test_soften_specificity_repairs_token_stitch_artifacts():
    raw = "byresilient marginsin aresilient pricing and compress toresilientas needed"
    out = _soften_unsourced_scenario_specificity(raw)
    lowered = out.lower()
    assert "byresilient" not in lowered
    assert "marginsin" not in lowered
    assert "aresilient" not in lowered
    assert "toresilient" not in lowered


def test_soften_specificity_removes_unsourced_dates_and_hard_regulatory_event_claims():
    raw = "DOJ ruling expected Q4 2026 with 3.2Mt capacity impact by September 2026."
    out = _soften_unsourced_scenario_specificity(raw)
    lowered = out.lower()
    assert "q4 2026" not in lowered
    assert "september 2026" not in lowered
    assert "doj ruling expected" not in lowered
    assert "potential regulatory developments" in lowered


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


def test_profile_context_guard_blocks_cross_sector_leakage():
    leaked = "Platform/services monetization and App Store policy are key."
    out = _enforce_profile_context_guard(leaked, "consumer_staples_tobacco")
    assert "sector profile only, not source-backed" in out


def test_aapl_fallback_thesis_uses_device_platform_language():
    th = _build_fallback_thesis(
        company="Apple Inc.",
        ticker="AAPL",
        sector="Technology",
        industry="Consumer Electronics",
        recommendation="HOLD / LOW CONVICTION",
        reason="fallback mode",
        model_signal="Negative valuation signal",
    )
    text = (th.thesis or "").lower()
    assert "sector profile: technology / consumer hardware & services ecosystem" in text
    assert "device upgrade cycles" in text
    assert "services attach" in text
    assert "installed base" in text
    assert "app store" in text
    assert "enterprise software adoption" not in text
    assert "cloud migration" not in text


def test_bat_fallback_thesis_uses_tobacco_cash_return_language():
    th = _build_fallback_thesis(
        company="British American Tobacco p.l.c.",
        ticker="BATS.L",
        sector="Consumer Staples",
        industry="Tobacco",
        recommendation="BUY / LOW CONVICTION",
        reason="fallback mode",
        model_signal="Positive valuation signal",
    )
    text = (th.thesis or "").lower()
    assert "combustible volume" in text
    assert "reduced-risk" in text
    assert "excise" in text
    assert "litigation" in text
    assert "cash return" in text


def test_nhy_fallback_thesis_uses_aluminum_cyclical_language():
    th = _build_fallback_thesis(
        company="Norsk Hydro ASA",
        ticker="NHY.OL",
        sector="Materials",
        industry="Aluminum",
        recommendation="WATCH / LOW CONVICTION",
        reason="fallback mode",
        model_signal="Positive valuation signal",
    )
    text = (th.thesis or "").lower()
    assert "aluminum" in text or "aluminium" in text
    assert "lme" in text
    assert "energy" in text
    assert "recycling" in text
    assert "commodity-cycle volatility" in text
