"""Tests for peer comps engine — sector validation, sanity gates, fallback."""
from unittest.mock import patch
from goldroger.data.comparables import (
    build_peer_multiples,
    resolve_peer_tickers,
    parse_peer_agent_output,
    _sectors_compatible,
    _sector_group,
    _classify_peer_bucket,
    MIN_VALID_PEERS,
)
from goldroger.data.fetcher import MarketData


def _md(
    ticker: str,
    sector: str,
    ev_ebitda: float | None = 12.0,
    ev_revenue: float | None = 2.0,
    market_cap: float | None = 200_000.0,
    industry: str = "Software - Infrastructure",
) -> MarketData:
    return MarketData(
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector=sector,
        ev_ebitda_market=ev_ebitda,
        ev_revenue_market=ev_revenue,
        ebitda_margin=0.20,
        market_cap=market_cap,
        additional_metadata={"industry": industry},
        confidence="verified",
        data_source="yfinance",
    )


# ── sector_group / sectors_compatible ────────────────────────────────────────

def test_sector_group_technology():
    assert _sector_group("Technology") == "tech"
    assert _sector_group("SaaS") == "tech"
    assert _sector_group("enterprise software") == "tech"


def test_sector_group_healthcare():
    assert _sector_group("Healthcare") == "healthcare"
    assert _sector_group("pharma") == "healthcare"
    assert _sector_group("biotech") == "healthcare"


def test_sector_group_unrecognized_returns_none():
    assert _sector_group("Miscellaneous Widgets") is None


def test_sectors_compatible_same_group():
    assert _sectors_compatible("SaaS", "Technology") is True
    assert _sectors_compatible("pharma", "Healthcare") is True


def test_sectors_compatible_different_group():
    assert _sectors_compatible("Technology", "Healthcare") is False
    assert _sectors_compatible("banking", "Consumer Cyclical") is False


def test_sectors_compatible_unclassified_gives_benefit_of_doubt():
    assert _sectors_compatible("Unknown Niche", "Technology") is True
    assert _sectors_compatible("Technology", "Unknown Niche") is True
    assert _sectors_compatible("", "Technology") is True


# ── build_peer_multiples validation ──────────────────────────────────────────

def test_sector_mismatch_drops_peer():
    peers = [
        _md("TECH1", "Technology"),
        _md("TECH2", "Technology"),
        _md("HLTH1", "Healthcare"),  # wrong sector
        _md("TECH3", "Technology"),
    ]
    tickers = [p.ticker for p in peers]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(tickers, target_sector="SaaS")
    assert result.n_peers == 3
    assert result.n_dropped_sector == 1
    assert all(p.sector == "Technology" for p in result.peers)


def test_nonexistent_ticker_dropped():
    data_map = {
        "REAL1": _md("REAL1", "Technology"),
        "REAL2": _md("REAL2", "Technology"),
        "REAL3": _md("REAL3", "Technology"),
        "FAKE1": None,  # doesn't exist
    }
    with patch(
        "goldroger.data.comparables.fetch_market_data",
        side_effect=lambda t: data_map.get(t),
    ):
        result = build_peer_multiples(list(data_map.keys()), target_sector="SaaS")
    assert result.n_peers == 3
    assert result.n_dropped_no_data == 1


def test_extreme_multiples_dropped():
    good = _md("GOOD1", "Technology", ev_ebitda=15.0)
    good2 = _md("GOOD2", "Technology", ev_ebitda=18.0)
    good3 = _md("GOOD3", "Technology", ev_ebitda=12.0)
    extreme = _md("EXTR1", "Technology", ev_ebitda=999.0, ev_revenue=999.0)
    extreme.pe_ratio = 999.0

    with patch(
        "goldroger.data.comparables.fetch_market_data",
        side_effect=[good, good2, good3, extreme],
    ):
        result = build_peer_multiples(["GOOD1", "GOOD2", "GOOD3", "EXTR1"])
    assert result.n_peers == 3
    assert result.n_dropped_sanity == 1


def test_fewer_than_min_peers_returns_sector_fallback():
    only_two = [_md("A", "Technology"), _md("B", "Technology")]
    with patch(
        "goldroger.data.comparables.fetch_market_data",
        side_effect=only_two + [None] * 10,
    ):
        result = build_peer_multiples(["A", "B", "MISS1", "MISS2"], target_sector="SaaS")
    assert result.n_peers == 2
    assert result.source == "yfinance_peers_low_confidence"


