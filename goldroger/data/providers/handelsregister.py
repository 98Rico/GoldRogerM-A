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
_UNTERNEHMENSREGISTER = "https://www.unternehmensregister.de/ureg/result.html"
_EUR_USD = 1.11  # approximate


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
            # Try Bundesanzeiger first (official publication)
            revenue = self._fetch_bundesanzeiger_revenue(query)
            if revenue is None:
                # Fallback: Unternehmensregister (broader coverage)
                revenue = self._fetch_unternehmensregister_revenue(query)
            if revenue is not None:
                return MarketData(
                    ticker=company_name.upper()[:6],
                    company_name=company_name,
                    sector="",
                    revenue_ttm=revenue,
                    confidence="verified",
                    data_source="handelsregister",
                )

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

    def _parse_german_number(self, s: str) -> Optional[float]:
        """Parse German-format numbers: '1.234.567,89' → 1234567.89"""
        try:
            # German: period = thousands separator, comma = decimal
            cleaned = s.replace(".", "").replace(",", ".")
            return float(cleaned)
        except ValueError:
            return None

    def _fetch_bundesanzeiger_revenue(self, company_name: str) -> Optional[float]:
        try:
            resp = httpx.get(
                _BUNDESANZEIGER_SEARCH,
                params={"query": company_name, "type": "jahresabschluss"},
                timeout=12,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            )
            if resp.status_code != 200:
                return None
            text = resp.text
            # Broader set of German revenue line labels in annual accounts
            patterns = [
                # Umsatzerlöse (standard HGB revenue line)
                r'Umsatzerlöse[^\d]{0,60}([\d.,]{4,})\s*(?:Tsd\.|T€|EUR|TEUR|€)?',
                r'Umsatz[^\d]{0,40}([\d.,]{4,})\s*(?:Tsd\.|T€)',
                # Gesamtleistung (total output — sometimes used instead)
                r'Gesamtleistung[^\d]{0,60}([\d.,]{4,})\s*(?:Tsd\.|T€|EUR)',
                # Erträge aus Lieferungen (revenues from deliveries — manufacturing)
                r'Erträge[^\d]{0,60}([\d.,]{4,})\s*(?:Tsd\.|T€|EUR)',
                # Umsatz in table cells (HTML)
                r'Umsatz[^<]{0,20}</td>\s*<td[^>]*>\s*([\d.,]+)',
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    val = self._parse_german_number(match.group(1).strip())
                    if val is None:
                        continue
                    # Determine scale: if value > 100M it's probably in EUR, else k€
                    if val > 100_000_000:
                        return val / 1_000_000 * _EUR_USD  # EUR → M$
                    elif val > 100_000:
                        return val / 1_000 * _EUR_USD       # k€ → M$
                    elif val > 100:
                        return val * _EUR_USD                # M€ → M$
        except Exception:
            pass
        return None

    def _fetch_unternehmensregister_revenue(self, company_name: str) -> Optional[float]:
        """Try Unternehmensregister.de — broader DE company database."""
        try:
            resp = httpx.get(
                _UNTERNEHMENSREGISTER,
                params={"rbuttonValue": "all", "suchBegriff": company_name},
                timeout=12,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            )
            if resp.status_code != 200:
                return None
            text = resp.text
            m = re.search(
                r'Umsatz[^\d]{0,60}([\d.,]{4,})\s*(?:Tsd\.|T€|EUR|TEUR)',
                text, re.IGNORECASE,
            )
            if m:
                val = self._parse_german_number(m.group(1).strip())
                if val and val > 100:
                    return val * _EUR_USD if val < 100_000 else val / 1_000 * _EUR_USD
        except Exception:
            pass
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None

    def capabilities(self) -> "ProviderCapabilities":
        from .base import ProviderCapabilities
        return ProviderCapabilities(
            name="handelsregister",
            display_name="Bundesanzeiger (DE)",
            description="German company registry via Bundesanzeiger — best-effort revenue from annual accounts",
            coverage=["DE"],
            company_types=["public", "private"],
            data_fields=["revenue", "sector"],
            cost_tier="free",
            requires_key=False,
        )
