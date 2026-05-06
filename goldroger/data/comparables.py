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

import httpx
import yfinance as yf

from goldroger.data.fetcher import fetch_market_data, resolve_ticker, MarketData
from goldroger.utils.cache import peer_universe_cache

MIN_VALID_PEERS = 3
_HTTP = httpx.Client(
    timeout=12,
    headers={"User-Agent": "Mozilla/5.0"},
    follow_redirects=True,
)

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
    industry: Optional[str] = None
    bucket: Optional[str] = None
    include_reason: Optional[str] = None
    similarity: Optional[float] = None
    weight: Optional[float] = None


@dataclass
class PeerMultiples:
    """Aggregated peer multiples — used as comps base in valuation."""
    peers: list[PeerData] = field(default_factory=list)

    # Medians (primary — less distorted by outliers)
    ev_ebitda_median: Optional[float] = None
    ev_ebitda_raw_median: Optional[float] = None
    ev_ebitda_weighted: Optional[float] = None
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
    n_dropped_scale: int = 0      # peers dropped for market-cap scale mismatch
    n_dropped_bucket: int = 0     # peers dropped by bucket-balance policy
    excluded_details: list[str] = field(default_factory=list)


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
    clean = [v for v in values if v and v > 0]
    if len(clean) < 5:
        return clean
    ordered = sorted(clean)
    lo = _percentile(ordered, p_low) or ordered[0]
    hi = _percentile(ordered, p_high) or ordered[-1]
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


def _econ_similarity(
    target_sector: str,
    peer_sector: str,
    target_mcap: float | None,
    peer_mcap: float | None,
    target_margin: float | None,
    peer_margin: float | None,
    target_growth: float | None,
    peer_growth: float | None,
) -> float:
    ts = (target_sector or "").lower()
    ps = (peer_sector or "").lower()
    industry_match = 1.0 if ts == ps and ts else (0.7 if _sectors_compatible(ts, ps) else 0.0)
    sector_match = 1.0 if _sectors_compatible(ts, ps) else 0.0
    business_model_match = 1.0 if _sector_group(ts or "") == _sector_group(ps or "") else 0.4
    mcap_similarity = _similarity_score(target_mcap, peer_mcap, target_sector, peer_sector)
    if target_margin is not None and peer_margin is not None:
        margin_similarity = max(0.0, 1.0 - min(abs(target_margin - peer_margin) / 0.25, 1.0))
    else:
        margin_similarity = 0.5
    if target_growth is not None and peer_growth is not None:
        growth_similarity = max(0.0, 1.0 - min(abs(target_growth - peer_growth) / 0.30, 1.0))
    else:
        growth_similarity = 0.5
    # Weighted formula requested by user + growth extension folded into business-model robustness.
    base = (
        0.30 * industry_match
        + 0.25 * sector_match
        + 0.20 * business_model_match
        + 0.15 * mcap_similarity
        + 0.10 * margin_similarity
    )
    return max(0.0, min(1.0, (base * 0.85) + (growth_similarity * 0.15)))


def _classify_peer_bucket(sector: str, industry: str, name: str = "") -> str:
    s = f"{sector or ''} {industry or ''} {name or ''}".lower()
    if any(k in s for k in ("semiconductor", "chip", "foundry", "memory", "gpu", "fabless")):
        return "semiconductors"
    if any(k in s for k in (
        "internet content", "internet retail", "interactive media",
        "communication services", "platform", "search", "social media",
        "cloud", "application software", "software infrastructure",
    )):
        return "ecosystem/platform"
    if any(k in s for k in (
        "software", "it services", "consulting", "enterprise services",
        "managed services", "saas",
    )):
        return "software/services"
    if any(k in s for k in (
        "consumer electronics", "hardware", "computer", "smartphone",
        "pc", "devices", "peripherals", "wearable",
    )):
        return "consumer hardware"
    return "other"


def _target_profile(target_sector: str, target_industry: str) -> str:
    t = f"{target_sector or ''} {target_industry or ''}".lower()
    if any(k in t for k in ("semiconductor", "chip", "foundry", "memory", "gpu", "fabless")):
        return "semiconductor"
    if any(k in t for k in ("consumer electronics", "hardware", "devices", "smartphone", "pc")) and "tech" in t:
        return "ecosystem_consumer_tech"
    if any(k in t for k in ("software", "internet", "platform", "communication services", "cloud")):
        return "platform_software"
    return "general_tech"


