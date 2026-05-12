"""Tests for peer comps engine — sector validation, sanity gates, fallback."""
from unittest.mock import patch
from goldroger.data.comparables import (
    build_peer_multiples,
    find_peers_deterministic_quick,
    resolve_peer_tickers,
    parse_peer_agent_output,
    _sectors_compatible,
    _sector_group,
    _classify_peer_bucket,
    MIN_VALID_PEERS,
)
from goldroger.data.fetcher import MarketData
from goldroger.utils.cache import peer_universe_cache


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


def test_semiconductor_equipment_keywords_classify_correctly():
    assert _classify_peer_bucket(
        sector="Technology",
        industry="Semiconductor Equipment & Materials",
        name="Applied Materials",
    ) == "semiconductor_equipment"
    assert _classify_peer_bucket(
        sector="Technology",
        industry="Semiconductor Equipment",
        name="Lam Research",
    ) == "semiconductor_equipment"


def test_materials_profile_uses_aluminum_bucket_for_alcoa_like_names():
    bucket = _classify_peer_bucket(
        sector="Basic Materials",
        industry="Aluminum",
        name="Alcoa Corporation",
    )
    assert bucket == "aluminum_metals"


def test_low_ev_ebitda_peer_is_filtered_by_sanity_floor():
    peers = {
        "PM": _md("PM", "Consumer Defensive", ev_ebitda=11.0, market_cap=120_000.0, industry="Tobacco"),
        "MO": _md("MO", "Consumer Defensive", ev_ebitda=9.0, market_cap=90_000.0, industry="Tobacco"),
        "JAPAY": _md("JAPAY", "Consumer Defensive", ev_ebitda=1.1, market_cap=80_000.0, industry="Tobacco"),
    }
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=lambda t: peers.get(t)):
        result = build_peer_multiples(
            ["PM", "MO", "JAPAY"],
            target_sector="Consumer Staples",
            target_industry="Tobacco",
            target_market_cap=130_000.0,
            min_valuation_peers=2,
        )
    jp = [p for p in result.peers if p.ticker == "JAPAY"]
    if jp:
        assert jp[0].role == "qualitative peer only"
        assert (jp[0].weight or 0.0) == 0.0


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
    assert net_weight <= 0.201


def test_bats_peer_set_excludes_bti_as_same_issuer_alternate_listing():
    peers = {
        "BTI": MarketData(
            ticker="BTI",
            company_name="British American Tobacco p.l.c.",
            sector="Consumer Defensive",
            ev_ebitda_market=9.5,
            market_cap=120_000.0,
            additional_metadata={
                "industry": "Tobacco",
                "country": "United Kingdom",
                "underlying_symbol": "BATS.L",
                "primary_listing_symbol": "BATS.L",
                "selected_listing_symbol": "BTI",
            },
        ),
        "PM": _md("PM", "Consumer Defensive", ev_ebitda=11.0, market_cap=140_000.0, industry="Tobacco"),
        "MO": _md("MO", "Consumer Defensive", ev_ebitda=9.0, market_cap=90_000.0, industry="Tobacco"),
    }
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=lambda t: peers.get(t)):
        result = build_peer_multiples(
            ["BTI", "PM", "MO"],
            target_sector="Consumer Staples",
            target_industry="Tobacco",
            target_market_cap=100_000.0,
            target_ticker="BATS.L",
            target_company_name="British American Tobacco p.l.c.",
            target_country="United Kingdom",
            target_primary_listing="BATS.L",
            target_underlying_symbol="BATS.L",
            min_valuation_peers=2,
        )
    assert all(p.ticker != "BTI" for p in result.peers)
    assert result.n_dropped_same_issuer >= 1
    assert any(p.ticker == "PM" for p in result.peers)
    assert any(p.ticker == "MO" for p in result.peers)


