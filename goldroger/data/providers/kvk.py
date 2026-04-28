"""
KVK (Kamer van Koophandel) — Dutch company registry. Free REST API, no key required.
Provides: company name, SBI sector code, address, status. Revenue not in public API.
"""
from __future__ import annotations
from typing import Optional
import httpx
from goldroger.data.fetcher import MarketData
from .base import DataProvider

_BASE = "https://api.kvk.nl/api/v2"

_SBI_SECTOR = {
    "62": "Technology", "63": "Technology",
    "64": "Financial Services", "65": "Financial Services",
    "47": "Retail", "46": "Wholesale",
    "56": "Consumer Discretionary", "55": "Consumer Discretionary",
    "86": "Healthcare", "87": "Healthcare",
    "41": "Real Estate", "68": "Real Estate",
    "49": "Industrials", "28": "Industrials",
    "35": "Energy",
}


class KVKProvider(DataProvider):
    name = "kvk"
    requires_credentials = True

    def is_available(self) -> bool:
        # KVK API requires an API key (api.kvk.nl returns 401 without one).
        # Apply for a free key at developers.kvk.nl
        import os
        return bool(os.getenv("KVK_API_KEY", ""))

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        from goldroger.data.name_resolver import resolve
        ids = resolve(company_name)
        for variant in list(dict.fromkeys([ids.legal_suffixes_stripped, company_name] + ids.variants)):
            if not variant:
                continue
            try:
                resp = httpx.get(
                    f"{_BASE}/zoeken",
                    params={"naam": variant, "resultatenPerPagina": 5},
                    timeout=10,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    continue
                items = resp.json().get("resultaten", [])
                if not items:
                    continue
                best = items[0]
                sbi = best.get("sbiCode", "")
                sector = _SBI_SECTOR.get(sbi[:2], "") if sbi else ""
                return MarketData(
                    ticker=company_name.upper()[:6],
                    company_name=best.get("naam", company_name),
                    sector=sector,
                    revenue_ttm=None,  # KVK public API doesn't expose financials
                    confidence="inferred",
                    data_source="kvk",
                )
            except Exception:
                continue
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None