def _bucket_weight_for_profile(profile: str, bucket: str) -> float:
    if profile == "semiconductor":
        return {
            "semiconductors": 1.00,
            "ecosystem/platform": 0.70,
            "software/services": 0.60,
            "consumer hardware": 0.60,
            "other": 0.50,
        }.get(bucket, 0.50)
    if profile == "ecosystem_consumer_tech":
        return {
            "ecosystem/platform": 1.00,
            "consumer hardware": 0.95,
            "software/services": 0.85,
            "semiconductors": 0.45,
            "other": 0.55,
        }.get(bucket, 0.55)
    if profile == "platform_software":
        return {
            "ecosystem/platform": 1.00,
            "software/services": 0.95,
            "consumer hardware": 0.65,
            "semiconductors": 0.60,
            "other": 0.55,
        }.get(bucket, 0.55)
    return {
        "ecosystem/platform": 0.90,
        "software/services": 0.85,
        "consumer hardware": 0.80,
        "semiconductors": 0.70,
        "other": 0.60,
    }.get(bucket, 0.60)


def _bucket_similarity_factor(profile: str, bucket: str) -> float:
    if profile == "semiconductor":
        return {
            "semiconductors": 1.00,
            "ecosystem/platform": 0.70,
            "software/services": 0.65,
            "consumer hardware": 0.65,
            "other": 0.55,
        }.get(bucket, 0.55)
    if profile == "ecosystem_consumer_tech":
        return {
            "ecosystem/platform": 1.00,
            "software/services": 0.92,
            "consumer hardware": 0.88,
            "semiconductors": 0.55,
            "other": 0.50,
        }.get(bucket, 0.50)
    if profile == "platform_software":
        return {
            "ecosystem/platform": 1.00,
            "software/services": 0.95,
            "consumer hardware": 0.72,
            "semiconductors": 0.62,
            "other": 0.55,
        }.get(bucket, 0.55)
    return {
        "ecosystem/platform": 0.92,
        "software/services": 0.90,
        "consumer hardware": 0.78,
        "semiconductors": 0.68,
        "other": 0.58,
    }.get(bucket, 0.58)


def _bucket_budgets(profile: str) -> dict[str, float]:
    if profile == "ecosystem_consumer_tech":
        return {
            "ecosystem/platform": 0.30,
            "software/services": 0.20,
            "consumer hardware": 0.30,
            "semiconductors": 0.20,
            "other": 0.00,
        }
    if profile == "platform_software":
        return {
            "ecosystem/platform": 0.35,
            "software/services": 0.30,
            "consumer hardware": 0.15,
            "semiconductors": 0.15,
            "other": 0.05,
        }
    if profile == "semiconductor":
        return {
            "ecosystem/platform": 0.10,
            "software/services": 0.10,
            "consumer hardware": 0.10,
            "semiconductors": 0.65,
            "other": 0.05,
        }
    return {
        "ecosystem/platform": 0.28,
        "software/services": 0.22,
        "consumer hardware": 0.22,
        "semiconductors": 0.20,
        "other": 0.08,
    }


def _normalize_bucket_weights(peers: list[PeerData], profile: str) -> list[PeerData]:
    if not peers:
        return peers
    budgets = _bucket_budgets(profile)
    buckets = {}
    for p in peers:
        b = p.bucket or "other"
        buckets.setdefault(b, []).append(p)

    # Activate only buckets that actually have peers.
    active = {b for b, arr in buckets.items() if arr}
    target = {b: (budgets.get(b, 0.0) if b in active else 0.0) for b in budgets}
    active_sum = sum(target.values())

    # If some budgeted buckets are missing, redistribute to non-semi active buckets first.
    if active_sum < 0.999 and active:
        missing = 1.0 - active_sum
        pref = [b for b in ("ecosystem/platform", "software/services", "consumer hardware", "other", "semiconductors") if b in active]
        if pref:
            denom = sum(max(target.get(b, 0.0), 0.01) for b in pref)
            for b in pref:
                target[b] = target.get(b, 0.0) + missing * (max(target.get(b, 0.0), 0.01) / denom)

    # Enforce semiconductor cap for Apple-like profiles.
    if profile in {"ecosystem_consumer_tech", "platform_software", "general_tech"}:
        semi_cap = 0.25 if profile == "ecosystem_consumer_tech" else 0.30
        semi_w = target.get("semiconductors", 0.0)
        if semi_w > semi_cap:
            excess = semi_w - semi_cap
            target["semiconductors"] = semi_cap
            recipients = [b for b in ("ecosystem/platform", "software/services", "consumer hardware", "other") if b in active]
            if recipients:
                denom = sum(max(target.get(b, 0.0), 0.01) for b in recipients)
                for b in recipients:
                    target[b] = target.get(b, 0.0) + excess * (max(target.get(b, 0.0), 0.01) / denom)
        # Ensure platform+software floor for ecosystem profiles.
        if profile == "ecosystem_consumer_tech":
            ps = target.get("ecosystem/platform", 0.0) + target.get("software/services", 0.0)
            if ps < 0.40:
                need = 0.40 - ps
                donors = [b for b in ("semiconductors", "other", "consumer hardware") if target.get(b, 0.0) > 0.0]
                for d in donors:
                    take = min(need, max(0.0, target[d] - (0.25 if d == "consumer hardware" else 0.0)))
                    if take <= 0:
                        continue
                    target[d] -= take
                    target["ecosystem/platform"] = target.get("ecosystem/platform", 0.0) + (take * 0.6)
                    target["software/services"] = target.get("software/services", 0.0) + (take * 0.4)
                    need -= take
                    if need <= 1e-6:
                        break

    # Normalize final bucket targets.
    t_sum = sum(v for v in target.values() if v > 0)
    if t_sum <= 0:
        eq = 1.0 / len(active)
        target = {b: (eq if b in active else 0.0) for b in target}
    else:
        for b in list(target.keys()):
            target[b] = max(0.0, target[b]) / t_sum

    # Allocate within each bucket by current peer weights.
    for b, arr in buckets.items():
        bw = target.get(b, 0.0)
        if bw <= 0 or not arr:
            for p in arr:
                p.weight = 0.0
            continue
        denom = sum(max(p.weight or 0.0, 0.001) for p in arr)
        for p in arr:
            p.weight = bw * (max(p.weight or 0.0, 0.001) / denom)
    return peers


