"""
Sector-calibrated valuation multiple tables.

Ranges reflect approximate market consensus as of 2024-2025 based on
Damodaran sector data, FactSet, and Bloomberg aggregates.

All multiples are EV-based:
  - ev_ebitda: (low, mid, high)
  - ev_revenue: (low, mid, high)
  - terminal_growth: long-run FCF growth rate for DCF
  - sector_beta: unleveraged sector beta (re-lever per company capital structure)
  - sector_wacc: typical WACC range midpoint when CAPM not computable
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectorMultiples:
    ev_ebitda: tuple[float, float, float]   # low, mid, high
    ev_revenue: tuple[float, float, float]  # low, mid, high
    terminal_growth: float                  # decimal, e.g. 0.025
    sector_beta: float                      # reference beta
    sector_wacc: float                      # fallback WACC (decimal)


# Keyed by canonical sector name (lowercase).
# Lookup via get_sector_multiples() handles fuzzy matching.
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
    "financials": SectorMultiples(
        ev_ebitda=(8.0, 11.0, 15.0),
        ev_revenue=(2.0, 3.0, 5.0),
        terminal_growth=0.020,
        sector_beta=1.00,
        sector_wacc=0.095,
    ),
    "banking": SectorMultiples(
        ev_ebitda=(7.0, 10.0, 14.0),
        ev_revenue=(2.0, 3.5, 5.0),
        terminal_growth=0.020,
        sector_beta=0.95,
        sector_wacc=0.093,
    ),
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

# Keyword → canonical key mapping for fuzzy sector resolution
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
    "insurance": "financials",
    "asset management": "financials",
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


def get_sector_multiples(sector: str) -> SectorMultiples:
    """
    Return the SectorMultiples for a given sector string.
    Performs case-insensitive lookup with alias resolution.
    Falls back to 'default' if no match found.
    """
    key = sector.strip().lower()

    if key in _MULTIPLES:
        return _MULTIPLES[key]

    if key in _ALIASES:
        return _MULTIPLES[_ALIASES[key]]

    # Partial-match scan
    for alias, canonical in _ALIASES.items():
        if alias in key or key in alias:
            return _MULTIPLES[canonical]

    for canonical in _MULTIPLES:
        if canonical in key or key in canonical:
            return _MULTIPLES[canonical]

    return _MULTIPLES["default"]
