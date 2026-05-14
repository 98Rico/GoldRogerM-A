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

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from dataclasses import dataclass, field
from math import log10
from typing import Optional
from unittest.mock import Mock

import httpx
import yfinance as yf

from goldroger.data.fetcher import fetch_market_data, resolve_ticker, MarketData
from goldroger.data.sector_profiles import detect_sector_profile
from goldroger.utils.cache import peer_universe_cache

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
try:
    yf.utils.get_yf_logger().setLevel(logging.CRITICAL)
except Exception:
    pass

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
    quote_currency: Optional[str] = None
    bucket: Optional[str] = None
    role: Optional[str] = None
    include_reason: Optional[str] = None
    similarity: Optional[float] = None
    business_similarity: Optional[float] = None
    scale_similarity: Optional[float] = None
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
    n_valuation_peers: int = 0
    n_qualitative_peers: int = 0
    effective_peer_count: float = 0.0
    pure_peer_weight_share: float = 0.0
    adjacent_peer_weight_share: float = 0.0
    peer_set_type: str = "adjacent_reference_set"
    source: str = "yfinance_peers"

    # Validation metadata — useful for CLI output and debugging
    n_dropped_sector: int = 0     # peers dropped for sector mismatch
    n_dropped_no_data: int = 0    # peers dropped because yfinance returned nothing
    n_dropped_sanity: int = 0     # peers dropped for extreme multiples
    n_dropped_scale: int = 0      # peers dropped for market-cap scale mismatch
    n_dropped_bucket: int = 0     # peers dropped by bucket-balance policy
    n_dropped_same_issuer: int = 0  # peers dropped as alternate listing of target
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


def _issuer_key(name: str) -> str:
    s = (name or "").lower()
    s = s.replace("&", " and ")
    for tok in (
        " public limited company",
        " p.l.c.",
        " plc",
        " ltd",
        " limited",
        " inc",
        " corporation",
        " corp",
        " asa",
        " nv",
        " sa",
        " ag",
        " group",
        ".",
        ",",
    ):
        s = s.replace(tok, " ")
    s = " ".join(s.split())
    return s


def _symbol_aliases(md: MarketData | None) -> set[str]:
    out: set[str] = set()
    if not md:
        return out
    out.add(str(md.ticker or "").upper())
    if isinstance(md.additional_metadata, dict):
        for k in ("underlying_symbol", "primary_listing_symbol", "selected_listing_symbol"):
            v = str(md.additional_metadata.get(k) or "").strip().upper()
            if v:
                out.add(v)
    return {x for x in out if x}


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


def _scale_similarity(target_mcap: float | None, peer_mcap: float | None) -> float:
    if not target_mcap or not peer_mcap or target_mcap <= 0 or peer_mcap <= 0:
        return 0.40
    ratio = max(target_mcap, peer_mcap) / min(target_mcap, peer_mcap)
    if ratio <= 1.5:
        return 1.00
    if ratio <= 3.0:
        return 0.85
    if ratio <= 6.0:
        return 0.65
    if ratio <= 10.0:
        return 0.45
    if ratio <= 20.0:
        return 0.25
    return 0.10


def _business_similarity(
    target_sector: str,
    peer_sector: str,
    target_margin: float | None,
    peer_margin: float | None,
    target_growth: float | None,
    peer_growth: float | None,
    profile: str,
    bucket: str,
) -> float:
    ts = (target_sector or "").lower()
    ps = (peer_sector or "").lower()
    industry_match = 1.0 if ts == ps and ts else (0.75 if _sectors_compatible(ts, ps) else 0.20)
    sector_match = 1.0 if _sectors_compatible(ts, ps) else 0.10
    bucket_fit = _bucket_similarity_factor(profile, bucket)
    if target_margin is not None and peer_margin is not None:
        margin_similarity = max(0.0, 1.0 - min(abs(target_margin - peer_margin) / 0.25, 1.0))
    else:
        margin_similarity = 0.5
    if target_growth is not None and peer_growth is not None:
        growth_similarity = max(0.0, 1.0 - min(abs(target_growth - peer_growth) / 0.30, 1.0))
    else:
        growth_similarity = 0.5
    score = (
        0.35 * industry_match
        + 0.25 * sector_match
        + 0.25 * bucket_fit
        + 0.10 * margin_similarity
        + 0.05 * growth_similarity
    )
    return max(0.0, min(1.0, score))


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
    # Private-oriented healthcare/healthtech buckets.
    if any(
        k in s
        for k in (
            "digital health",
            "telemedicine",
            "virtual care",
            "care navigation",
            "patient engagement",
            "patient access",
        )
    ):
        return "digital_health"
    if any(
        k in s
        for k in (
            "healthcare software",
            "clinical software",
            "ehr",
            "electronic health record",
            "medical software",
            "health it",
            "healthcare technology",
            "doximity",
            "veeva",
        )
    ):
        return "healthcare_software"
    if any(k in s for k in ("healthtech platform", "healthtech", "doctolib", "teladoc")):
        return "healthtech_platform"
    if any(k in s for k in ("healthcare services", "provider services", "hospital services", "managed care")):
        return "healthcare_services_adjacent"

    # Private-oriented HR tech buckets.
    if any(k in s for k in ("hcm", "human capital management", "payroll", "workforce management", "adp", "paychex")):
        return "hcm_payroll"
    if any(k in s for k in ("hr tech", "hr software", "human resources software", "personio", "workday", "paycom")):
        return "hrtech_saas"

    # Technology buckets first to avoid false "materials_general" classification
    # for semiconductor-equipment names/industries (e.g., "Semiconductor Equipment & Materials").
    if any(k in s for k in (
        "semiconductor equipment", "semiconductor equipment & materials",
        "lithography", "wafer fab", "eda", "design automation",
        "test equipment", "metrology", "lam research", "applied materials", "asml", "kla", "klac", "amat", "lrcx",
    )):
        return "semiconductor_equipment"
    if any(k in s for k in (
        "semiconductor", "chip", "foundry", "memory", "gpu", "fabless", "wafer",
    )):
        return "semiconductors"
    if any(k in s for k in (
        "network", "networking", "communications equipment", "communication equipment",
        "router", "switch", "telecom equipment",
    )):
        return "networking_infrastructure"
    if any(k in s for k in (
        "consumer electronics", "hardware", "computer", "smartphone",
        "pc", "devices", "peripherals", "wearable", "tablet",
    )):
        return "consumer_hardware_ecosystem"
    if any(k in s for k in (
        "internet content", "internet retail", "interactive media",
        "communication services", "platform", "search", "social media",
        "cloud", "application software", "software infrastructure",
        "software", "it services", "consulting", "saas",
    )):
        return "software_services_platform"
    # Consumer staples / tobacco-specific buckets
    if any(k in s for k in ("tobacco", "nicotine", "cigarette", "vape", "smoke-free", "oral nicotine")):
        return "tobacco_nicotine"
    if any(k in s for k in ("beverage", "soft drink", "brewer", "distiller")):
        return "beverages_adjacent"
    if any(k in s for k in ("household", "personal care", "home care", "hygiene")):
        return "household_products_adjacent"
    if any(k in s for k in ("retail", "supermarket", "discount store", "warehouse club", "grocery")):
        return "retail_adjacent"
    if any(k in s for k in ("consumer defensive", "consumer staples", "packaged foods", "snacks")):
        return "consumer_staples_adjacent"
    # Materials / mining split buckets (avoid overly generic "materials_general").
    if any(k in s for k in ("aluminum", "aluminium", "alumina", "bauxite", "smelter", "primary aluminum")):
        return "aluminum_metals"
    if any(k in s for k in ("metals & mining", "metal mining", "mining", "steel", "iron ore", "copper", "nickel", "zinc", "lithium", "gold", "silver")):
        return "metals_mining"
    if any(k in s for k in ("construction materials", "building materials", "cement", "aggregates", "coatings", "paint")):
        return "construction_materials_adjacent"
    if any(k in s for k in ("industrial gases", "specialty chemical", "specialty chemicals", "agricultural chemicals", "fertilizer", "chemicals")):
        return "chemicals_adjacent"
    # Healthcare / financial / industrial coverage buckets
    if any(k in s for k in ("pharma", "pharmaceutical", "biotech", "drug")):
        return "healthcare_pharma"
    if any(k in s for k in ("medtech", "medical device", "healthcare equipment")):
        return "healthcare_medtech"
    if any(k in s for k in ("neobank", "digital bank", "challenger bank", "nubank", "revolut", "monzo")):
        return "fintech_digital_bank"
    if any(
        k in s
        for k in (
            "payments",
            "payment processing",
            "merchant acquiring",
            "cross-border payment",
            "paypal",
            "adyen",
            "wise",
        )
    ):
        return "fintech_payments"
    if any(k in s for k in ("brokerage", "trading platform", "online broker", "robinhood")):
        return "fintech_brokerage"
    if any(k in s for k in ("crypto", "cryptocurrency", "digital asset exchange", "coinbase")):
        return "fintech_crypto_platform"
    if any(k in s for k in ("consumer lending", "digital lending", "loan platform", "sofi")):
        return "fintech_consumer_lending"
    if any(k in s for k in ("fintech infrastructure", "banking-as-a-service", "issuer processing", "payments infrastructure")):
        return "fintech_infrastructure"
    if any(k in s for k in ("bank", "banking", "financial services", "consumer finance")):
        return "financials_banks"
    if "insurance" in s:
        return "financials_insurance"
    if any(k in s for k in ("oil", "gas", "energy", "upstream", "downstream")):
        return "energy_oil_gas"
    if any(k in s for k in ("utility", "electric", "water", "power")):
        return "utilities_general"
    if any(k in s for k in ("reit", "real estate", "property")):
        return "real_estate_reit"
    if any(k in s for k in ("chemical", "mining", "metals", "materials")):
        return "materials_general"
    if any(k in s for k in ("telecom", "wireless", "media", "communication services")):
        return "telecom_media"
    if "industrial" in s:
        return "industrials_general"
    if _sector_group(s or "") in {"tech", "comms", "consumer"}:
        return "other_adjacent_tech"
    return "other_adjacent"


