from goldroger.cli import (
    _infer_source_note,
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