def _search_yahoo_tickers(query: str, limit: int = 12) -> list[str]:
    if not query.strip():
        return []
    try:
        resp = _HTTP.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": max(5, min(limit, 25)), "newsCount": 0},
        )
        quotes = resp.json().get("quotes", [])
        out: list[str] = []
        for q in quotes:
            sym = (q.get("symbol") or "").upper().strip()
            qt = q.get("quoteType") or ""
            if not sym or qt not in ("EQUITY", "ETF"):
                continue
            out.append(sym)
        return list(dict.fromkeys(out))[:limit]
    except Exception:
        return []


def find_peers_dynamic(
    company_name: str,
    target_sector: str,
    target_market_cap: float | None,
    seed_tickers: list[str],
) -> list[str]:
    """Dynamic staged peer discovery (no hardcoded peer tickers)."""
    peers: list[str] = []
    peers.extend([t.upper() for t in seed_tickers if t])

    # Stage 1: same industry / sector
    peers.extend(_search_yahoo_tickers(f"{target_sector} companies", limit=10))
    peers.extend(_search_yahoo_tickers(f"{company_name} competitors", limit=10))

    # Stage 2: same sector + similar size phrasing
    if target_market_cap and target_market_cap > 500_000:
        size_label = "mega cap"
    elif target_market_cap and target_market_cap > 50_000:
        size_label = "large cap"
    else:
        size_label = "mid cap"
    peers.extend(_search_yahoo_tickers(f"{size_label} {target_sector}", limit=10))

    # Stage 3: adjacent industries
    if len(set(peers)) < 5:
        grp = _sector_group(target_sector or "")
        adjacent = {
            "tech": ["communication services", "internet services", "semiconductors"],
            "comms": ["technology", "internet platforms"],
            "consumer": ["technology retail", "internet retail"],
        }.get(grp or "", ["global equities"])
        for q in adjacent:
            peers.extend(_search_yahoo_tickers(q, limit=8))

    # Stage 4: global similar business models
    if len(set(peers)) < 3:
        peers.extend(_search_yahoo_tickers(f"global {target_sector} leaders", limit=12))

    # Small resolver pass for plain names if needed
    if len(set(peers)) < 3:
        for q in [company_name, target_sector]:
            t = resolve_ticker(q)
            if t:
                peers.append(t.upper())

    return list(dict.fromkeys(peers))


