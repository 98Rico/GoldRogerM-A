"""
Crunchbase data provider — freemium, useful for private companies.

Free tier: 200 requests/day via Basic API.
Provides: funding rounds, estimated revenue, headcount, investors, founders.

To activate: set CRUNCHBASE_API_KEY in your .env file.
Get a free key at: https://data.crunchbase.com/docs/using-the-api

Best for: startups, scale-ups, VC-backed private companies.
Less useful for: family-owned businesses (Longchamp), unlisted conglomerates.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from .base import DataProvider

_BASE = "https://api.crunchbase.com/api/v4"


class CrunchbaseProvider(DataProvider):
    name = "crunchbase"
    requires_credentials = True

    def is_available(self) -> bool:
        return bool(os.getenv("CRUNCHBASE_API_KEY"))

    def fetch(self, ticker: str) -> Optional[MarketData]:
        # Crunchbase doesn't use stock tickers — use resolve_ticker flow instead
        return None

    def search_company(self, company_name: str) -> Optional[dict]:
        """Search Crunchbase for a company and return raw entity data."""
        api_key = os.getenv("CRUNCHBASE_API_KEY")
        if not api_key:
            return None
        try:
            resp = httpx.post(
                f"{_BASE}/searches/organizations",
                params={"user_key": api_key},
                json={
                    "field_ids": [
                        "identifier", "short_description", "revenue_range",
                        "num_employees_enum", "funding_total", "last_funding_type",
                    ],
                    "query": [{"type": "predicate", "field_id": "facet_ids",
                               "operator_id": "includes", "values": ["company"]}],
                    "limit": 5,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            entities = resp.json().get("entities", [])
            # Try to find closest name match
            for e in entities:
                name = e.get("properties", {}).get("identifier", {}).get("value", "")
                if company_name.lower() in name.lower():
                    return e.get("properties", {})
            return entities[0].get("properties", {}) if entities else None
        except Exception:
            return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        """Best-effort: fetch revenue estimate from Crunchbase by company name."""
        data = self.search_company(company_name)
        if not data:
            return None

        # revenue_range is a string like "$10M to $50M" — take midpoint
        revenue = _parse_revenue_range(data.get("revenue_range"))
        if not revenue:
            return None

        return MarketData(
            ticker=company_name.upper()[:6],
            company_name=company_name,
            sector="",
            revenue_ttm=revenue,
            confidence="estimated",
            data_source="crunchbase",
        )

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None  # Crunchbase has no tickers


def _parse_revenue_range(range_str: Optional[str]) -> Optional[float]:
    """Parse Crunchbase revenue range string to USD millions midpoint."""
    if not range_str:
        return None
    # Format: "$10M to $50M", "$1B to $10B", "Less than $1M"
    import re
    nums = re.findall(r"[\d.]+\s*[MBK]?", range_str.replace(",", ""))
    values = []
    for n in nums:
        n = n.strip()
        if n.endswith("B"):
            values.append(float(n[:-1]) * 1000)
        elif n.endswith("M"):
            values.append(float(n[:-1]))
        elif n.endswith("K"):
            values.append(float(n[:-1]) / 1000)
        elif n:
            try:
                values.append(float(n))
            except ValueError:
                pass
    if not values:
        return None
    return sum(values) / len(values)  # midpoint