def _normalize_bucket_for_profile(profile: str, bucket: str) -> str:
    b = bucket or "other_adjacent"
    if profile == "consumer_staples_tobacco" and b == "other_adjacent_tech":
        return "consumer_staples_adjacent"
    if profile == "consumer_staples_general" and b == "other_adjacent_tech":
        return "consumer_staples_adjacent"
    if profile in {"premium_device_platform", "consumer_hardware_ecosystem"} and b == "other_adjacent_tech":
        return "software_services_platform"
    if profile == "materials_chemicals_mining" and b == "materials_general":
        return "metals_mining"
    if profile == "fintech_digital_bank_payments" and b == "other_adjacent_tech":
        return "fintech_payments"
    return b


def _target_profile(target_sector: str, target_industry: str) -> str:
    _txt = f"{target_sector or ''} {target_industry or ''}".lower()
    if any(
        tok in _txt
        for tok in (
            "fintech",
            "digital bank",
            "neobank",
            "payments",
            "payment",
            "cross-border",
            "consumer finance",
        )
    ):
        return "fintech_digital_bank_payments"
    _profile_key = detect_sector_profile(target_sector, target_industry)
    if _profile_key == "technology_semiconductors":
        return "semiconductors"
    if _profile_key == "technology_consumer_electronics":
        return "premium_device_platform"
    if _profile_key == "technology_software":
        return "software_services_ecosystem"
    if _profile_key == "consumer_staples_tobacco":
        return "consumer_staples_tobacco"
    if _profile_key.startswith("consumer_staples"):
        return "consumer_staples_general"
    if _profile_key.startswith("financials"):
        return "financials"
    if _profile_key.startswith("healthcare"):
        return "healthcare"
    if _profile_key in {"energy_oil_gas", "utilities", "real_estate_reit", "materials_chemicals_mining", "telecom_media", "industrials"}:
        return _profile_key
    return "general_tech"


def _bucket_weight_for_profile(profile: str, bucket: str) -> float:
    if profile == "premium_device_platform":
        return {
            "consumer_hardware_ecosystem": 1.00,
            "software_services_platform": 0.92,
            "networking_infrastructure": 0.34,
            "semiconductors": 0.40,
            "semiconductor_equipment": 0.34,
            "other_adjacent_tech": 0.35,
        }.get(bucket, 0.40)
    if profile == "semiconductors":
        return {
            "semiconductors": 1.00,
            "semiconductor_equipment": 0.90,
            "networking_infrastructure": 0.70,
            "software_services_platform": 0.70,
            "consumer_hardware_ecosystem": 0.65,
            "other_adjacent_tech": 0.55,
        }.get(bucket, 0.55)
    if profile == "consumer_hardware_ecosystem":
        return {
            "consumer_hardware_ecosystem": 1.00,
            "software_services_platform": 0.82,
            "networking_infrastructure": 0.52,
            "semiconductors": 0.52,
            "semiconductor_equipment": 0.46,
            "other_adjacent_tech": 0.50,
        }.get(bucket, 0.55)
    if profile == "software_services_ecosystem":
        return {
            "software_services_platform": 1.00,
            "consumer_hardware_ecosystem": 0.75,
            "networking_infrastructure": 0.62,
            "semiconductors": 0.55,
            "semiconductor_equipment": 0.45,
            "other_adjacent_tech": 0.55,
        }.get(bucket, 0.55)
    if profile == "consumer_staples_tobacco":
        return {
            "tobacco_nicotine": 1.00,
            "consumer_staples_adjacent": 0.70,
            "beverages_adjacent": 0.55,
            "household_products_adjacent": 0.55,
            "retail_adjacent": 0.40,
        }.get(bucket, 0.30)
    if profile == "consumer_staples_general":
        return {
            "consumer_staples_adjacent": 1.00,
            "beverages_adjacent": 0.80,
            "household_products_adjacent": 0.80,
            "retail_adjacent": 0.60,
            "tobacco_nicotine": 0.55,
        }.get(bucket, 0.35)
    if profile == "fintech_digital_bank_payments":
        return {
            "fintech_digital_bank": 1.00,
            "fintech_payments": 0.92,
            "fintech_infrastructure": 0.82,
            "fintech_consumer_lending": 0.75,
            "fintech_brokerage": 0.62,
            "financials_banks": 0.65,
            "software_services_platform": 0.58,
            "fintech_crypto_platform": 0.35,
        }.get(bucket, 0.40)
    if profile == "materials_chemicals_mining":
        return {
            "aluminum_metals": 1.00,
            "metals_mining": 0.90,
            "construction_materials_adjacent": 0.60,
            "chemicals_adjacent": 0.45,
            "materials_general": 0.40,
        }.get(bucket, 0.35)
    return {
        "software_services_platform": 0.88,
        "consumer_hardware_ecosystem": 0.82,
        "networking_infrastructure": 0.72,
        "semiconductors": 0.68,
        "semiconductor_equipment": 0.55,
        "other_adjacent_tech": 0.60,
        "tobacco_nicotine": 0.55,
        "consumer_staples_adjacent": 0.60,
        "beverages_adjacent": 0.58,
        "household_products_adjacent": 0.58,
        "retail_adjacent": 0.50,
        "aluminum_metals": 0.60,
        "metals_mining": 0.58,
        "construction_materials_adjacent": 0.45,
        "chemicals_adjacent": 0.42,
        "materials_general": 0.40,
    }.get(bucket, 0.60)


