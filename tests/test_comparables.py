"""Tests for peer comps engine — sector validation, sanity gates, fallback."""
from unittest.mock import patch
from goldroger.data.comparables import (
    build_peer_multiples,
    resolve_peer_tickers,
    parse_peer_agent_output,
    _sectors_compatible,
    _sector_group,
    MIN_VALID_PEERS,
)
from goldroger.data.fetcher import MarketData


def _md(ticker: str, sector: str, ev_ebitda: float = 12.0, ev_revenue: float = 2.0) -> MarketData:
    return MarketData(
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector=sector,
        ev_ebitda_market=ev_ebitda,
        ev_revenue_market=ev_revenue,
        ebitda_margin=0.20,
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
