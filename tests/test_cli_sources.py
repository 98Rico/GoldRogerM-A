from goldroger.cli import _infer_source_note, _parse_sources_md


def test_parse_sources_md_extracts_source_confidence_and_url():
    md = """# Sources

| Metric | Value | Source | Confidence |
|--------|-------|--------|------------|
| Revenue TTM | $100M | yfinance ([link](https://example.com/rev)) | ✅ verified |
"""
    parsed = _parse_sources_md(md)
    assert "Revenue TTM" in parsed
    assert parsed["Revenue TTM"]["source"] == "yfinance"
    assert parsed["Revenue TTM"]["confidence"] == "verified"
    assert parsed["Revenue TTM"]["url"] == "https://example.com/rev"


def test_infer_source_note_uses_alias_mapping():
    src_map = {
        "Revenue TTM": {
            "value": "$100M",
            "source": "yfinance",
            "confidence": "verified",
            "url": "",
        }
    }
    note = _infer_source_note("Revenue", "$100M", src_map)
    assert "Revenue: yfinance (verified)" == note


def test_infer_source_note_falls_back_to_estimate_tag():
    note = _infer_source_note("Gross Margin", "45.0% [estimated]", {})
    assert "model estimate (estimated)" in note


def test_infer_source_note_for_indicative_midpoint_mentions_low_confidence_input():
    src_map = {
        "Implied Target Price": {
            "value": "$217",
            "source": "valuation_bridge",
            "confidence": "verified",
            "url": "",
        }
    }
    note = _infer_source_note("Indicative midpoint", "~$217", src_map)
    assert "verified calculation, low-confidence input" in note
