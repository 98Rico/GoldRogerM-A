"""
Transaction comparables — real M&A deal multiples.

Stores deal data extracted by TransactionCompsAgent (web search → structured JSON).
Multiples are validated with the same sanity gates used for peer comps.

Cache: goldroger/data/transaction_comps_cache.json  (append-only, auto-deduplicated)

Usage:
    comps = load_cache()
    medians = sector_medians(comps, sector="software", min_year=2021)
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "transaction_comps_cache.json")

# Sanity bounds — same philosophy as peer comps
_EV_EBITDA_MIN, _EV_EBITDA_MAX = 2.0, 60.0
_EV_REV_MIN, _EV_REV_MAX = 0.1, 30.0
_MIN_EV_M = 5.0          # ignore sub-$5M deals (noise)


@dataclass
class TransactionComp:
    target: str
    acquirer: str
    sector: str
    year: int
    ev_m: float                      # Enterprise Value in USD millions
    revenue_m: Optional[float]
    ebitda_m: Optional[float]
    ev_ebitda: Optional[float]       # computed or stated
    ev_revenue: Optional[float]      # computed or stated
    source: str                      # URL or description


# ── Persistence ───────────────────────────────────────────────────────────────

def load_cache() -> list[TransactionComp]:
    if not os.path.exists(_CACHE_PATH):
        return []
    try:
        with open(_CACHE_PATH) as f:
            raw = json.load(f)
        return [TransactionComp(**r) for r in raw]
    except Exception:
        return []


def save_cache(comps: list[TransactionComp]) -> None:
    try:
        with open(_CACHE_PATH, "w") as f:
            json.dump([asdict(c) for c in comps], f, indent=2)
    except Exception:
        pass


def add_comps(new_comps: list[TransactionComp]) -> list[TransactionComp]:
    """Merge new_comps into cache, deduplicating by (target, year). Returns merged list."""
    existing = load_cache()
    keys = {(c.target.lower(), c.year) for c in existing}
    for c in new_comps:
        if (c.target.lower(), c.year) not in keys:
            existing.append(c)
            keys.add((c.target.lower(), c.year))
    save_cache(existing)
    return existing


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(c: TransactionComp) -> bool:
    if c.ev_m < _MIN_EV_M:
        return False
    if c.ev_ebitda is not None and not (_EV_EBITDA_MIN <= c.ev_ebitda <= _EV_EBITDA_MAX):
        return False
    if c.ev_revenue is not None and not (_EV_REV_MIN <= c.ev_revenue <= _EV_REV_MAX):
        return False
    return True


# ── Aggregation ───────────────────────────────────────────────────────────────

def _median(values: list[float]) -> Optional[float]:
    clean = sorted(v for v in values if v and v > 0)
    if not clean:
        return None
    mid = len(clean) // 2
    return (clean[mid - 1] + clean[mid]) / 2 if len(clean) % 2 == 0 else clean[mid]


def _sector_canonical(sector: str) -> str:
    """Resolve sector to canonical name via sector_multiples aliases."""
    from goldroger.data.sector_multiples import _ALIASES  # type: ignore[attr-defined]
    return _ALIASES.get(sector.strip().lower(), sector.strip().lower())


def sector_medians(
    comps: list[TransactionComp],
    sector: str,
    min_year: int = 2020,
) -> dict:
    """
    Return median deal multiples for a sector from the cache.

    Sector matching is broad: substring, word overlap, or alias resolution
    so "SaaS", "enterprise software", and "software" all match each other.
    """
    sector_l = sector.lower()
    sector_canonical = _sector_canonical(sector)
    sector_words = set(sector_l.split())

    def _matches(c: TransactionComp) -> bool:
        cl = c.sector.lower()
        if sector_l in cl or cl in sector_l:
            return True
        if sector_words & set(cl.split()):
            return True
        # Alias resolution: both map to same canonical sector
        return _sector_canonical(c.sector) == sector_canonical

    filtered = [
        c for c in comps
        if c.year >= min_year and _matches(c) and _validate(c)
    ]

    ev_ebitdas = [c.ev_ebitda for c in filtered if c.ev_ebitda]
    ev_revenues = [c.ev_revenue for c in filtered if c.ev_revenue]

    return {
        "n_deals": len(filtered),
        "ev_ebitda_median": _median(ev_ebitdas),
        "ev_revenue_median": _median(ev_revenues),
        "deals": [
            {"target": c.target, "acquirer": c.acquirer, "year": c.year,
             "ev_m": c.ev_m, "ev_ebitda": c.ev_ebitda, "ev_revenue": c.ev_revenue}
            for c in filtered[-6:]  # most recent 6
        ],
    }


# ── Parser — LLM agent output → TransactionComp list ─────────────────────────

def parse_agent_output(raw: str, sector: str) -> list[TransactionComp]:
    """Parse JSON output from TransactionCompsAgent."""
    try:
        data = json.loads(raw)
        deals = data if isinstance(data, list) else data.get("deals", [])
    except Exception:
        return []

    result = []
    for d in deals:
        try:
            ev_m = float(d.get("ev_m") or d.get("ev_usd_m") or 0)
            if ev_m < _MIN_EV_M:
                continue

            revenue_m = _safe_float(d.get("revenue_m"))
            ebitda_m = _safe_float(d.get("ebitda_m"))

            # Compute multiples if not stated
            ev_ebitda = _safe_float(d.get("ev_ebitda"))
            if ev_ebitda is None and ebitda_m and ebitda_m > 0:
                ev_ebitda = round(ev_m / ebitda_m, 1)

            ev_revenue = _safe_float(d.get("ev_revenue"))
            if ev_revenue is None and revenue_m and revenue_m > 0:
                ev_revenue = round(ev_m / revenue_m, 1)

            year = int(d.get("year") or 0)
            if year < 2010:
                continue

            comp = TransactionComp(
                target=str(d.get("target", "Unknown")),
                acquirer=str(d.get("acquirer", "Unknown")),
                sector=str(d.get("sector") or sector),
                year=year,
                ev_m=ev_m,
                revenue_m=revenue_m,
                ebitda_m=ebitda_m,
                ev_ebitda=ev_ebitda,
                ev_revenue=ev_revenue,
                source=str(d.get("source", "")),
            )
            if _validate(comp):
                result.append(comp)
        except Exception:
            continue
    return result


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
