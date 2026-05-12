from goldroger.data.sourcing import ProviderDescriptor, make_source_result


def test_make_source_result_defaults_and_flags():
    out = make_source_result(
        123.4,
        source_name="yfinance",
        source_confidence="verified",
        currency="USD",
        unit="millions",
        warning_flags=["stale"],
    )
    assert out.value == 123.4
    assert out.source_name == "yfinance"
    assert out.source_confidence == "verified"
    assert out.currency == "USD"
    assert out.unit == "millions"
    assert out.warning_flags == ["stale"]
    assert out.as_of_date


def test_provider_descriptor_to_dict():
    d = ProviderDescriptor(
        name="yfinance",
        source_type="api",
        coverage=["GLOBAL"],
        freshness="intraday",
        confidence_level="verified",
        limitations=["Unofficial API"],
        raw_fields=["marketCap"],
        normalized_fields=["market_cap"],
    ).to_dict()
    assert d["name"] == "yfinance"
    assert d["source_type"] == "api"
    assert "GLOBAL" in d["coverage"]

