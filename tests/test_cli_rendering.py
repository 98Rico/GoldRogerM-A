from goldroger.cli import (
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
    assert "Research: PARTIAL" in block
    assert "Peers: DEGRADED" in block
    assert "Valuation: LOW_CONFIDENCE" in block
    assert "Recommendation: HOLD / LOW CONVICTION" in block
    assert "Research source: fallback | Research depth: limited | Market data source-backed: no" in block
    assert reason == "DCF/comps disagreement"


def test_status_normalizers():
    assert _normalize_research_status("skipped_quick_mode") == "SKIPPED_QUICK_MODE"
    assert _normalize_research_status("degraded") == "PARTIAL"
    assert _normalize_valuation_status("ok", "Low") == "LOW_CONFIDENCE"
    assert _normalize_valuation_status("failed", "Medium") == "FAILED"