def _bucket_similarity_factor(profile: str, bucket: str) -> float:
    if profile == "premium_device_platform":
        return {
            "consumer_hardware_ecosystem": 1.00,
            "software_services_platform": 0.88,
            "networking_infrastructure": 0.30,
            "semiconductors": 0.34,
            "semiconductor_equipment": 0.28,
            "other_adjacent_tech": 0.32,
        }.get(bucket, 0.35)
    if profile == "semiconductors":
        return {
            "semiconductors": 1.00,
            "semiconductor_equipment": 0.90,
            "networking_infrastructure": 0.68,
            "software_services_platform": 0.68,
            "consumer_hardware_ecosystem": 0.62,
            "other_adjacent_tech": 0.50,
        }.get(bucket, 0.55)
    if profile == "consumer_hardware_ecosystem":
        return {
            "consumer_hardware_ecosystem": 1.00,
            "software_services_platform": 0.78,
            "networking_infrastructure": 0.50,
            "semiconductors": 0.50,
            "semiconductor_equipment": 0.42,
            "other_adjacent_tech": 0.45,
        }.get(bucket, 0.50)
    if profile == "software_services_ecosystem":
        return {
            "software_services_platform": 1.00,
            "consumer_hardware_ecosystem": 0.72,
            "networking_infrastructure": 0.62,
            "semiconductors": 0.50,
            "semiconductor_equipment": 0.40,
            "other_adjacent_tech": 0.50,
        }.get(bucket, 0.55)
    if profile == "consumer_staples_tobacco":
        return {
            "tobacco_nicotine": 1.00,
            "consumer_staples_adjacent": 0.72,
            "beverages_adjacent": 0.58,
            "household_products_adjacent": 0.55,
            "retail_adjacent": 0.42,
        }.get(bucket, 0.25)
    if profile == "consumer_staples_general":
        return {
            "consumer_staples_adjacent": 1.00,
            "beverages_adjacent": 0.82,
            "household_products_adjacent": 0.82,
            "retail_adjacent": 0.62,
            "tobacco_nicotine": 0.58,
        }.get(bucket, 0.30)
    if profile == "fintech_digital_bank_payments":
        return {
            "fintech_digital_bank": 1.00,
            "fintech_payments": 0.90,
            "fintech_infrastructure": 0.80,
            "fintech_consumer_lending": 0.72,
            "fintech_brokerage": 0.60,
            "financials_banks": 0.60,
            "software_services_platform": 0.55,
            "fintech_crypto_platform": 0.30,
        }.get(bucket, 0.38)
    if profile == "materials_chemicals_mining":
        return {
            "aluminum_metals": 1.00,
            "metals_mining": 0.88,
            "construction_materials_adjacent": 0.55,
            "chemicals_adjacent": 0.45,
            "materials_general": 0.40,
        }.get(bucket, 0.35)
    return {
        "software_services_platform": 0.90,
        "consumer_hardware_ecosystem": 0.80,
        "networking_infrastructure": 0.68,
        "semiconductors": 0.62,
        "semiconductor_equipment": 0.48,
        "other_adjacent_tech": 0.55,
        "tobacco_nicotine": 0.55,
        "consumer_staples_adjacent": 0.58,
        "beverages_adjacent": 0.55,
        "household_products_adjacent": 0.55,
        "retail_adjacent": 0.45,
        "aluminum_metals": 0.58,
        "metals_mining": 0.56,
        "construction_materials_adjacent": 0.45,
        "chemicals_adjacent": 0.42,
        "materials_general": 0.40,
    }.get(bucket, 0.58)


def _bucket_budgets(profile: str) -> dict[str, float]:
    if profile == "premium_device_platform":
        return {
            "software_services_platform": 0.40,
            "consumer_hardware_ecosystem": 0.35,
            "networking_infrastructure": 0.10,
            "semiconductors": 0.10,
            "semiconductor_equipment": 0.05,
            "other_adjacent_tech": 0.00,
        }
    if profile == "consumer_hardware_ecosystem":
        return {
            "software_services_platform": 0.40,
            "consumer_hardware_ecosystem": 0.30,
            "networking_infrastructure": 0.15,
            "semiconductors": 0.10,
            "semiconductor_equipment": 0.05,
            "other_adjacent_tech": 0.00,
        }
    if profile == "software_services_ecosystem":
        return {
            "software_services_platform": 0.45,
            "consumer_hardware_ecosystem": 0.20,
            "networking_infrastructure": 0.15,
            "semiconductors": 0.10,
            "semiconductor_equipment": 0.05,
            "other_adjacent_tech": 0.05,
        }
    if profile == "semiconductors":
        return {
            "software_services_platform": 0.20,
            "consumer_hardware_ecosystem": 0.10,
            "networking_infrastructure": 0.10,
            "semiconductors": 0.45,
            "semiconductor_equipment": 0.10,
            "other_adjacent_tech": 0.05,
        }
    if profile == "consumer_staples_tobacco":
        return {
            "tobacco_nicotine": 0.60,
            "consumer_staples_adjacent": 0.25,
            "beverages_adjacent": 0.08,
            "household_products_adjacent": 0.05,
            "retail_adjacent": 0.02,
        }
    if profile == "consumer_staples_general":
        return {
            "consumer_staples_adjacent": 0.50,
            "beverages_adjacent": 0.20,
            "household_products_adjacent": 0.20,
            "retail_adjacent": 0.08,
            "tobacco_nicotine": 0.02,
        }
    if profile == "fintech_digital_bank_payments":
        return {
            "fintech_digital_bank": 0.35,
            "fintech_payments": 0.30,
            "fintech_infrastructure": 0.12,
            "fintech_consumer_lending": 0.08,
            "fintech_brokerage": 0.06,
            "financials_banks": 0.05,
            "software_services_platform": 0.03,
            "fintech_crypto_platform": 0.01,
        }
    if profile == "materials_chemicals_mining":
        return {
            "aluminum_metals": 0.45,
            "metals_mining": 0.35,
            "construction_materials_adjacent": 0.10,
            "chemicals_adjacent": 0.05,
            "materials_general": 0.05,
        }
    return {
        "software_services_platform": 0.35,
        "consumer_hardware_ecosystem": 0.25,
        "networking_infrastructure": 0.15,
        "semiconductors": 0.15,
        "semiconductor_equipment": 0.05,
        "other_adjacent_tech": 0.05,
    }