def test_no_sector_hint_skips_sector_validation():
    """Without target_sector, all real tickers should pass regardless of sector."""
    mixed = [
        _md("T1", "Technology"),
        _md("T2", "Healthcare"),
        _md("T3", "Consumer Cyclical"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=mixed):
        result = build_peer_multiples(["T1", "T2", "T3"])
    assert result.n_peers == 3
    assert result.n_dropped_sector == 0


def test_median_computed_correctly():
    peers = [_md(f"P{i}", "Technology", ev_ebitda=float(i * 5)) for i in range(1, 5)]
    # ev_ebitda values: 5, 10, 15, 20 → median = 12.5
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(["P1", "P2", "P3", "P4"])
    assert round(float(result.ev_ebitda_median or 0.0), 2) == 12.5
    assert result.n_peers == 4


def test_missing_ev_ebitda_peer_is_qualitative_only():
    peers = [
        _md("CORE1", "Technology", ev_ebitda=20.0, industry="Consumer Electronics"),
        _md("CORE2", "Technology", ev_ebitda=22.0, industry="Software - Infrastructure"),
        _md("QUAL1", "Technology", ev_ebitda=None, ev_revenue=9.0, industry="Software - Infrastructure"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["CORE1", "CORE2", "QUAL1"],
            target_sector="Technology",
            target_industry="Consumer Electronics",
        )
    assert result.n_peers == 3
    assert result.n_valuation_peers == 2
    assert result.n_qualitative_peers == 1
    q = [p for p in result.peers if p.ticker == "QUAL1"][0]
    assert q.role == "qualitative peer only"
    assert (q.weight or 0.0) == 0.0


def test_controlled_relaxation_restores_minimum_valuation_peers():
    # One strong core peer + two adjacent candidates below strict similarity floor.
    peers = [
        _md("CORE", "Technology", ev_ebitda=24.0, market_cap=2_500_000.0, industry="Consumer Electronics"),
        _md("ADJ1", "Technology", ev_ebitda=18.0, market_cap=350_000.0, industry="Semiconductors"),
        _md("ADJ2", "Technology", ev_ebitda=19.0, market_cap=380_000.0, industry="Semiconductors"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["CORE", "ADJ1", "ADJ2"],
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_market_cap=3_000_000.0,
            min_similarity=0.85,  # deliberately strict
            min_market_cap_ratio=0.05,
            min_valuation_peers=2,
        )
    assert result.n_valuation_peers >= 2


def test_cisco_like_name_classifies_as_networking_not_semiconductors():
    bucket = _classify_peer_bucket(
        sector="Technology",
        industry="Communication Equipment",
        name="Cisco Systems",
    )
    assert bucket == "networking_infrastructure"


def test_mega_cap_floor_forces_tiny_peer_to_qualitative_only():
    peers = [
        _md("MEGA1", "Technology", ev_ebitda=24.0, market_cap=3_000_000.0, industry="Software - Infrastructure"),
        _md("SMALL1", "Technology", ev_ebitda=18.0, market_cap=5_000.0, industry="Consumer Electronics"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["MEGA1", "SMALL1"],
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_market_cap=4_000_000.0,
            min_market_cap_ratio=0.05,
            min_valuation_peers=1,
        )
    small = [p for p in result.peers if p.ticker == "SMALL1"]
    if small:
        assert small[0].role == "qualitative peer only"
        assert (small[0].weight or 0.0) == 0.0
    assert all(p.ticker != "SMALL1" or (p.weight or 0.0) == 0.0 for p in result.peers)


def test_networking_bucket_is_capped_for_premium_device_profile():
    peers = [
        _md("SOFT1", "Technology", ev_ebitda=25.0, market_cap=2_200_000.0, industry="Software - Infrastructure"),
        _md("SOFT2", "Technology", ev_ebitda=21.0, market_cap=1_600_000.0, industry="Software - Infrastructure"),
        _md("NET1", "Technology", ev_ebitda=16.0, market_cap=900_000.0, industry="Communication Equipment"),
        _md("SEMI1", "Technology", ev_ebitda=20.0, market_cap=1_000_000.0, industry="Semiconductors"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["SOFT1", "SOFT2", "NET1", "SEMI1"],
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_market_cap=4_000_000.0,
            min_market_cap_ratio=0.05,
            min_valuation_peers=3,
        )
    net_weight = sum(
        float(p.weight or 0.0)
        for p in result.peers
        if p.bucket == "networking_infrastructure" and p.ev_ebitda is not None
    )
    assert net_weight <= 0.151


# ── resolve_peer_tickers ──────────────────────────────────────────────────────

def test_resolve_with_ticker_provided():
    raw = [{"name": "Apple", "ticker": "AAPL", "exchange": "NASDAQ"}]
    result = resolve_peer_tickers(raw)
    assert result == ["AAPL"]


def test_resolve_deduplicates():
    raw = [
        {"name": "Apple", "ticker": "AAPL"},
        {"name": "Apple Inc", "ticker": "AAPL"},
    ]
    result = resolve_peer_tickers(raw)
    assert result == ["AAPL"]


def test_resolve_skips_empty_ticker_and_name():
    raw = [{"name": "", "ticker": ""}]
    with patch("goldroger.data.comparables.resolve_ticker", return_value=None):
        result = resolve_peer_tickers(raw)
    assert result == []


# ── parse_peer_agent_output ───────────────────────────────────────────────────

def test_parse_valid_output():
    raw = '{"peers": [{"name": "Apple", "ticker": "AAPL"}]}'
    result = parse_peer_agent_output(raw)
    assert result[0]["ticker"] == "AAPL"


def test_parse_invalid_json_returns_empty():
    result = parse_peer_agent_output("not json")
    assert result == []