def test_nhy_local_peer_set_excludes_otc_alternate_listings():
    peers = {
        "NHYDY": MarketData(
            ticker="NHYDY",
            company_name="Norsk Hydro ASA",
            sector="Basic Materials",
            ev_ebitda_market=5.0,
            market_cap=23_000.0,
            additional_metadata={
                "industry": "Aluminum",
                "country": "Norway",
                "underlying_symbol": "NHY.OL",
                "primary_listing_symbol": "NHY.OL",
                "selected_listing_symbol": "NHYDY",
            },
        ),
        "NHYKF": MarketData(
            ticker="NHYKF",
            company_name="Norsk Hydro ASA",
            sector="Basic Materials",
            ev_ebitda_market=5.1,
            market_cap=23_000.0,
            additional_metadata={
                "industry": "Aluminum",
                "country": "Norway",
                "underlying_symbol": "NHY.OL",
                "primary_listing_symbol": "NHY.OL",
                "selected_listing_symbol": "NHYKF",
            },
        ),
        "AA": _md("AA", "Basic Materials", ev_ebitda=6.2, market_cap=8_000.0, industry="Aluminum"),
        "RIO": _md("RIO", "Basic Materials", ev_ebitda=7.5, market_cap=120_000.0, industry="Other Industrial Metals & Mining"),
    }
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=lambda t: peers.get(t)):
        result = build_peer_multiples(
            ["NHYDY", "NHYKF", "AA", "RIO"],
            target_sector="Materials",
            target_industry="Aluminum",
            target_market_cap=220_000.0,
            target_ticker="NHY.OL",
            target_company_name="Norsk Hydro ASA",
            target_country="Norway",
            target_primary_listing="NHY.OL",
            target_underlying_symbol="NHY.OL",
            min_valuation_peers=2,
        )
    assert all(p.ticker not in {"NHYDY", "NHYKF"} for p in result.peers)
    assert result.n_dropped_same_issuer >= 2
    assert any(p.ticker == "AA" for p in result.peers)
    assert any(p.ticker == "RIO" for p in result.peers)


def test_apple_quick_deterministic_reserve_peer_floor():
    peer_universe_cache.clear()
    md = _md(
        "AAPL",
        "Technology",
        ev_ebitda=26.0,
        market_cap=4_200_000.0,
        industry="Consumer Electronics",
    )
    with patch("goldroger.data.comparables._search_yahoo_tickers", return_value=[]), \
         patch("goldroger.data.comparables.yf.Industry", side_effect=Exception("no net")), \
         patch("goldroger.data.comparables.yf.Sector", side_effect=Exception("no net")):
        peers = find_peers_deterministic_quick(
            target_md=md,
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_peers=12,
        )
    assert len(peers) >= 6
    assert peers[:6] == ["MSFT", "ORCL", "CSCO", "NVDA", "AVGO", "MU"]


def test_deterministic_peer_cache_is_ticker_scoped():
    peer_universe_cache.clear()
    aapl = _md(
        "AAPL",
        "Technology",
        ev_ebitda=26.0,
        market_cap=4_200_000.0,
        industry="Consumer Electronics",
    )
    msft = _md(
        "MSFT",
        "Technology",
        ev_ebitda=17.0,
        market_cap=3_200_000.0,
        industry="Software - Infrastructure",
    )
    with patch("goldroger.data.comparables._search_yahoo_tickers", return_value=[]), \
         patch("goldroger.data.comparables.yf.Industry", side_effect=Exception("no net")), \
         patch("goldroger.data.comparables.yf.Sector", side_effect=Exception("no net")):
        aapl_peers = find_peers_deterministic_quick(
            target_md=aapl,
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_peers=12,
        )
        msft_peers = find_peers_deterministic_quick(
            target_md=msft,
            target_sector="Technology",
            target_industry="Software - Infrastructure",
            target_peers=12,
        )
    assert aapl_peers[:3] == ["MSFT", "ORCL", "CSCO"]
    assert msft_peers != aapl_peers