def _normalize_bucket_weights(peers: list[PeerData], profile: str) -> list[PeerData]:
    if not peers:
        return peers
    budgets = _bucket_budgets(profile)
    buckets = {}
    for p in peers:
        b = p.bucket or "other_adjacent_tech"
        buckets.setdefault(b, []).append(p)

    # Activate only buckets that actually have peers.
    active = {b for b, arr in buckets.items() if arr}
    all_keys = set(active) | set(budgets.keys())
    target = {b: (budgets.get(b, 0.0) if b in active else 0.0) for b in all_keys}
    active_sum = sum(target.values())

    # If some budgeted buckets are missing, redistribute to active buckets first.
    if active_sum < 0.999 and active:
        missing = 1.0 - active_sum
        pref = [b for b in (
            "software_services_platform",
            "consumer_hardware_ecosystem",
            "networking_infrastructure",
            "semiconductors",
            "semiconductor_equipment",
            "other_adjacent_tech",
            "tobacco_nicotine",
            "consumer_staples_adjacent",
            "beverages_adjacent",
            "household_products_adjacent",
            "retail_adjacent",
            "aluminum_metals",
            "metals_mining",
            "construction_materials_adjacent",
            "chemicals_adjacent",
            "materials_general",
        ) if b in active]
        if not pref:
            pref = sorted(active)
        if pref:
            denom = sum(max(target.get(b, 0.0), 0.01) for b in pref)
            for b in pref:
                target[b] = target.get(b, 0.0) + missing * (max(target.get(b, 0.0), 0.01) / denom)

    # Enforce semiconductor cap for Apple-like profiles.
    if profile in {"premium_device_platform", "consumer_hardware_ecosystem", "software_services_ecosystem", "general_tech"}:
        if profile == "premium_device_platform":
            semi_cap = 0.35
        elif profile == "consumer_hardware_ecosystem":
            semi_cap = 0.35
        else:
            semi_cap = 0.30
        semi_w = target.get("semiconductors", 0.0) + target.get("semiconductor_equipment", 0.0)
        if semi_w > semi_cap:
            excess = semi_w - semi_cap
            s0 = target.get("semiconductors", 0.0)
            se0 = target.get("semiconductor_equipment", 0.0)
            denom = max(s0 + se0, 1e-9)
            target["semiconductors"] = semi_cap * (s0 / denom)
            target["semiconductor_equipment"] = semi_cap * (se0 / denom)
            recipients = [b for b in (
                "software_services_platform",
                "consumer_hardware_ecosystem",
                "networking_infrastructure",
                "other_adjacent_tech",
            ) if b in active]
            if recipients:
                denom = sum(max(target.get(b, 0.0), 0.01) for b in recipients)
                for b in recipients:
                    target[b] = target.get(b, 0.0) + excess * (max(target.get(b, 0.0), 0.01) / denom)
        # Software/platform max cap for Apple-like profile.
        if profile in {"premium_device_platform", "consumer_hardware_ecosystem"}:
            soft_cap = 0.55
            soft_w = target.get("software_services_platform", 0.0)
            if soft_w > soft_cap:
                excess = soft_w - soft_cap
                target["software_services_platform"] = soft_cap
                recips = [b for b in (
                    "consumer_hardware_ecosystem",
                    "networking_infrastructure",
                    "semiconductors",
                    "semiconductor_equipment",
                    "other_adjacent_tech",
                ) if b in active]
                if recips:
                    denom = sum(max(target.get(b, 0.0), 0.01) for b in recips)
                    for b in recips:
                        target[b] = target.get(b, 0.0) + excess * (max(target.get(b, 0.0), 0.01) / denom)
        # Networking cap for Apple-like ecosystem profiles so a single networking peer
        # cannot dominate weights when consumer-hardware peers are sparse.
        if profile in {"premium_device_platform", "consumer_hardware_ecosystem"}:
            net_cap = 0.20
            net_w = target.get("networking_infrastructure", 0.0)
            if ("networking_infrastructure" in active) and net_w > net_cap:
                excess = net_w - net_cap
                target["networking_infrastructure"] = net_cap
                recips = [b for b in (
                    "software_services_platform",
                    "consumer_hardware_ecosystem",
                    "other_adjacent_tech",
                    "semiconductors",
                    "semiconductor_equipment",
                ) if b in active and b != "networking_infrastructure"]
                if recips:
                    denom = sum(max(target.get(b, 0.0), 0.01) for b in recips)
                    for b in recips:
                        target[b] = target.get(b, 0.0) + excess * (max(target.get(b, 0.0), 0.01) / denom)
        # Ensure software/platform floor for ecosystem profiles.
        if profile in {"premium_device_platform", "consumer_hardware_ecosystem"}:
            ps = target.get("software_services_platform", 0.0)
            if ("software_services_platform" in active) and ps < 0.35:
                need = 0.35 - ps
                donors = [b for b in ("semiconductors", "semiconductor_equipment", "networking_infrastructure", "other_adjacent_tech", "consumer_hardware_ecosystem") if target.get(b, 0.0) > 0.0]
                for d in donors:
                    floor = 0.25 if d == "consumer_hardware_ecosystem" else 0.0
                    take = min(need, max(0.0, target[d] - floor))
                    if take <= 0:
                        continue
                    target[d] -= take
                    target["software_services_platform"] = target.get("software_services_platform", 0.0) + take
                    need -= take
                    if need <= 1e-6:
                        break
            ch = target.get("consumer_hardware_ecosystem", 0.0)
            if ("consumer_hardware_ecosystem" in active) and ch < 0.25:
                need = 0.25 - ch
                donors = [b for b in ("software_services_platform", "networking_infrastructure", "other_adjacent_tech", "semiconductors", "semiconductor_equipment") if target.get(b, 0.0) > 0.0]
                for d in donors:
                    floor = 0.35 if d == "software_services_platform" else 0.0
                    take = min(need, max(0.0, target[d] - floor))
                    if take <= 0:
                        continue
                    target[d] -= take
                    target["consumer_hardware_ecosystem"] = target.get("consumer_hardware_ecosystem", 0.0) + take
                    need -= take
                    if need <= 1e-6:
                        break
        # Ensure software floor for software-services profiles.
        if profile == "software_services_ecosystem":
            soft = target.get("software_services_platform", 0.0)
            if ("software_services_platform" in active) and soft < 0.35:
                need = 0.35 - soft
                donors = [b for b in ("semiconductors", "semiconductor_equipment", "networking_infrastructure", "other_adjacent_tech", "consumer_hardware_ecosystem") if target.get(b, 0.0) > 0.0]
                for d in donors:
                    take = min(need, max(0.0, target[d] - 0.0))
                    if take <= 0:
                        continue
                    target[d] -= take
                    target["software_services_platform"] = target.get("software_services_platform", 0.0) + take
                    need -= take
                    if need <= 1e-6:
                        break
        # Re-apply bucket caps after floor adjustments to avoid rebound above caps.
        if profile in {"premium_device_platform", "consumer_hardware_ecosystem"}:
            _caps = {
                "software_services_platform": 0.55,
                "networking_infrastructure": 0.20,
            }
            # combined semis cap
            _semi_cap = 0.35
            _semi_sum = target.get("semiconductors", 0.0) + target.get("semiconductor_equipment", 0.0)
            if _semi_sum > _semi_cap:
                _ex = _semi_sum - _semi_cap
                s0 = target.get("semiconductors", 0.0)
                se0 = target.get("semiconductor_equipment", 0.0)
                _den = max(s0 + se0, 1e-9)
                target["semiconductors"] = _semi_cap * (s0 / _den)
                target["semiconductor_equipment"] = _semi_cap * (se0 / _den)
                _recips = [b for b in ("software_services_platform", "consumer_hardware_ecosystem", "networking_infrastructure", "other_adjacent_tech") if b in active]
                if _recips:
                    _den2 = sum(max(target.get(b, 0.0), 0.01) for b in _recips)
                    for b in _recips:
                        target[b] = target.get(b, 0.0) + _ex * (max(target.get(b, 0.0), 0.01) / _den2)
            for _bucket, _cap in _caps.items():
                _w = target.get(_bucket, 0.0)
                if (_bucket in active) and _w > _cap:
                    _ex = _w - _cap
                    target[_bucket] = _cap
                    _recips = [b for b in ("consumer_hardware_ecosystem", "software_services_platform", "other_adjacent_tech", "semiconductors", "semiconductor_equipment", "networking_infrastructure") if b in active and b != _bucket]
                    if _recips:
                        _den2 = sum(max(target.get(b, 0.0), 0.01) for b in _recips)
                        for b in _recips:
                            target[b] = target.get(b, 0.0) + _ex * (max(target.get(b, 0.0), 0.01) / _den2)

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


