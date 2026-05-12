from goldroger.cli import (
    _format_metric_value,
    _fmt_timing_s,
    _infer_source_note,
    _normalize_sector_label,
    _normalize_research_status,
    _normalize_valuation_status,
    _peer_table_headers,
    _render_pipeline_status_block,
)


def test_peer_table_headers_default_is_compact():
    headers = _peer_table_headers(debug=False)
    assert headers == ["Ticker", "Role", "Bucket", "MCap", "EV/EBITDA", "Weight"]


def test_peer_table_headers_debug_is_verbose():
    headers = _peer_table_headers(debug=True)
    assert "Similarity" in headers
    assert "Business Sim" in headers
    assert "Scale Sim" in headers


def test_pipeline_status_block_is_normalized_and_compact():
    block, reason = _render_pipeline_status_block(
        {
            "research_enrichment": "DEGRADED_API_CAPACITY",
            "peers": "DEGRADED_API_CAPACITY",
            "valuation": "DEGRADED",
            "confidence": "Low",
            "recommendation": "HOLD / LOW CONVICTION",
            "confidence_reason": "DCF/comps disagreement",
            "research_source": "fallback",
            "research_depth": "limited",
            "market_data_source_backed": "no",
        }
    )
    assert "Research: PARTIAL_FALLBACK" in block
    assert "Peers: PEERS_FAILED" in block
    assert "Valuation: LOW_CONFIDENCE" in block
    assert "Recommendation: HOLD / LOW CONVICTION" in block
    assert "Research source: fallback | Research depth: limited | Market context source-backed: no" in block
    assert "Research used in valuation: no | Research used in thesis: conservative template only" in block
    assert reason == "DCF/comps disagreement"


def test_status_normalizers():
    assert _normalize_research_status("skipped_quick_mode") == "SKIPPED_QUICK_MODE"
    assert _normalize_research_status("degraded") == "PARTIAL_FALLBACK"
    assert _normalize_research_status("partial_fallback") == "PARTIAL_FALLBACK"
    assert _normalize_research_status("research_partial_source_backed") == "PARTIAL_SOURCE_BACKED"
    assert _normalize_valuation_status("ok", "Low") == "LOW_CONFIDENCE"
    assert _normalize_valuation_status("failed", "Medium") == "FAILED"


def test_pipeline_status_preserves_ok_adjacent_peer_state():
    block, _ = _render_pipeline_status_block(
        {
            "research_enrichment": "OK",
            "peers": "OK_ADJACENT",
            "valuation": "DEGRADED",
            "confidence": "Low",
            "recommendation": "HOLD / LOW CONVICTION",
        }
    )
    assert "Peers: OK_ADJACENT" in block


def test_pipeline_status_partial_fallback_line_is_rendered():
    block, _ = _render_pipeline_status_block(
        {
            "research_enrichment": "PARTIAL_FALLBACK",
            "peers": "ADJACENT_COMPS_LOW_DIVERSITY",
            "valuation": "DEGRADED",
            "confidence": "Low",
            "recommendation": "HOLD / LOW CONVICTION",
            "research_source": "fallback",
            "research_depth": "limited",
            "market_data_source_backed": "no",
        }
    )
    assert "Research: PARTIAL_FALLBACK" in block
    assert (
        "Full research unavailable; report generated from verified market data, deterministic peer set, "
        "and conservative fallback thesis template."
    ) in block


def test_pipeline_status_skipped_quick_mode_uses_skipped_research_source():
    block, _ = _render_pipeline_status_block(
        {
            "research_enrichment": "SKIPPED_QUICK_MODE",
            "peers": "ADJACENT_COMPS_LOW_DIVERSITY",
            "valuation": "DEGRADED",
            "confidence": "Low",
            "recommendation": "HOLD / LOW CONVICTION",
            "research_source": "skipped",
            "research_depth": "none",
            "market_data_source_backed": "no",
        }
    )
    assert "Research: SKIPPED_QUICK_MODE" in block
    assert "Research source: skipped | Research depth: none | Market context source-backed: no" in block


def test_fmt_timing_s_hides_none_like_values():
    assert _fmt_timing_s(None) == "N/A"
    assert _fmt_timing_s("None") == "N/A"
    assert _fmt_timing_s("Nones") == "N/A"
    assert _fmt_timing_s("") == "N/A"
    assert _fmt_timing_s("nan") == "N/A"
    assert _fmt_timing_s(6.2) == "6.20s"


def test_normalize_sector_label_tobacco():
    assert _normalize_sector_label("Consumer Staples", "Consumer Goods - Tobacco") == "Consumer Staples / Tobacco"
    assert _normalize_sector_label("Consumer Staples - Tobacco", "Tobacco") == "Consumer Staples / Tobacco"


def test_pipeline_status_adds_range_hint_under_high_dispersion_low_confidence():
    block, _ = _render_pipeline_status_block(
        {
            "research_enrichment": "PARTIAL_FALLBACK",
            "peers": "MIXED_COMPS_OK",
            "valuation": "DEGRADED",
            "confidence": "Low",
            "recommendation": "BUY / LOW CONVICTION",
            "method_dispersion_level": "High",
            "method_dispersion_ratio": 3.31,
        }
    )
    assert "Use range over midpoint due to low confidence and high method dispersion." in block


def test_pipeline_status_renders_normalization_audit_and_suppression():
    block, _ = _render_pipeline_status_block(
        {
            "research_enrichment": "PARTIAL_FALLBACK",
            "peers": "PEERS_FAILED",
            "valuation": "FAILED",
            "confidence": "Low",
            "recommendation": "INCONCLUSIVE",
            "normalization_status": "FAILED",
            "normalization_reason": "currency mismatch without FX normalization",
            "quote_currency": "USD",
            "financial_statement_currency": "NOK",
            "market_cap_currency": "USD",
            "listing_type": "depositary_receipt_likely_unconfirmed",
            "share_count_basis": "unknown_depositary_ratio",
            "adr_detected": False,
            "depository_receipt_detected": True,
            "adr_ratio": None,
            "fx_source": "static_fx_table",
            "fx_confidence": "low",
            "fx_timestamp": "static_table",
            "sanity_breaker_triggered": True,
        }
    )
    assert "Data normalization: FAILED" in block
    assert "Quote/market cap currency: USD/USD" in block
    assert "Financial statement currency: NOK" in block
    assert "Listing type: depositary_receipt_likely_unconfirmed" in block
    assert "Depositary receipt detected: yes" in block
    assert "FX source/confidence: static_fx_table / low (static_table)" in block
    assert "Recommendation suppressed by sanity breaker: data check required." in block


def test_currency_prefixed_revenue_and_fcf_formatting():
    assert _format_metric_value("Revenue", "NOK 201266M") == "NOK 201.3B"
    assert _format_metric_value("Free Cash Flow", "GBP 3000M [check currency/ADR basis]") == "GBP 3.0B [check currency/ADR basis]"


def test_infer_source_note_range_and_midpoint_are_bridge_explicit():
    src_map = {
        "Fair Value Range": {
            "value": "$118.00–$314.00",
            "source": "scenario_blended",
            "confidence": "inferred",
            "url": "",
        },
        "Implied Target Price": {
            "value": "$188.00",
            "source": "valuation_bridge",
            "confidence": "verified",
            "url": "",
        },
    }
    range_note = _infer_source_note("Fair Value Range", "$118.00–$314.00", src_map)
    midpoint_note = _infer_source_note("Indicative midpoint", "~$188", src_map)
    assert "valuation_bridge from blended valuation low/high" in range_note
    assert "valuation_bridge from blended valuation mid" in midpoint_note
