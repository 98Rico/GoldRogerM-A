"""
Peer comparables engine.

For any company (public or private), identifies 4-6 listed peers via LLM,
fetches their real market data from yfinance, and computes median/mean multiples.

This replaces sector-table averages with real live market data — critical for
accurate private company valuation and up-to-date comps.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from goldroger.data.fetcher import fetch_market_data, resolve_ticker, MarketData


@dataclass
class PeerData:
    name: str
    ticker: str
    ev_ebitda: Optional[float] = None
    ev_revenue: Optional[float] = None
    pe_ratio: Optional[float] = None
    ebitda_margin: Optional[float] = None
    revenue_growth: Optional[float] = None
    market_cap: Optional[float] = None


@dataclass
class PeerMultiples:
    """Aggregated peer multiples — used as comps base in valuation."""
    peers: list[PeerData] = field(default_factory=list)

    # Medians (primary — less distorted by outliers)
    ev_ebitda_median: Optional[float] = None
    ev_revenue_median: Optional[float] = None
    pe_median: Optional[float] = None
    ebitda_margin_median: Optional[float] = None
    revenue_growth_median: Optional[float] = None

    # Ranges for football field
    ev_ebitda_low: Optional[float] = None
    ev_ebitda_high: Optional[float] = None
    ev_revenue_low: Optional[float] = None
    ev_revenue_high: Optional[float] = None

    n_peers: int = 0
    source: str = "yfinance_peers"


def _median(values: list[float]) -> Optional[float]:
    clean = sorted(v for v in values if v and v > 0)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2 == 0:
        return (clean[mid - 1] + clean[mid]) / 2
    return clean[mid]


def _percentile(values: list[float], pct: float) -> Optional[float]:
    clean = sorted(v for v in values if v and v > 0)
    if not clean:
        return None
    idx = int(len(clean) * pct)
    return clean[max(0, min(idx, len(clean) - 1))]


def build_peer_multiples(peer_tickers: list[str]) -> PeerMultiples:
    """
    Fetch market data for each peer ticker and compute aggregated multiples.
    Filters out peers where data is unavailable or multiples look extreme.
    """
    peers: list[PeerData] = []

    for ticker in peer_tickers:
        md = fetch_market_data(ticker)
        if md is None:
            continue

        ev_ebitda = md.ev_ebitda_market
        ev_revenue = md.ev_revenue_market
        pe = md.pe_ratio or md.forward_pe

        # Basic sanity gates — exclude extreme outliers
        if ev_ebitda is not None and (ev_ebitda < 1 or ev_ebitda > 150):
            ev_ebitda = None
        if ev_revenue is not None and (ev_revenue < 0.1 or ev_revenue > 50):
            ev_revenue = None
        if pe is not None and (pe < 3 or pe > 200):
            pe = None

        peers.append(PeerData(
            name=md.company_name,
            ticker=ticker,
            ev_ebitda=ev_ebitda,
            ev_revenue=ev_revenue,
            pe_ratio=pe,
            ebitda_margin=md.ebitda_margin,
            revenue_growth=md.forward_revenue_growth or md.revenue_growth_yoy,
            market_cap=md.market_cap,
        ))

    if not peers:
        return PeerMultiples()

    ev_ebitdas = [p.ev_ebitda for p in peers if p.ev_ebitda]
    ev_revenues = [p.ev_revenue for p in peers if p.ev_revenue]
    pes = [p.pe_ratio for p in peers if p.pe_ratio]
    margins = [p.ebitda_margin for p in peers if p.ebitda_margin]
    growths = [p.revenue_growth for p in peers if p.revenue_growth]

    return PeerMultiples(
        peers=peers,
        ev_ebitda_median=_median(ev_ebitdas),
        ev_revenue_median=_median(ev_revenues),
        pe_median=_median(pes),
        ebitda_margin_median=_median(margins),
        revenue_growth_median=_median(growths),
        ev_ebitda_low=_percentile(ev_ebitdas, 0.25),
        ev_ebitda_high=_percentile(ev_ebitdas, 0.75),
        ev_revenue_low=_percentile(ev_revenues, 0.25),
        ev_revenue_high=_percentile(ev_revenues, 0.75),
        n_peers=len(peers),
        source="yfinance_peers",
    )


def resolve_peer_tickers(raw_peers: list[dict]) -> list[str]:
    """
    Given LLM-returned peer list [{name, ticker, exchange}], resolve valid tickers.
    Falls back to yfinance resolve_ticker if provided ticker fails.
    """
    tickers: list[str] = []
    for p in raw_peers:
        ticker = p.get("ticker", "").strip().upper()
        if ticker:
            tickers.append(ticker)
            continue
        # Fallback: try to resolve by name
        name = p.get("name", "")
        if name:
            resolved = resolve_ticker(name)
            if resolved:
                tickers.append(resolved)
    return list(dict.fromkeys(tickers))  # deduplicate, preserve order


def parse_peer_agent_output(raw: str) -> list[dict]:
    """Parse JSON output from PeerFinderAgent."""
    try:
        data = json.loads(raw)
        return data.get("peers", [])
    except Exception:
        return []