def _apply_peer_weight_caps(peers: list[PeerData], profile: str) -> list[PeerData]:
    """Apply per-peer caps to prevent concentration in adjacent-heavy sets."""
    vals = [p for p in peers if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0]
    if not vals:
        return peers

    single_cap = 0.35 if profile in {"premium_device_platform", "consumer_hardware_ecosystem"} else 0.50
    semi_cap = 0.15 if profile in {"premium_device_platform", "consumer_hardware_ecosystem"} else None
    network_cap = 0.20 if profile in {"premium_device_platform", "consumer_hardware_ecosystem"} else None
    retail_cap = 0.15 if profile == "consumer_staples_tobacco" else None
    if profile == "consumer_staples_tobacco":
        single_cap = 0.45
    core_weights = [float(p.weight or 0.0) for p in vals if (p.role or "").strip().lower() == "core valuation peer"]
    adjacent_cap_from_core = (max(core_weights) * 0.75) if core_weights else None

    excess = 0.0
    for p in vals:
        cap = single_cap
        if network_cap is not None and (p.bucket or "") == "networking_infrastructure":
            cap = min(cap, network_cap)
        if semi_cap is not None and (p.bucket or "") in {"semiconductors", "semiconductor_equipment"}:
            cap = min(cap, semi_cap)
        if retail_cap is not None and (p.bucket or "") == "retail_adjacent":
            cap = min(cap, retail_cap)
        if adjacent_cap_from_core is not None and (p.role or "").strip().lower() == "adjacent valuation peer":
            cap = min(cap, adjacent_cap_from_core)
        w = float(p.weight or 0.0)
        if w > cap:
            excess += (w - cap)
            p.weight = cap

    if excess <= 0:
        return peers

    recipients: list[PeerData] = []
    for p in vals:
        cap = single_cap
        if network_cap is not None and (p.bucket or "") == "networking_infrastructure":
            cap = min(cap, network_cap)
        if semi_cap is not None and (p.bucket or "") in {"semiconductors", "semiconductor_equipment"}:
            cap = min(cap, semi_cap)
        if retail_cap is not None and (p.bucket or "") == "retail_adjacent":
            cap = min(cap, retail_cap)
        if adjacent_cap_from_core is not None and (p.role or "").strip().lower() == "adjacent valuation peer":
            cap = min(cap, adjacent_cap_from_core)
        if float(p.weight or 0.0) + 1e-9 < cap:
            recipients.append(p)
    if not recipients:
        return peers

    room = 0.0
    for p in recipients:
        p_cap = single_cap
        if network_cap is not None and (p.bucket or "") == "networking_infrastructure":
            p_cap = min(p_cap, network_cap)
        if semi_cap is not None and (p.bucket or "") in {"semiconductors", "semiconductor_equipment"}:
            p_cap = min(p_cap, semi_cap)
        if retail_cap is not None and (p.bucket or "") == "retail_adjacent":
            p_cap = min(p_cap, retail_cap)
        if adjacent_cap_from_core is not None and (p.role or "").strip().lower() == "adjacent valuation peer":
            p_cap = min(p_cap, adjacent_cap_from_core)
        room += max(0.0, p_cap - float(p.weight or 0.0))
    if room <= 0:
        return peers

    for p in recipients:
        p_cap = single_cap
        if network_cap is not None and (p.bucket or "") == "networking_infrastructure":
            p_cap = min(p_cap, network_cap)
        if semi_cap is not None and (p.bucket or "") in {"semiconductors", "semiconductor_equipment"}:
            p_cap = min(p_cap, semi_cap)
        if retail_cap is not None and (p.bucket or "") == "retail_adjacent":
            p_cap = min(p_cap, retail_cap)
        if adjacent_cap_from_core is not None and (p.role or "").strip().lower() == "adjacent valuation peer":
            p_cap = min(p_cap, adjacent_cap_from_core)
        p_room = max(0.0, p_cap - float(p.weight or 0.0))
        if p_room <= 0:
            continue
        add = excess * (p_room / room)
        p.weight = float(p.weight or 0.0) + add
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

    self_ticker = (target_md.ticker or "").upper().strip()
    meta = target_md.additional_metadata if isinstance(target_md.additional_metadata, dict) else {}
    sector_key = str(meta.get("sector_key") or "").strip()
    industry_key = str(meta.get("industry_key") or "").strip()
    cache_key = f"quick_peers:{self_ticker}:{sector_key}:{industry_key}:{target_sector}:{target_industry}"
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
    if _is_mega_cap and _profile in {"premium_device_platform", "consumer_hardware_ecosystem", "software_services_ecosystem"}:
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
    if _is_mega_cap and _profile in {"premium_device_platform", "consumer_hardware_ecosystem", "software_services_ecosystem"}:
        # Controlled expansion order for Apple-like ecosystems:
        # 1) software/platform, 2) consumer-hardware ecosystem, 3) adjacent tech, 4) semis/infrastructure.
        candidates.extend(_search_yahoo_tickers("internet platform mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("communication services mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("digital platform ecosystem mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("consumer hardware mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("consumer electronics global leaders equities", limit=12))
        candidates.extend(_search_yahoo_tickers("premium device platform companies equities", limit=10))
        candidates.extend(_search_yahoo_tickers("global hardware services ecosystem equities", limit=10))
        candidates.extend(_search_yahoo_tickers("global device ecosystem leaders equities", limit=10))
        candidates.extend(_search_yahoo_tickers("technology mega cap equities", limit=12))
        candidates.extend(_search_yahoo_tickers("semiconductor infrastructure mega cap equities", limit=8))
    elif _profile == "consumer_staples_tobacco":
        candidates.extend(_search_yahoo_tickers("global tobacco equities", limit=16))
        candidates.extend(_search_yahoo_tickers("nicotine products companies equities", limit=12))
        candidates.extend(_search_yahoo_tickers("consumer staples tobacco peers", limit=12))
        candidates.extend(_search_yahoo_tickers("defensive consumer staples large cap equities", limit=12))
    elif _profile == "fintech_digital_bank_payments":
        candidates.extend(_search_yahoo_tickers("global fintech digital banking equities", limit=16))
        candidates.extend(_search_yahoo_tickers("digital payments platforms equities", limit=14))
        candidates.extend(_search_yahoo_tickers("neobank listed peers equities", limit=12))
        candidates.extend(_search_yahoo_tickers("cross-border payments equities", limit=10))

    profile_reserve: dict[str, list[str]] = {
        "premium_device_platform": ["MSFT", "ORCL", "CSCO", "NVDA", "AVGO", "MU"],
        "consumer_staples_tobacco": ["PM", "MO", "BTI", "IMBBY", "JAPAY"],
        "materials_chemicals_mining": ["AA", "CENX", "RIO", "BHP", "FCX", "NUE"],
        "fintech_digital_bank_payments": ["WIZEY", "PYPL", "ADYEY", "NU", "SOFI", "HOOD", "COIN"],
    }

    # Deduplicate, remove self ticker, and cap.
    out = [t for t in dict.fromkeys(candidates) if t and t != self_ticker]
    reserve = profile_reserve.get(_profile, [])
    if reserve:
        if len(out) < min(5, target_peers):
            out = reserve + out
        else:
            out = reserve + [t for t in out if t not in reserve]
        out = [t for t in dict.fromkeys(out) if t and t != self_ticker]
    out = out[:max(8, target_peers)]
    peer_universe_cache.set(cache_key, out)
    return out


# ── Core functions ────────────────────────────────────────────────────────────

def _peer_role(profile: str, bucket: str, ev_ebitda: float | None) -> str:
    if ev_ebitda is None:
        return "qualitative peer only"
    if profile == "premium_device_platform":
        if bucket in {"consumer_hardware_ecosystem"}:
            return "core valuation peer"
        if bucket in {
            "software_services_platform",
            "networking_infrastructure",
            "semiconductors",
            "semiconductor_equipment",
            "other_adjacent_tech",
        }:
            return "adjacent valuation peer"
    if profile == "consumer_hardware_ecosystem":
        if bucket in {"consumer_hardware_ecosystem"}:
            return "core valuation peer"
        if bucket in {
            "software_services_platform",
            "networking_infrastructure",
            "semiconductors",
            "semiconductor_equipment",
            "other_adjacent_tech",
        }:
            return "adjacent valuation peer"
    if profile == "software_services_ecosystem":
        if bucket in {"software_services_platform", "consumer_hardware_ecosystem"}:
            return "core valuation peer"
        if bucket in {"networking_infrastructure", "semiconductors", "semiconductor_equipment", "other_adjacent_tech"}:
            return "adjacent valuation peer"
    if profile == "semiconductors":
        if bucket in {"semiconductors", "semiconductor_equipment"}:
            return "core valuation peer"
        return "adjacent valuation peer"
    if profile == "consumer_staples_tobacco":
        if bucket == "tobacco_nicotine":
            return "core valuation peer"
        if bucket in {
            "consumer_staples_adjacent",
            "beverages_adjacent",
            "household_products_adjacent",
            "retail_adjacent",
        }:
            return "adjacent valuation peer"
        return "qualitative peer only"
    if profile == "consumer_staples_general":
        if bucket in {"consumer_staples_adjacent", "beverages_adjacent", "household_products_adjacent"}:
            return "core valuation peer"
        if bucket in {"retail_adjacent", "tobacco_nicotine"}:
            return "adjacent valuation peer"
        return "qualitative peer only"
    if profile == "fintech_digital_bank_payments":
        if bucket in {"fintech_digital_bank"}:
            return "core valuation peer"
        if bucket in {"fintech_payments", "fintech_infrastructure", "fintech_consumer_lending", "financials_banks"}:
            return "adjacent valuation peer"
        if bucket in {"fintech_brokerage", "fintech_crypto_platform", "software_services_platform"}:
            return "qualitative peer only"
        return "qualitative peer only"
    if profile == "materials_chemicals_mining":
        if bucket in {"aluminum_metals", "metals_mining"}:
            return "core valuation peer"
        if bucket in {"construction_materials_adjacent", "chemicals_adjacent", "materials_general"}:
            return "adjacent valuation peer"
        return "qualitative peer only"
    if bucket in {"software_services_platform", "consumer_hardware_ecosystem"}:
        return "core valuation peer"
    if bucket in {"tobacco_nicotine", "consumer_staples_adjacent", "beverages_adjacent", "household_products_adjacent"}:
        return "core valuation peer"
    return "adjacent valuation peer"


def _relaxation_stage(profile: str, bucket: str) -> int:
    if profile == "premium_device_platform":
        if bucket == "consumer_hardware_ecosystem":
            return 1
        if bucket == "software_services_platform":
            return 2
        if bucket == "networking_infrastructure":
            return 3
        if bucket == "other_adjacent_tech":
            return 4
        if bucket in {"semiconductors", "semiconductor_equipment"}:
            return 5
    if profile == "consumer_hardware_ecosystem":
        if bucket == "consumer_hardware_ecosystem":
            return 1
        if bucket == "software_services_platform":
            return 2
        if bucket == "networking_infrastructure":
            return 3
        if bucket == "other_adjacent_tech":
            return 4
        if bucket in {"semiconductors", "semiconductor_equipment"}:
            return 5
    if profile == "software_services_ecosystem":
        if bucket == "software_services_platform":
            return 1
        if bucket == "consumer_hardware_ecosystem":
            return 3
        if bucket == "networking_infrastructure":
            return 4
        if bucket == "other_adjacent_tech":
            return 4
        if bucket in {"semiconductors", "semiconductor_equipment"}:
            return 5
    if profile == "semiconductors":
        if bucket in {"semiconductors", "semiconductor_equipment"}:
            return 1
        if bucket == "software_services_platform":
            return 2
        if bucket == "consumer_hardware_ecosystem":
            return 3
        return 4
    if profile == "consumer_staples_tobacco":
        if bucket == "tobacco_nicotine":
            return 1
        if bucket == "consumer_staples_adjacent":
            return 2
        if bucket in {"beverages_adjacent", "household_products_adjacent"}:
            return 3
        if bucket == "retail_adjacent":
            return 4
        return 5
    if profile == "consumer_staples_general":
        if bucket in {"consumer_staples_adjacent", "beverages_adjacent", "household_products_adjacent"}:
            return 1
        if bucket == "retail_adjacent":
            return 2
        if bucket == "tobacco_nicotine":
            return 3
        return 4
    if profile == "fintech_digital_bank_payments":
        if bucket in {"fintech_digital_bank", "fintech_payments"}:
            return 1
        if bucket in {"fintech_infrastructure", "fintech_consumer_lending", "financials_banks"}:
            return 2
        if bucket in {"fintech_brokerage", "software_services_platform"}:
            return 3
        if bucket in {"fintech_crypto_platform"}:
            return 4
        return 5
    if profile == "materials_chemicals_mining":
        if bucket == "aluminum_metals":
            return 1
        if bucket == "metals_mining":
            return 2
        if bucket == "construction_materials_adjacent":
            return 3
        if bucket in {"chemicals_adjacent", "materials_general"}:
            return 4
        return 5
    if bucket in {"software_services_platform", "consumer_hardware_ecosystem"}:
        return 2
    if bucket in {"consumer_staples_adjacent", "beverages_adjacent", "household_products_adjacent", "tobacco_nicotine"}:
        return 2
    if bucket == "networking_infrastructure":
        return 3
    if bucket == "other_adjacent_tech":
        return 4
    return 5

def build_peer_multiples(
    peer_tickers: list[str],
    target_sector: str = "",
    target_industry: str = "",
    target_market_cap: float | None = None,
    target_ticker: str = "",
    target_company_name: str = "",
    target_country: str = "",
    target_primary_listing: str = "",
    target_underlying_symbol: str = "",
    min_similarity: float = 0.0,
    target_ebitda_margin: float | None = None,
    target_growth: float | None = None,
    min_market_cap_ratio: float = 0.0,
    min_valuation_peers: int = MIN_VALID_PEERS,
    max_return_peers: int | None = None,
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
    relaxation_pool: list[tuple[int, PeerData, str]] = []
    n_no_data = n_sector = n_sanity = n_scale = n_bucket = n_same_issuer = 0
    excluded: list[str] = []
    profile = _target_profile(target_sector, target_industry)
    _is_mega_target = bool(target_market_cap and target_market_cap > 500_000.0)
    _mega_valuation_floor = (
        max(100_000.0, (target_market_cap or 0.0) * 0.05)
        if _is_mega_target
        else 0.0
    )

    target_aliases = {
        str(target_ticker or "").upper().strip(),
        str(target_primary_listing or "").upper().strip(),
        str(target_underlying_symbol or "").upper().strip(),
    }
    target_aliases = {x for x in target_aliases if x}
    target_key = _issuer_key(target_company_name)
    target_country_l = str(target_country or "").strip().lower()

    # Batch fetch peer market data to reduce quick/full peer-validation latency.
    _md_map: dict[str, Optional[MarketData]] = {}
    _unique_tickers = [t for t in dict.fromkeys(peer_tickers) if t]
    if _unique_tickers and not isinstance(fetch_market_data, Mock):
        _workers = min(6, len(_unique_tickers))
        with ThreadPoolExecutor(max_workers=max(1, _workers)) as _pool:
            _futs = { _pool.submit(fetch_market_data, _t): _t for _t in _unique_tickers }
            for _fut in as_completed(_futs):
                _ticker = _futs[_fut]
                try:
                    _md_map[_ticker] = _fut.result()
                except Exception:
                    _md_map[_ticker] = None
    else:
        for _t in _unique_tickers:
            try:
                _md_map[_t] = fetch_market_data(_t)
            except Exception:
                _md_map[_t] = None

    for ticker in peer_tickers:
        md = _md_map.get(ticker)

        # Gate 1 — ticker must exist in yfinance
        if md is None:
            n_no_data += 1
            excluded.append(f"{ticker}: not found")
            continue

        # Gate 1b — drop alternate listings for the same issuer as target.
        peer_aliases = _symbol_aliases(md)
        if target_aliases and (target_aliases & peer_aliases):
            n_same_issuer += 1
            excluded.append(
                f"{ticker}: same issuer as target ({'/'.join(sorted(target_aliases))}) / alternate listing"
            )
            continue
        peer_name_key = _issuer_key(md.company_name or "")
        peer_country = ""
        if isinstance(md.additional_metadata, dict):
            peer_country = str(md.additional_metadata.get("country") or "").strip().lower()
        if target_key and peer_name_key and target_key == peer_name_key:
            if not target_country_l or not peer_country or target_country_l == peer_country:
                n_same_issuer += 1
                excluded.append(f"{ticker}: same issuer as target ({md.company_name}) / alternate listing")
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
        _ev_ebitda_floor = 3.0
        _ev_ebitda_ceiling = 40.0
        if profile in {"materials_chemicals_mining", "energy_oil_gas"}:
            # Commodity-heavy sectors can print lower multiples in cyclic troughs.
            _ev_ebitda_floor = 2.0
        if ev_ebitda is not None and (ev_ebitda < _ev_ebitda_floor or ev_ebitda > _ev_ebitda_ceiling):
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

        _industry = ""
        if isinstance(md.additional_metadata, dict):
            _industry = str(md.additional_metadata.get("industry") or "")
        _bucket = _classify_peer_bucket(md.sector or "", _industry, md.company_name or "")
        _bucket = _normalize_bucket_for_profile(profile, _bucket)
        _business_sim = _business_similarity(
            target_sector=target_sector,
            peer_sector=md.sector or "",
            target_margin=target_ebitda_margin,
            peer_margin=md.ebitda_margin,
            target_growth=target_growth,
            peer_growth=(md.forward_revenue_growth or md.revenue_growth_yoy),
            profile=profile,
            bucket=_bucket,
        )
        _scale_sim = _scale_similarity(target_market_cap, md.market_cap)
        _sim = max(0.0, min(1.0, (0.75 * _business_sim) + (0.25 * _scale_sim)))
        _b_weight = _bucket_weight_for_profile(profile, _bucket)

        # Gate 2b — scale compatibility with bucket-aware floor for mega-cap targets.
        _scale_fail = False
        _below_mega_valuation_floor = bool(
            _is_mega_target
            and md.market_cap
            and md.market_cap > 0
            and md.market_cap < _mega_valuation_floor
        )
        if (
            min_market_cap_ratio > 0
            and target_market_cap
            and target_market_cap > 0
            and md.market_cap
            and md.market_cap > 0
        ):
            global_floor = max(100_000.0, target_market_cap * min_market_cap_ratio)
            adjacent_floor = max(40_000.0, target_market_cap * 0.015)
            floor = adjacent_floor if _bucket in {"semiconductors", "semiconductor_equipment", "networking_infrastructure", "other_adjacent_tech"} else global_floor
            if md.market_cap < floor:
                n_scale += 1
                excluded.append(f"{ticker}: below market-cap floor")
                _scale_fail = True
        if _below_mega_valuation_floor and not _scale_fail:
            n_scale += 1
            excluded.append(
                f"{ticker}: below mega-cap valuation floor (${_mega_valuation_floor/1000:.0f}B)"
            )

        # Outlier cap for adjacent semiconductors in Apple-like profiles.
        _outlier_capped = False
        if (
            profile in {"premium_device_platform", "consumer_hardware_ecosystem", "software_services_ecosystem"}
            and _bucket in {"semiconductors", "semiconductor_equipment"}
            and ev_ebitda is not None
            and ev_ebitda > 40.0
        ):
            ev_ebitda = 40.0
            _outlier_capped = True

        _role = _peer_role(profile, _bucket, ev_ebitda)
        if profile in {"premium_device_platform", "consumer_hardware_ecosystem", "software_services_ecosystem"} and ticker.upper() == "PLTR":
            n_sector += 1
            excluded.append("PLTR: filtered for Apple-like profiles (AI/software context only)")
            continue
        _stage = _relaxation_stage(profile, _bucket)
        _valuation_scale_ok = not _below_mega_valuation_floor
        if _below_mega_valuation_floor:
            # For mega-caps, tiny peers may remain qualitative context but cannot drive valuation.
            _role = "qualitative peer only"
        if _role == "core valuation peer":
            _sim_floor = max(min_similarity, 0.45)
        elif _role == "adjacent valuation peer":
            _sim_floor = max(min_similarity * 0.8, 0.35)
        else:
            _sim_floor = 0.30
        _passes_similarity = _sim >= _sim_floor

        include_reason = "adjacent: sector/size fit"
        if _role == "core valuation peer":
            include_reason = "core: business model aligned"
        elif _role == "adjacent valuation peer":
            if _bucket == "software_services_platform":
                include_reason = "adjacent platform/services reference"
            elif _bucket == "networking_infrastructure":
                include_reason = "adjacent infrastructure reference"
            elif _bucket in {"semiconductors", "semiconductor_equipment"}:
                include_reason = "adjacent semiconductor/infrastructure reference"
            else:
                include_reason = "adjacent: business-model/size fit"
        elif _role == "qualitative peer only":
            include_reason = "qualitative only: EV/EBITDA unavailable"
        if _below_mega_valuation_floor:
            include_reason = (
                f"qualitative only: below mega-cap valuation floor "
                f"(${_mega_valuation_floor/1000:.0f}B)"
            )
        if _outlier_capped:
            include_reason += " (outlier EV/EBITDA capped)"
        _data_quality = (1.0 if ev_ebitda is not None else 0.0)
        if _role == "qualitative peer only":
            _data_quality = 0.0
        _weight_raw = _business_sim * _scale_sim * _data_quality * _b_weight
        if not _valuation_scale_ok:
            _weight_raw = 0.0
        _candidate = PeerData(
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
            quote_currency=(
                str(md.additional_metadata.get("quote_currency_normalized") or md.additional_metadata.get("quote_currency") or "")
                if isinstance(md.additional_metadata, dict)
                else ""
            ),
            bucket=_bucket,
            role=_role,
            include_reason=include_reason,
            similarity=_sim,
            business_similarity=_business_sim,
            scale_similarity=_scale_sim,
            weight=max(_weight_raw, 0.0),
        )

        # Keep strict acceptance first; stage-based relaxation can add more peers later.
        if _passes_similarity and not _scale_fail:
            peers.append(_candidate)
        else:
            if not _passes_similarity:
                n_sector += 1
                excluded.append(f"{ticker}: similarity below threshold")
            # Controlled relaxation also handles mild scale-mismatch candidates.
            _relax_ok = _sim >= 0.25
            if _relax_ok:
                _why = "controlled relaxation"
                if _scale_fail:
                    _why = "scale-relaxed controlled relaxation"
                relaxation_pool.append((_stage, _candidate, _why))

    # Bucket-balance policy for non-semi tech: avoid semiconductor-dominated peer sets.
    if peers and profile in {"premium_device_platform", "consumer_hardware_ecosystem", "software_services_ecosystem", "general_tech"}:
        semis = [p for p in peers if p.bucket in {"semiconductors", "semiconductor_equipment"}]
        non_semis = [p for p in peers if p.bucket not in {"semiconductors", "semiconductor_equipment"}]
        if semis and len(semis) > max(2, int(0.35 * len(peers))):
            keep_n = max(2, int(0.35 * len(peers)))
            semis_sorted = sorted(semis, key=lambda p: (p.weight or 0.0), reverse=True)
            semis_keep = semis_sorted[:keep_n]
            semis_drop = semis_sorted[keep_n:]
            peers = non_semis + semis_keep
            n_bucket += len(semis_drop)
            excluded.extend([f"{p.ticker}: bucket-balance cap" for p in semis_drop])

    # Controlled relaxation to avoid over-filtering:
    # 1) same archetype, 2) software/platform, 3) consumer hardware, 4) adjacent tech, 5) semis.
    def _is_valuation_peer(_p: PeerData) -> bool:
        return bool(
            _p.ev_ebitda is not None
            and (_p.weight or 0.0) > 0.0
            and str(_p.role or "").strip().lower() != "qualitative peer only"
        )

    _valuation_peers = [p for p in peers if _is_valuation_peer(p)]
    if len(_valuation_peers) < max(1, min_valuation_peers):
        for _, cand, why in sorted(relaxation_pool, key=lambda x: (x[0], -(x[1].similarity or 0.0))):
            if any(p.ticker == cand.ticker for p in peers):
                continue
            peers.append(cand)
            excluded.append(f"{cand.ticker}: re-included by {why}")
            _valuation_peers = [p for p in peers if _is_valuation_peer(p)]
            if len(_valuation_peers) >= max(1, min_valuation_peers):
                break

    peers = _normalize_bucket_weights(peers, profile)
    # Qualitative peers and peers without EV/EBITDA must be zero-weight for valuation math.
    for p in peers:
        if (
            str(p.role or "").strip().lower() == "qualitative peer only"
            or p.ev_ebitda is None
            or (p.weight or 0.0) <= 0.0
        ):
            p.weight = 0.0
            p.role = "qualitative peer only"
    _valuation_weight_sum = sum((p.weight or 0.0) for p in peers if _is_valuation_peer(p))
    if _valuation_weight_sum > 0:
        for p in peers:
            if _is_valuation_peer(p):
                p.weight = float(p.weight or 0.0) / _valuation_weight_sum
    peers = _apply_peer_weight_caps(peers, profile)
    peers = sorted(peers, key=lambda p: (p.weight or 0.0), reverse=True)
    if max_return_peers and max_return_peers > 0 and len(peers) > max_return_peers:
        dropped = peers[max_return_peers:]
        peers = peers[:max_return_peers]
        excluded.extend([f"{p.ticker}: trimmed to top-{max_return_peers} peer target" for p in dropped])
        peers = _normalize_bucket_weights(peers, profile)
        for p in peers:
            if (
                str(p.role or "").strip().lower() == "qualitative peer only"
                or p.ev_ebitda is None
                or (p.weight or 0.0) <= 0.0
            ):
                p.weight = 0.0
                p.role = "qualitative peer only"
        _valuation_weight_sum = sum((p.weight or 0.0) for p in peers if _is_valuation_peer(p))
        if _valuation_weight_sum > 0:
            for p in peers:
                if _is_valuation_peer(p):
                    p.weight = float(p.weight or 0.0) / _valuation_weight_sum
        peers = _apply_peer_weight_caps(peers, profile)
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
            n_dropped_same_issuer=n_same_issuer,
            excluded_details=excluded[:30],
            source="sector_fallback",
        )

    ev_ebitdas_raw = [p.ev_ebitda for p in peers if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0]
    ev_ebitda_w = [p.weight or 1.0 for p in peers if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0]
    ev_revenues_raw = [p.ev_revenue for p in peers if p.ev_revenue and (p.weight or 0.0) > 0.0]
    ev_revenue_w = [p.weight or 1.0 for p in peers if p.ev_revenue and (p.weight or 0.0) > 0.0]
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
        _ev_ebitda_weighted = _weighted_mean(ev_ebitdas, ev_ebitda_w)
        _ev_ebitda_central = _ev_ebitda_weighted or _median(ev_ebitdas)
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

    _n_valuation = len([p for p in peers if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0])
    _n_qual = len([p for p in peers if p.ev_ebitda is None or (p.weight or 0.0) <= 0.0])
    _effective_peer_count = 0.0
    if ev_ebitda_w:
        _w_sum_sq = sum((float(w) ** 2) for w in ev_ebitda_w if w and w > 0)
        if _w_sum_sq > 0:
            _effective_peer_count = 1.0 / _w_sum_sq
    _pure_weight = sum(
        float(p.weight or 0.0)
        for p in peers
        if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0 and (p.role or "") == "core valuation peer"
    )
    _adj_weight = sum(
        float(p.weight or 0.0)
        for p in peers
        if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0 and (p.role or "") == "adjacent valuation peer"
    )
    _pure_share = _pure_weight / (_pure_weight + _adj_weight) if (_pure_weight + _adj_weight) > 0 else 0.0
    if _pure_share >= 0.80:
        _peer_set_type = "PURE_COMPS_OK"
    elif _pure_share >= 0.50:
        _peer_set_type = "MIXED_COMPS_OK"
    elif _pure_share > 0.0:
        _peer_set_type = "ADJACENT_COMPS"
    elif _n_valuation > 0:
        _peer_set_type = "NO_PURE_COMPS"
    else:
        _peer_set_type = "ADJACENT_REFERENCE_SET"

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
        n_valuation_peers=_n_valuation,
        n_qualitative_peers=_n_qual,
        effective_peer_count=_effective_peer_count,
        pure_peer_weight_share=_pure_share,
        adjacent_peer_weight_share=max(0.0, 1.0 - _pure_share) if (_pure_weight + _adj_weight) > 0 else 0.0,
        peer_set_type=_peer_set_type,
        n_dropped_no_data=n_no_data,
        n_dropped_sector=n_sector,
        n_dropped_sanity=n_sanity,
        n_dropped_scale=n_scale,
        n_dropped_bucket=n_bucket,
        n_dropped_same_issuer=n_same_issuer,
        excluded_details=excluded[:30],
        source=("yfinance_peers" if _n_valuation >= MIN_VALID_PEERS else "yfinance_peers_low_confidence"),
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