def test_apple_like_semiconductor_weight_share_and_single_peer_cap():
    peers = [
        _md("MSFT", "Technology", ev_ebitda=17.0, market_cap=3_000_000.0, industry="Software - Infrastructure"),
        _md("ORCL", "Technology", ev_ebitda=25.0, market_cap=560_000.0, industry="Software - Infrastructure"),
        _md("CSCO", "Technology", ev_ebitda=23.0, market_cap=360_000.0, industry="Communication Equipment"),
        _md("NVDA", "Technology", ev_ebitda=55.0, market_cap=5_000_000.0, industry="Semiconductors"),
        _md("AVGO", "Technology", ev_ebitda=42.0, market_cap=2_000_000.0, industry="Semiconductors"),
        _md("MU", "Technology", ev_ebitda=20.0, market_cap=740_000.0, industry="Semiconductors"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["MSFT", "ORCL", "CSCO", "NVDA", "AVGO", "MU"],
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_market_cap=4_200_000.0,
            min_market_cap_ratio=0.05,
            min_valuation_peers=5,
        )
    semi_weight = sum(
        float(p.weight or 0.0)
        for p in result.peers
        if (p.bucket or "") in {"semiconductors", "semiconductor_equipment"} and p.ev_ebitda is not None
    )
    max_semi_peer = max(
        (
            float(p.weight or 0.0)
            for p in result.peers
            if (p.bucket or "") in {"semiconductors", "semiconductor_equipment"} and p.ev_ebitda is not None
        ),
        default=0.0,
    )
    assert semi_weight <= 0.351
    assert max_semi_peer <= 0.151


def test_premium_device_platform_labels_software_peer_as_adjacent():
    peers = [
        _md("HARD1", "Technology", ev_ebitda=18.0, market_cap=1_500_000.0, industry="Consumer Electronics"),
        _md("SOFT1", "Technology", ev_ebitda=22.0, market_cap=2_500_000.0, industry="Software - Infrastructure"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["HARD1", "SOFT1"],
            target_sector="Technology",
            target_industry="Consumer Electronics",
            target_market_cap=4_000_000.0,
            min_market_cap_ratio=0.05,
            min_valuation_peers=1,
        )
    software = [p for p in result.peers if p.ticker == "SOFT1"][0]
    assert software.role == "adjacent valuation peer"


def test_tobacco_target_rejects_tech_peer_set():
    peers = [
        _md("BTI", "Consumer Defensive", ev_ebitda=10.5, market_cap=85_000.0, industry="Tobacco"),
        _md("AAPL", "Technology", ev_ebitda=24.0, market_cap=3_000_000.0, industry="Consumer Electronics"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["BTI", "AAPL"],
            target_sector="Consumer Defensive Tobacco",
            target_industry="Tobacco",
            min_valuation_peers=1,
        )
    assert all(p.ticker != "AAPL" for p in result.peers)
    assert result.n_dropped_sector >= 1


def test_energy_target_rejects_financial_peer_set():
    peers = [
        _md("XOM", "Energy", ev_ebitda=8.2, market_cap=450_000.0, industry="Oil & Gas"),
        _md("JPM", "Financial Services", ev_ebitda=11.0, market_cap=600_000.0, industry="Banks"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["XOM", "JPM"],
            target_sector="Energy",
            target_industry="Oil & Gas",
            min_valuation_peers=1,
        )
    assert all(p.ticker != "JPM" for p in result.peers)
    assert result.n_dropped_sector >= 1


def test_banking_target_rejects_technology_peer_set():
    peers = [
        _md("JPM", "Financial Services", ev_ebitda=11.0, market_cap=600_000.0, industry="Banks"),
        _md("MSFT", "Technology", ev_ebitda=20.0, market_cap=2_800_000.0, industry="Software - Infrastructure"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["JPM", "MSFT"],
            target_sector="Banking Financial Services",
            target_industry="Banks",
            min_valuation_peers=1,
        )
    assert all(p.ticker != "MSFT" for p in result.peers)
    assert result.n_dropped_sector >= 1


def test_tobacco_bucket_labels_are_sector_specific():
    assert _classify_peer_bucket("Consumer Defensive", "Tobacco", "British American Tobacco") == "tobacco_nicotine"
    assert _classify_peer_bucket("Consumer Defensive", "Beverages - Non-Alcoholic", "Coca-Cola") == "beverages_adjacent"
    assert _classify_peer_bucket("Consumer Defensive", "Household & Personal Products", "Procter & Gamble") == "household_products_adjacent"
    assert _classify_peer_bucket("Consumer Defensive", "Discount Stores", "Costco") == "retail_adjacent"


def test_tobacco_profile_peer_set_type_is_mixed_when_adjacent_weights_dominate():
    peers = [
        _md("PM", "Consumer Defensive", ev_ebitda=13.0, market_cap=140_000.0, industry="Tobacco"),
        _md("MO", "Consumer Defensive", ev_ebitda=11.0, market_cap=80_000.0, industry="Tobacco"),
        _md("KO", "Consumer Defensive", ev_ebitda=17.0, market_cap=270_000.0, industry="Beverages - Non-Alcoholic"),
        _md("PG", "Consumer Defensive", ev_ebitda=18.0, market_cap=380_000.0, industry="Household & Personal Products"),
    ]
    with patch("goldroger.data.comparables.fetch_market_data", side_effect=peers):
        result = build_peer_multiples(
            ["PM", "MO", "KO", "PG"],
            target_sector="Consumer Staples Tobacco",
            target_industry="Tobacco",
            target_market_cap=180_000.0,
            min_valuation_peers=3,
        )
    assert result.n_valuation_peers >= 3
    assert result.peer_set_type in {"MIXED_COMPS_OK", "ADJACENT_REFERENCE_SET", "PURE_COMPS_OK"}
    assert 0.0 <= result.pure_peer_weight_share <= 1.0
    assert 0.0 <= result.adjacent_peer_weight_share <= 1.0


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
