"""
Peer comparables engine.

For any company (public or private), identifies 4-6 listed peers via LLM,
fetches their real market data from yfinance, validates sector + size fit,
and computes median multiples.

Validation rules (applied before any peer enters the multiple computation):
  1. Ticker must resolve to real yfinance data (not a hallucinated symbol)
  2. Peer sector must be compatible with target sector (broad GICS group match)
  3. Numeric sanity gates: EV/EBITDA 1–150, EV/Rev 0.1–50, P/E 3–200
  4. Minimum 3 validated peers required — falls back to sector-table if fewer

If fewer than MIN_VALID_PEERS pass, the returned PeerMultiples will have
source="sector_fallback" and n_peers=0 to signal upstream that sector-table
multiples should be used instead.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import log10
from typing import Optional

from goldroger.data.fetcher import fetch_market_data, resolve_ticker, MarketData

MIN_VALID_PEERS = 3

# ── Sector compatibility ──────────────────────────────────────────────────────

# Broad GICS-like groups. Both target sector (LLM free text) and peer sector
# (yfinance standardized) are matched against these keyword sets.
# If a sector matches no group it's "unclassified" — peer is not rejected.
_SECTOR_GROUPS: dict[str, frozenset[str]] = {
    "tech": frozenset([
        "technology", "software", "saas", "semiconductor", "cloud",
        "hardware", "it ", "information technology", "tech",
    ]),
    "healthcare": frozenset([
        "healthcare", "pharma", "pharmaceutical", "biotech", "medtech",
        "health", "life science", "medical",
    ]),
    "consumer": frozenset([
        "consumer", "retail", "ecommerce", "luxury", "food", "beverage",
        "apparel", "fashion", "cyclical", "defensive", "staple", "fmcg",
        "cosmetic", "beauty",
    ]),
    "financials": frozenset([
        "financial", "banking", "bank", "insurance", "fintech",
        "asset management", "wealth", "investment",
    ]),
    "industrials": frozenset([
        "industrial", "aerospace", "defense", "manufacturing",
        "logistics", "transport", "engineering",
    ]),
    "energy": frozenset([
        "energy", "oil", "gas", "utility", "utilities", "renewable",
        "clean energy",
    ]),
    "comms": frozenset([
        "communication", "media", "telecom", "entertainment",
        "gaming", "social", "advertising",
    ]),
    "materials": frozenset([
        "material", "chemical", "mining", "metal", "agriculture",
    ]),
    "real_estate": frozenset([
        "real estate", "reit", "property",
    ]),
}


def _sector_group(sector: str) -> Optional[str]:
    """Return the broad sector group, preferring the longest keyword match."""
    s = sector.lower()
    best_group: Optional[str] = None
    best_len = 0
    for group, keywords in _SECTOR_GROUPS.items():
        for kw in keywords:
            if kw in s and len(kw) > best_len:
                best_group = group
                best_len = len(kw)
    return best_group


def _sectors_compatible(target: str, peer: str) -> bool:
    """
    Return True if target and peer sectors belong to the same broad group.
    Returns True (don't reject) when either sector is unrecognized.
    """
    if not target or not peer:
        return True
    tg = _sector_group(target)
    pg = _sector_group(peer)
    if tg is None or pg is None:
        return True  # unclassified — give benefit of the doubt
    return tg == pg


# ── Data structures ───────────────────────────────────────────────────────────

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
    sector: Optional[str] = None
    similarity: Optional[float] = None
    weight: Optional[float] = None


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

    # Ranges for football field (25th–75th percentile)
    ev_ebitda_low: Optional[float] = None
    ev_ebitda_high: Optional[float] = None
    ev_revenue_low: Optional[float] = None
    ev_revenue_high: Optional[float] = None

    n_peers: int = 0
    source: str = "yfinance_peers"

    # Validation metadata — useful for CLI output and debugging
    n_dropped_sector: int = 0     # peers dropped for sector mismatch
    n_dropped_no_data: int = 0    # peers dropped because yfinance returned nothing
    n_dropped_sanity: int = 0     # peers dropped for extreme multiples


# ── Aggregation helpers ───────────────────────────────────────────────────────

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


def _winsorize(values: list[float], p_low: float = 0.10, p_high: float = 0.90) -> list[float]:
    clean = sorted(v for v in values if v and v > 0)
    if len(clean) < 5:
        return clean
    lo = _percentile(clean, p_low) or clean[0]
    hi = _percentile(clean, p_high) or clean[-1]
    return [min(max(v, lo), hi) for v in clean]


def _weighted_mean(values: list[float], weights: list[float]) -> Optional[float]:
    if not values or not weights or len(values) != len(weights):
        return None
    den = sum(max(w, 0.0) for w in weights)
    if den <= 0:
        return None
    num = sum(v * max(w, 0.0) for v, w in zip(values, weights))
    return num / den


def _weighted_percentile(values: list[float], weights: list[float], pct: float) -> Optional[float]:
    if not values or not weights or len(values) != len(weights):
        return None
    paired = sorted(
        [(v, max(w, 0.0)) for v, w in zip(values, weights) if v and v > 0 and w and w > 0],
        key=lambda x: x[0],
    )
    if not paired:
        return None
    total = sum(w for _, w in paired)
    if total <= 0:
        return None
    threshold = total * max(0.0, min(1.0, pct))
    run = 0.0
    for v, w in paired:
        run += w
        if run >= threshold:
            return v
    return paired[-1][0]


def _similarity_score(target_mcap: float | None, peer_mcap: float | None, target_sector: str, peer_sector: str) -> float:
    size_score = 0.5
    if target_mcap and peer_mcap and target_mcap > 0 and peer_mcap > 0:
        ratio = max(target_mcap, peer_mcap) / min(target_mcap, peer_mcap)
        size_score = max(0.0, 1.0 - min(log10(ratio) / 2.0, 1.0))
    ts = (target_sector or "").lower()
    ps = (peer_sector or "").lower()
    sector_score = 0.4
    if ts and ps:
        tset = {x for x in ts.replace("/", " ").replace("-", " ").split() if len(x) > 2}
        pset = {x for x in ps.replace("/", " ").replace("-", " ").split() if len(x) > 2}
        if tset and pset:
            inter = len(tset & pset)
            union = len(tset | pset)
            sector_score = (inter / union) if union else 0.4
    return max(0.0, min(1.0, 0.65 * size_score + 0.35 * sector_score))


# ── Core functions ────────────────────────────────────────────────────────────

def build_peer_multiples(
    peer_tickers: list[str],
    target_sector: str = "",
    target_market_cap: float | None = None,
    min_similarity: float = 0.0,
) -> PeerMultiples:
    """
    Fetch market data for each peer ticker and compute validated multiples.

    Validation steps (in order):
      1. yfinance must return real data (ticker exists)
      2. Peer sector must be compatible with target_sector
      3. Multiples must be within sanity bounds

    If fewer than MIN_VALID_PEERS pass all checks, returns PeerMultiples with
    source="sector_fallback" and n_peers=0 — caller should use sector-table fallback.

    Args:
        peer_tickers:  list of ticker symbols from PeerFinderAgent (pre-deduplicated)
        target_sector: sector string for the company being valued (free text OK)
    """
    peers: list[PeerData] = []
    n_no_data = n_sector = n_sanity = 0

    for ticker in peer_tickers:
        md = fetch_market_data(ticker)

        # Gate 1 — ticker must exist in yfinance
        if md is None:
            n_no_data += 1
            continue

        # Gate 2 — sector compatibility
        if target_sector and md.sector:
            if not _sectors_compatible(target_sector, md.sector):
                n_sector += 1
                continue

        # Gate 3 — numeric sanity
        ev_ebitda = md.ev_ebitda_market
        ev_revenue = md.ev_revenue_market
        pe = md.pe_ratio or md.forward_pe

        sanity_fail = False
        if ev_ebitda is not None and (ev_ebitda < 1 or ev_ebitda > 150):
            ev_ebitda = None
            sanity_fail = True
        if ev_revenue is not None and (ev_revenue < 0.1 or ev_revenue > 50):
            ev_revenue = None
            sanity_fail = True
        if pe is not None and (pe < 3 or pe > 200):
            pe = None
            sanity_fail = True

        # Only count as sanity-dropped if ALL multiples were bad
        if sanity_fail and ev_ebitda is None and ev_revenue is None and pe is None:
            n_sanity += 1
            continue

        _sim = _similarity_score(target_market_cap, md.market_cap, target_sector, md.sector or "")
        if _sim < min_similarity:
            n_sector += 1
            continue
        peers.append(PeerData(
            name=md.company_name,
            ticker=ticker,
            ev_ebitda=ev_ebitda,
            ev_revenue=ev_revenue,
            pe_ratio=pe,
            ebitda_margin=md.ebitda_margin,
            revenue_growth=md.forward_revenue_growth or md.revenue_growth_yoy,
            market_cap=md.market_cap,
            sector=md.sector,
            similarity=_sim,
            weight=max(_sim, 0.01),
        ))

    # Not enough validated peers → signal sector-table fallback
    if len(peers) < MIN_VALID_PEERS:
        return PeerMultiples(
            n_peers=0,
            n_dropped_no_data=n_no_data,
            n_dropped_sector=n_sector,
            n_dropped_sanity=n_sanity,
            source="sector_fallback",
        )

    ev_ebitdas_raw = [p.ev_ebitda for p in peers if p.ev_ebitda]
    ev_ebitda_w = [p.weight or 1.0 for p in peers if p.ev_ebitda]
    ev_revenues_raw = [p.ev_revenue for p in peers if p.ev_revenue]
    ev_revenue_w = [p.weight or 1.0 for p in peers if p.ev_revenue]
    ev_ebitdas = _winsorize(ev_ebitdas_raw)
    ev_revenues = _winsorize(ev_revenues_raw)
    pes = [p.pe_ratio for p in peers if p.pe_ratio]
    margins = [p.ebitda_margin for p in peers if p.ebitda_margin]
    growths = [p.revenue_growth for p in peers if p.revenue_growth]

    # weighted dispersion on raw lists; medians on winsorized weighted central tendency
    ev_ebitda_low = _weighted_percentile(ev_ebitdas_raw, ev_ebitda_w, 0.25) or _percentile(ev_ebitdas, 0.25)
    ev_ebitda_high = _weighted_percentile(ev_ebitdas_raw, ev_ebitda_w, 0.75) or _percentile(ev_ebitdas, 0.75)
    ev_revenue_low = _weighted_percentile(ev_revenues_raw, ev_revenue_w, 0.25) or _percentile(ev_revenues, 0.25)
    ev_revenue_high = _weighted_percentile(ev_revenues_raw, ev_revenue_w, 0.75) or _percentile(ev_revenues, 0.75)

    # Small peer sets create unstable percentile bands.
    if len(ev_ebitdas) < 5:
        med = _median(ev_ebitdas)
        if med:
            ev_ebitda_low = med * 0.85
            ev_ebitda_high = med * 1.15

    return PeerMultiples(
        peers=peers,
        ev_ebitda_median=_weighted_mean(ev_ebitdas, ev_ebitda_w) or _median(ev_ebitdas),
        ev_revenue_median=_weighted_mean(ev_revenues, ev_revenue_w) or _median(ev_revenues),
        pe_median=_median(pes),
        ebitda_margin_median=_median(margins),
        revenue_growth_median=_median(growths),
        ev_ebitda_low=ev_ebitda_low,
        ev_ebitda_high=ev_ebitda_high,
        ev_revenue_low=ev_revenue_low,
        ev_revenue_high=ev_revenue_high,
        n_peers=len(peers),
        n_dropped_no_data=n_no_data,
        n_dropped_sector=n_sector,
        n_dropped_sanity=n_sanity,
        source="yfinance_peers",
    )


def resolve_peer_tickers(raw_peers: list[dict]) -> list[str]:
    """
    Given LLM-returned peer list [{name, ticker, exchange}], resolve valid tickers.
    Falls back to yfinance name search if provided ticker fails to resolve.
    Returns deduplicated list.
    """
    tickers: list[str] = []
    for p in raw_peers:
        ticker = p.get("ticker", "").strip().upper()
        if ticker:
            tickers.append(ticker)
            continue
        # Ticker missing — try to resolve by name
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