def find_peers_deterministic_quick(
    target_md: MarketData | None,
    target_sector: str,
    target_industry: str = "",
    target_peers: int = 12,
) -> list[str]:
    """
    Deterministic quick peer universe (no LLM / no generic web-search endpoint).
    Uses yfinance Sector/Industry endpoints and caches the universe for 24h.
    """
    if target_md is None:
        return []

    meta = target_md.additional_metadata if isinstance(target_md.additional_metadata, dict) else {}
    sector_key = str(meta.get("sector_key") or "").strip()
    industry_key = str(meta.get("industry_key") or "").strip()
    cache_key = f"quick_peers:{sector_key}:{industry_key}:{target_sector}:{target_industry}"
    cached = peer_universe_cache.get(cache_key)
    if cached is not None:
        return list(cached)

    candidates: list[str] = []

    def _extract_symbols(df_like) -> list[str]:
        out: list[str] = []
        try:
            if df_like is None:
                return out
            if hasattr(df_like, "columns") and "symbol" in getattr(df_like, "columns", []):
                out.extend([str(x).upper() for x in df_like["symbol"].tolist() if str(x).strip()])
            if hasattr(df_like, "index"):
                out.extend([str(x).upper() for x in list(df_like.index) if str(x).strip()])
        except Exception:
            pass
        return out

    try:
        if industry_key:
            ind = yf.Industry(industry_key)
            candidates.extend(_extract_symbols(getattr(ind, "top_performing_companies", None)))
            candidates.extend(_extract_symbols(getattr(ind, "top_growth_companies", None)))
    except Exception:
        pass

    try:
        if sector_key:
            sec = yf.Sector(sector_key)
            candidates.extend(_extract_symbols(getattr(sec, "top_companies", None)))
            # yfinance Sector.industries may contain representative symbols.
            industries = getattr(sec, "industries", None)
            candidates.extend(_extract_symbols(industries))
    except Exception:
        pass

    # For non-semiconductor mega-cap tech, broaden into adjacent platform sectors
    # so semis do not dominate the peer set.
    _profile = _target_profile(target_sector, target_industry)
    _is_mega_cap = bool(target_md.market_cap and target_md.market_cap > 500_000)
    if _is_mega_cap and _profile in {"ecosystem_consumer_tech", "platform_software"}:
        try:
            comms = yf.Sector("communication-services")
            candidates.extend(_extract_symbols(getattr(comms, "top_companies", None)))
        except Exception:
            pass
        try:
            cyc = yf.Sector("consumer-cyclical")
            candidates.extend(_extract_symbols(getattr(cyc, "top_companies", None)))
        except Exception:
            pass

    # Deterministic fallback: narrow yahoo-finance endpoint query (not LLM/web-search agent).
    if len(set(candidates)) < 5:
        candidates.extend(_search_yahoo_tickers(f"{target_sector} equities", limit=20))
    if _is_mega_cap and _profile in {"ecosystem_consumer_tech", "platform_software"}:
        candidates.extend(_search_yahoo_tickers("internet platform mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("communication services mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("consumer hardware mega cap equities", limit=12))

    # Deduplicate, remove self ticker, and cap.
    self_ticker = (target_md.ticker or "").upper().strip()
    out = [t for t in dict.fromkeys(candidates) if t and t != self_ticker]
    out = out[:max(5, target_peers)]
    peer_universe_cache.set(cache_key, out)
    return out


# ── Core functions ────────────────────────────────────────────────────────────

def build_peer_multiples(
    peer_tickers: list[str],
    target_sector: str = "",
    target_industry: str = "",
    target_market_cap: float | None = None,
    min_similarity: float = 0.0,
    target_ebitda_margin: float | None = None,
    target_growth: float | None = None,
    min_market_cap_ratio: float = 0.0,
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
    n_no_data = n_sector = n_sanity = n_scale = n_bucket = 0
    excluded: list[str] = []
    profile = _target_profile(target_sector, target_industry)

    for ticker in peer_tickers:
        md = fetch_market_data(ticker)

        # Gate 1 — ticker must exist in yfinance
        if md is None:
            n_no_data += 1
            excluded.append(f"{ticker}: not found")
            continue

        # Gate 2 — sector compatibility
        if target_sector and md.sector:
            if not _sectors_compatible(target_sector, md.sector):
                n_sector += 1
                excluded.append(f"{ticker}: sector mismatch")
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
            excluded.append(f"{ticker}: invalid multiples")
            continue

        _sim = _econ_similarity(
            target_sector=target_sector,
            peer_sector=md.sector or "",
            target_mcap=target_market_cap,
            peer_mcap=md.market_cap,
            target_margin=target_ebitda_margin,
            peer_margin=md.ebitda_margin,
            target_growth=target_growth,
            peer_growth=(md.forward_revenue_growth or md.revenue_growth_yoy),
        )
        _industry = ""
        if isinstance(md.additional_metadata, dict):
            _industry = str(md.additional_metadata.get("industry") or "")
        _bucket = _classify_peer_bucket(md.sector or "", _industry, md.company_name or "")
        _b_weight = _bucket_weight_for_profile(profile, _bucket)
        _sim = _sim * _bucket_similarity_factor(profile, _bucket)

        # Gate 2b — scale compatibility with bucket-aware floor for mega-cap targets.
        if (
            min_market_cap_ratio > 0
            and target_market_cap
            and target_market_cap > 0
            and md.market_cap
            and md.market_cap > 0
        ):
            global_floor = max(100_000.0, target_market_cap * min_market_cap_ratio)
            adjacent_floor = max(50_000.0, target_market_cap * 0.02)
            floor = adjacent_floor if _bucket in {"semiconductors", "other"} else global_floor
            if md.market_cap < floor:
                n_scale += 1
                excluded.append(f"{ticker}: below market-cap floor")
                continue

        # Outlier cap for adjacent semiconductors in Apple-like profiles.
        _outlier_capped = False
        if (
            profile in {"ecosystem_consumer_tech", "platform_software"}
            and _bucket == "semiconductors"
            and ev_ebitda is not None
            and ev_ebitda > 40.0
        ):
            ev_ebitda = 40.0
            _outlier_capped = True

        if _sim < min_similarity:
            n_sector += 1
            excluded.append(f"{ticker}: similarity below threshold")
            continue
        if _sim < 0.35:
            n_sector += 1
            excluded.append(f"{ticker}: business-model similarity too low")
            continue

        include_reason = "adjacent: sector/size fit"
        if profile == "semiconductor" and _bucket == "semiconductors":
            include_reason = "core: same semiconductor profile"
        elif _bucket in {"ecosystem/platform", "consumer hardware", "software/services"}:
            include_reason = "core: business model aligned"
        elif _bucket == "semiconductors" and profile != "semiconductor":
            include_reason = "adjacent: semiconductor exposure"
        if _outlier_capped:
            include_reason += " (outlier EV/EBITDA capped)"
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
            industry=_industry,
            bucket=_bucket,
            include_reason=include_reason,
            similarity=_sim,
            weight=max(_sim * _b_weight, 0.01),
        ))

    # Bucket-balance policy for non-semi tech: avoid semiconductor-dominated peer sets.
    if peers and profile in {"ecosystem_consumer_tech", "platform_software", "general_tech"}:
        semis = [p for p in peers if p.bucket == "semiconductors"]
        non_semis = [p for p in peers if p.bucket != "semiconductors"]
        if semis and len(semis) > max(2, int(0.35 * len(peers))):
            keep_n = max(2, int(0.35 * len(peers)))
            semis_sorted = sorted(semis, key=lambda p: (p.weight or 0.0), reverse=True)
            semis_keep = semis_sorted[:keep_n]
            semis_drop = semis_sorted[keep_n:]
            peers = non_semis + semis_keep
            n_bucket += len(semis_drop)
            excluded.extend([f"{p.ticker}: bucket-balance cap" for p in semis_drop])

    peers = _normalize_bucket_weights(peers, profile)
    peers = sorted(peers, key=lambda p: (p.weight or 0.0), reverse=True)

    # No validated peers.
    if len(peers) == 0:
        return PeerMultiples(
            n_peers=0,
            n_dropped_no_data=n_no_data,
            n_dropped_sector=n_sector,
            n_dropped_sanity=n_sanity,
            n_dropped_scale=n_scale,
            n_dropped_bucket=n_bucket,
            excluded_details=excluded[:30],
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

    if len(ev_ebitdas) < 5:
        _ev_ebitda_central = _median(ev_ebitdas)
        _ev_ebitda_weighted = _weighted_mean(ev_ebitdas, ev_ebitda_w)
    else:
        _ev_ebitda_weighted = (
            _weighted_percentile(ev_ebitdas, ev_ebitda_w, 0.50)
            or _weighted_mean(ev_ebitdas, ev_ebitda_w)
        )
        _ev_ebitda_central = (
            _ev_ebitda_weighted
            or _median(ev_ebitdas)
        )
    if len(ev_revenues) < 5:
        _ev_revenue_central = _median(ev_revenues)
    else:
        _ev_revenue_central = (
            _weighted_percentile(ev_revenues, ev_revenue_w, 0.50)
            or _weighted_mean(ev_revenues, ev_revenue_w)
            or _median(ev_revenues)
        )

    return PeerMultiples(
        peers=peers,
        ev_ebitda_median=_ev_ebitda_central,
        ev_ebitda_raw_median=_median(ev_ebitdas_raw),
        ev_ebitda_weighted=_ev_ebitda_weighted,
        ev_revenue_median=_ev_revenue_central,
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
        n_dropped_scale=n_scale,
        n_dropped_bucket=n_bucket,
        excluded_details=excluded[:30],
        source=("yfinance_peers" if len(peers) >= MIN_VALID_PEERS else "yfinance_peers_low_confidence"),
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
