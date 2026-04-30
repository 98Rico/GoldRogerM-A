"""
Sector-calibrated valuation multiple tables.

Ranges reflect approximate market consensus as of 2024-2025 based on
Damodaran sector data, FactSet, and Bloomberg aggregates.

EV-based multiples (default path):
  - ev_ebitda: (low, mid, high)
  - ev_revenue: (low, mid, high)

Equity-based multiples (financial companies — banks, insurers, asset managers):
  - pe_range: (low, mid, high) — Price/Earnings
  - pb_range: (low, mid, high) — Price/Book

valuation_method:
  - "ev_ebitda" → standard DCF + EV/EBITDA comps path
  - "pe_pb"     → P/E and P/B path (financial companies)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SectorMultiples:
    ev_ebitda: tuple[float, float, float]    # low, mid, high
    ev_revenue: tuple[float, float, float]   # low, mid, high
    terminal_growth: float                   # decimal, e.g. 0.025
    sector_beta: float                       # reference beta
    sector_wacc: float                       # fallback WACC (decimal)
    valuation_method: str = "ev_ebitda"      # "ev_ebitda" or "pe_pb"
    pe_range: Optional[tuple[float, float, float]] = None   # P/E low/mid/high
    pb_range: Optional[tuple[float, float, float]] = None   # P/B low/mid/high


_MULTIPLES: dict[str, SectorMultiples] = {
    "technology": SectorMultiples(
        ev_ebitda=(15.0, 22.0, 35.0),
        ev_revenue=(4.0, 7.0, 15.0),
        terminal_growth=0.025,
        sector_beta=1.20,
        sector_wacc=0.105,
    ),
    "software": SectorMultiples(
        ev_ebitda=(20.0, 30.0, 50.0),
        ev_revenue=(8.0, 12.0, 20.0),
        terminal_growth=0.025,
        sector_beta=1.30,
        sector_wacc=0.110,
    ),
    "semiconductors": SectorMultiples(
        ev_ebitda=(14.0, 20.0, 30.0),
        ev_revenue=(4.0, 6.0, 10.0),
        terminal_growth=0.025,
        sector_beta=1.25,
        sector_wacc=0.108,
    ),
    "healthcare": SectorMultiples(
        ev_ebitda=(12.0, 16.0, 22.0),
        ev_revenue=(2.0, 4.0, 7.0),
        terminal_growth=0.020,
        sector_beta=0.80,
        sector_wacc=0.085,
    ),
    "biotechnology": SectorMultiples(
        ev_ebitda=(18.0, 25.0, 40.0),
        ev_revenue=(5.0, 9.0, 18.0),
        terminal_growth=0.020,
        sector_beta=1.10,
        sector_wacc=0.100,
    ),
    "pharmaceuticals": SectorMultiples(
        ev_ebitda=(10.0, 14.0, 20.0),
        ev_revenue=(3.0, 5.0, 8.0),
        terminal_growth=0.020,
        sector_beta=0.75,
        sector_wacc=0.082,
    ),
    "consumer staples": SectorMultiples(
        ev_ebitda=(10.0, 14.0, 18.0),
        ev_revenue=(1.5, 2.5, 4.0),
        terminal_growth=0.015,
        sector_beta=0.60,
        sector_wacc=0.075,
    ),
    "consumer discretionary": SectorMultiples(
        ev_ebitda=(8.0, 12.0, 18.0),
        ev_revenue=(1.0, 2.0, 3.5),
        terminal_growth=0.020,
        sector_beta=1.00,
        sector_wacc=0.095,
    ),
    "retail": SectorMultiples(
        ev_ebitda=(7.0, 10.0, 15.0),
        ev_revenue=(0.5, 1.0, 2.0),
        terminal_growth=0.015,
        sector_beta=0.90,
        sector_wacc=0.090,
    ),
    "industrials": SectorMultiples(
        ev_ebitda=(8.0, 11.0, 15.0),
        ev_revenue=(1.0, 1.5, 2.5),
        terminal_growth=0.020,
        sector_beta=1.00,
        sector_wacc=0.095,
    ),
    "aerospace": SectorMultiples(
        ev_ebitda=(9.0, 12.0, 16.0),
        ev_revenue=(1.2, 1.8, 2.8),
        terminal_growth=0.020,
        sector_beta=1.05,
        sector_wacc=0.097,
    ),
    "energy": SectorMultiples(
        ev_ebitda=(4.0, 7.0, 10.0),
        ev_revenue=(0.5, 1.0, 1.8),
        terminal_growth=0.010,
        sector_beta=1.10,
        sector_wacc=0.100,
    ),
    "utilities": SectorMultiples(
        ev_ebitda=(8.0, 11.0, 14.0),
        ev_revenue=(2.0, 3.0, 4.0),
        terminal_growth=0.010,
        sector_beta=0.40,
        sector_wacc=0.070,
    ),
    # ── Financial companies — P/E + P/B path ──────────────────────────────
    "financials": SectorMultiples(
        ev_ebitda=(8.0, 11.0, 15.0),
        ev_revenue=(2.0, 3.0, 5.0),
        terminal_growth=0.020,
        sector_beta=1.00,
        sector_wacc=0.095,
        valuation_method="pe_pb",
        pe_range=(10.0, 13.0, 17.0),
        pb_range=(1.0, 1.4, 2.0),
    ),
    "banking": SectorMultiples(
        ev_ebitda=(7.0, 10.0, 14.0),
        ev_revenue=(2.0, 3.5, 5.0),
        terminal_growth=0.020,
        sector_beta=0.95,
        sector_wacc=0.093,
        valuation_method="pe_pb",
        pe_range=(9.0, 12.0, 16.0),
        pb_range=(0.8, 1.2, 1.8),
    ),
    "insurance": SectorMultiples(
        ev_ebitda=(8.0, 11.0, 15.0),
        ev_revenue=(1.5, 2.5, 4.0),
        terminal_growth=0.020,
        sector_beta=0.85,
        sector_wacc=0.088,
        valuation_method="pe_pb",
        pe_range=(10.0, 13.0, 18.0),
        pb_range=(1.0, 1.5, 2.2),
    ),
    "asset management": SectorMultiples(
        ev_ebitda=(10.0, 14.0, 20.0),
        ev_revenue=(3.0, 5.0, 8.0),
        terminal_growth=0.020,
        sector_beta=1.10,
        sector_wacc=0.100,
        valuation_method="pe_pb",
        pe_range=(12.0, 16.0, 22.0),
        pb_range=(2.0, 3.0, 5.0),
    ),
    # ─────────────────────────────────────────────────────────────────────
    "real estate": SectorMultiples(
        ev_ebitda=(15.0, 20.0, 25.0),
        ev_revenue=(5.0, 8.0, 12.0),
        terminal_growth=0.020,
        sector_beta=0.70,
        sector_wacc=0.080,
    ),
    "materials": SectorMultiples(
        ev_ebitda=(7.0, 10.0, 14.0),
        ev_revenue=(1.0, 1.5, 2.5),
        terminal_growth=0.015,
        sector_beta=1.00,
        sector_wacc=0.095,
    ),
    "telecom": SectorMultiples(
        ev_ebitda=(6.0, 9.0, 12.0),
        ev_revenue=(1.5, 2.5, 3.5),
        terminal_growth=0.015,
        sector_beta=0.70,
        sector_wacc=0.080,
    ),
    "communication services": SectorMultiples(
        ev_ebitda=(8.0, 13.0, 20.0),
        ev_revenue=(2.0, 4.0, 8.0),
        terminal_growth=0.020,
        sector_beta=0.85,
        sector_wacc=0.088,
    ),
    "media": SectorMultiples(
        ev_ebitda=(8.0, 12.0, 18.0),
        ev_revenue=(1.5, 2.5, 4.0),
        terminal_growth=0.015,
        sector_beta=0.90,
        sector_wacc=0.090,
    ),
    "e-commerce": SectorMultiples(
        ev_ebitda=(18.0, 28.0, 45.0),
        ev_revenue=(3.0, 5.0, 10.0),
        terminal_growth=0.025,
        sector_beta=1.20,
        sector_wacc=0.105,
    ),
    "default": SectorMultiples(
        ev_ebitda=(8.0, 12.0, 16.0),
        ev_revenue=(1.5, 2.5, 4.0),
        terminal_growth=0.020,
        sector_beta=1.00,
        sector_wacc=0.095,
    ),
}

_ALIASES: dict[str, str] = {
    "tech": "technology",
    "information technology": "technology",
    "it services": "technology",
    "cloud": "software",
    "saas": "software",
    "software as a service": "software",
    "enterprise software": "software",
    "chips": "semiconductors",
    "semiconductor": "semiconductors",
    "drug": "pharmaceuticals",
    "pharma": "pharmaceuticals",
    "biotech": "biotechnology",
    "life sciences": "biotechnology",
    "medical": "healthcare",
    "hospital": "healthcare",
    "health": "healthcare",
    "food": "consumer staples",
    "beverage": "consumer staples",
    "tobacco": "consumer staples",
    "household": "consumer staples",
    "luxury": "consumer discretionary",
    "apparel": "consumer discretionary",
    "auto": "consumer discretionary",
    "automotive": "consumer discretionary",
    "e commerce": "e-commerce",
    "online retail": "e-commerce",
    "marketplace": "e-commerce",
    "oil": "energy",
    "gas": "energy",
    "mining": "materials",
    "chemicals": "materials",
    "metals": "materials",
    "defense": "aerospace",
    "power": "utilities",
    "electric": "utilities",
    "water": "utilities",
    "bank": "banking",
    "banks": "banking",
    "financial services": "financials",
    "insurance": "insurance",
    "asset manager": "asset management",
    "investment management": "asset management",
    "reit": "real estate",
    "property": "real estate",
    "wireless": "telecom",
    "cable": "telecom",
    "internet": "communication services",
    "social media": "communication services",
    "streaming": "communication services",
    "advertising": "media",
    "publishing": "media",
    "gaming": "technology",
    "video games": "technology",
}


def _word_in(phrase: str, text: str) -> bool:
    """Return True if all words of `phrase` appear as whole words in `text`."""
    import re
    for word in phrase.split():
        if not re.search(r'\b' + re.escape(word) + r'\b', text):
            return False
    return True


def get_sector_multiples(sector: str) -> SectorMultiples:
    """
    Return the SectorMultiples for a given sector string.
    Case-insensitive with alias resolution. Falls back to 'default'.
    Matching priority:
      1. Exact key match
      2. Exact alias match
      3. Alias whole-word substring match (longest alias wins to avoid "fin" → financials for "fintech")
      4. Canonical whole-word substring match
      5. Default
    """
    key = sector.strip().lower()

    if key in _MULTIPLES:
        return _MULTIPLES[key]
    if key in _ALIASES:
        return _MULTIPLES[_ALIASES[key]]

    # Sort aliases by length descending so longer (more specific) aliases win
    for alias, canonical in sorted(_ALIASES.items(), key=lambda x: -len(x[0])):
        if _word_in(alias, key) or _word_in(key, alias):
            return _MULTIPLES[canonical]

    for canonical in sorted(_MULTIPLES.keys(), key=lambda x: -len(x)):
        if _word_in(canonical, key) or _word_in(key, canonical):
            return _MULTIPLES[canonical]

    return _MULTIPLES["default"]


def is_financial_sector(sector: str) -> bool:
    """Return True if this sector uses P/E / P/B valuation (not EV/EBITDA)."""
    return get_sector_multiples(sector).valuation_method == "pe_pb"


# ── Sector benchmark tables (EBITDA margin + revenue growth) ─────────────────
# Used by fill_gaps to tag estimated values when live data is absent.

_SECTOR_EBITDA_MARGINS: dict[str, float] = {
    "technology": 0.20,
    "software": 0.25,
    "semiconductors": 0.30,
    "healthcare": 0.18,
    "biotechnology": -0.05,
    "pharmaceuticals": 0.22,
    "consumer staples": 0.15,
    "consumer discretionary": 0.12,
    "retail": 0.08,
    "industrials": 0.12,
    "aerospace": 0.12,
    "energy": 0.20,
    "utilities": 0.30,
    "financials": 0.30,
    "banking": 0.30,
    "insurance": 0.15,
    "asset management": 0.30,
    "real estate": 0.35,
    "materials": 0.15,
    "telecom": 0.28,
    "communication services": 0.22,
    "media": 0.18,
    "e-commerce": 0.08,
    "default": 0.18,
}

_SECTOR_REV_GROWTH: dict[str, float] = {
    "technology": 0.12,
    "software": 0.18,
    "semiconductors": 0.12,
    "healthcare": 0.08,
    "biotechnology": 0.20,
    "pharmaceuticals": 0.06,
    "consumer staples": 0.04,
    "consumer discretionary": 0.06,
    "retail": 0.04,
    "industrials": 0.05,
    "aerospace": 0.06,
    "energy": 0.03,
    "utilities": 0.03,
    "financials": 0.06,
    "banking": 0.05,
    "insurance": 0.04,
    "asset management": 0.07,
    "real estate": 0.05,
    "materials": 0.04,
    "telecom": 0.03,
    "communication services": 0.08,
    "media": 0.04,
    "e-commerce": 0.15,
    "default": 0.07,
}


def get_sector_ebitda_margin(sector: str) -> float:
    """Return the typical EBITDA margin for a sector (decimal)."""
    key = sector.strip().lower()
    canonical = _ALIASES.get(key, key)
    return _SECTOR_EBITDA_MARGINS.get(canonical, _SECTOR_EBITDA_MARGINS["default"])


def get_sector_rev_growth(sector: str) -> float:
    """Return the typical revenue growth rate for a sector (decimal)."""
    key = sector.strip().lower()
    canonical = _ALIASES.get(key, key)
    return _SECTOR_REV_GROWTH.get(canonical, _SECTOR_REV_GROWTH["default"])
