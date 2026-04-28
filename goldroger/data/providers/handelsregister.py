"""
Handelsregister / offeneregister.de provider — German companies, free, no key required.

offeneregister.de is an open-source mirror of the German commercial register with
structured JSON data. Provides: company name, legal form, registered address,
registration court, status.

Financial data (annual accounts) for German companies is published in the
Bundesanzeiger (bundesanzeiger.de) — fetched best-effort via structured search.

No credentials required. Data may lag ~1–2 years.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from .base import DataProvider

_OFFENE_BASE = "https://api.offeneregister.de"
_BUNDESANZEIGER_SEARCH = "https://www.bundesanzeiger.de/pub/de/search"


class HandelsregisterProvider(DataProvider):
    name = "handelsregister"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        profile = self._search_offeneregister(company_name)
        if not profile:
            return None

        # Try to get revenue from Bundesanzeiger (best-effort)
        revenue = self._fetch_bundesanzeiger_revenue(company_name)

        return MarketData(
            ticker=company_name.upper()[:6],
            company_name=profile.get("name", company_name),
            sector="",  # Handelsregister doesn't expose sector codes reliably
            revenue_ttm=revenue,
            confidence="verified" if revenue else "inferred",
            data_source="handelsregister",
        )

    def _search_offeneregister(self, company_name: str) -> Optional[dict]:
        try:
            resp = httpx.get(
                f"{_OFFENE_BASE}/companies",
                params={"name": company_name, "limit": 5},
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            companies = data if isinstance(data, list) else data.get("data", [])
            for c in companies:
                name = c.get("name", "")
                if company_name.lower() in name.lower():
                    return c
            return companies[0] if companies else None
        except Exception:
            return None

    def _fetch_bundesanzeiger_revenue(self, company_name: str) -> Optional[float]:
        """Best-effort: search Bundesanzeiger for published annual reports."""
        try:
            resp = httpx.get(
                _BUNDESANZEIGER_SEARCH,
                params={"query": company_name, "type": "jahresabschluss"},
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            )
            if resp.status_code != 200:
                return None
            # Extract revenue figures from HTML (Umsatzerlöse = revenue)
            text = resp.text
            patterns = [
                r'Umsatzerlöse[^<]*<[^>]+>\s*([\d.,]+)\s*(?:Tsd\.|T€|EUR)',
                r'Umsatz[^<]*>\s*([\d.,]+)\s*(?:Tsd\.|T€)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    raw = match.group(1).replace(".", "").replace(",", ".")
                    val_k_eur = float(raw)
                    return val_k_eur / 1000 * 1.11  # k€ → M€ → M$ (CHF-adjacent rate)
        except Exception:
            pass
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None
