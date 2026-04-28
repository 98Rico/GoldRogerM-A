"""
Handelsregister provider — German companies.

Primary: Bundesanzeiger (bundesanzeiger.de) — official published annual accounts.
Note: api.offeneregister.de is DNS-dead as of 2025 and has been removed.

Revenue extraction from Bundesanzeiger HTML is best-effort (regex on Umsatzerlöse).
Data may lag 1–2 years. No credentials required.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from goldroger.data.name_resolver import resolve
from .base import DataProvider

_BUNDESANZEIGER_SEARCH = "https://www.bundesanzeiger.de/pub/de/search"


class HandelsregisterProvider(DataProvider):
    name = "handelsregister"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        ids = resolve(company_name)
        queries = list(dict.fromkeys(filter(None, [
            ids.handelsregister_query, company_name, *ids.variants,
        ])))

        for query in queries:
            revenue = self._fetch_bundesanzeiger_revenue(query)
            if revenue is not None:
                return MarketData(
                    ticker=company_name.upper()[:6],
                    company_name=company_name,
                    sector="",
                    revenue_ttm=revenue,
                    confidence="verified",
                    data_source="handelsregister",
                )

        # Return partial record (sector/name only) if Bundesanzeiger found the company
        # but couldn't parse revenue
        if self._bundesanzeiger_exists(company_name):
            return MarketData(
                ticker=company_name.upper()[:6],
                company_name=company_name,
                sector="",
                revenue_ttm=None,
                confidence="inferred",
                data_source="handelsregister",
            )
        return None

    def _bundesanzeiger_exists(self, company_name: str) -> bool:
        try:
            resp = httpx.get(
                _BUNDESANZEIGER_SEARCH,
                params={"query": company_name},
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            )
            return resp.status_code == 200 and company_name.lower()[:6] in resp.text.lower()
        except Exception:
            return False

    def _fetch_bundesanzeiger_revenue(self, company_name: str) -> Optional[float]:
        try:
            resp = httpx.get(
                _BUNDESANZEIGER_SEARCH,
                params={"query": company_name, "type": "jahresabschluss"},
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            )
            if resp.status_code != 200:
                return None
            text = resp.text
            patterns = [
                r'Umsatzerlöse[^<]*<[^>]+>\s*([\d.,]+)\s*(?:Tsd\.|T€|EUR|TEUR)',
                r'Umsatz[^<]*>\s*([\d.,]+)\s*(?:Tsd\.|T€)',
                r'Gesamtleistung[^<]*<[^>]+>\s*([\d.,]+)\s*(?:Tsd\.|T€)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    raw = match.group(1).replace(".", "").replace(",", ".")
                    val_k_eur = float(raw)
                    return val_k_eur / 1000 * 1.11  # k€ → M€ → M$ (approx)
        except Exception:
            pass
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None
