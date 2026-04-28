"""
Companies House (UK) data provider — free REST API, no key required for basic search.

Provides: company verification, SIC codes (sector), incorporation date, status.
Revenue from filed accounts where available (iXBRL, best-effort).

Register at https://developer.company-information.service.gov.uk for an API key
to avoid anonymous rate limits (600 req/5min vs 50 req/5min unauthenticated).

Set COMPANIES_HOUSE_API_KEY in .env to activate authenticated access.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from .base import DataProvider

_BASE = "https://api.company-information.service.gov.uk"

# SIC code → sector name (partial, most common M&A sectors)
_SIC_SECTOR = {
    "62": "Technology",  "63": "Technology",
    "64": "Financial Services", "65": "Financial Services", "66": "Financial Services",
    "47": "Retail", "46": "Wholesale",
    "10": "Consumer Staples", "11": "Consumer Staples",
    "56": "Consumer Discretionary",
    "72": "Healthcare",
    "41": "Real Estate", "68": "Real Estate",
    "49": "Industrials", "52": "Industrials",
    "35": "Energy",
    "85": "Education",
    "86": "Healthcare", "87": "Healthcare",
}


class CompaniesHouseProvider(DataProvider):
    name = "companies_house"
    requires_credentials = False

    def is_available(self) -> bool:
        return True  # free, always available

    def _auth(self):
        api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        return (api_key, "") if api_key else None

    def _get(self, path: str, params: dict | None = None) -> Optional[dict]:
        try:
            resp = httpx.get(
                f"{_BASE}{path}",
                params=params,
                auth=self._auth(),
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def _search(self, company_name: str) -> Optional[str]:
        data = self._get("/search/companies", {"q": company_name, "items_per_page": 5})
        if not data:
            return None
        items = data.get("items", [])
        for item in items:
            title = item.get("title", "").lower()
            if company_name.lower() in title:
                return item.get("company_number")
        return items[0].get("company_number") if items else None

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None  # Companies House uses company names, not tickers

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        company_number = self._search(company_name)
        if not company_number:
            return None

        profile = self._get(f"/company/{company_number}")
        if not profile:
            return None

        status = profile.get("company_status", "")
        if status not in ("active", ""):
            return None

        # Derive sector from SIC codes
        sic_codes = profile.get("sic_codes", [])
        sector = ""
        for sic in sic_codes:
            prefix = sic[:2]
            if prefix in _SIC_SECTOR:
                sector = _SIC_SECTOR[prefix]
                break

        # Try to get revenue from most recent filed accounts (best-effort)
        revenue = self._fetch_revenue(company_number)

        return MarketData(
            ticker=company_name.upper()[:6],
            company_name=profile.get("company_name", company_name),
            sector=sector,
            revenue_ttm=revenue,
            confidence="verified" if revenue else "inferred",
            data_source="companies_house",
        )

    def _fetch_revenue(self, company_number: str) -> Optional[float]:
        filings = self._get(
            f"/company/{company_number}/filing-history",
            {"category": "accounts", "items_per_page": 5},
        )
        if not filings:
            return None
        items = filings.get("items", [])
        for filing in items:
            # Only full accounts have revenue; abbreviated don't
            ftype = filing.get("type", "")
            if ftype in ("AA", "ACCOUNTS TYPE FULL", "ACCOUNTS TYPE SMALL"):
                doc_url = (
                    filing.get("links", {})
                    .get("document_metadata", "")
                )
                if doc_url:
                    revenue = self._parse_xbrl_revenue(doc_url)
                    if revenue:
                        return revenue
        return None

    def _parse_xbrl_revenue(self, metadata_url: str) -> Optional[float]:
        try:
            meta = httpx.get(
                metadata_url,
                auth=self._auth(),
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if meta.status_code != 200:
                return None
            links = meta.json().get("links", {})
            doc_url = links.get("document", "")
            if not doc_url:
                return None
            doc = httpx.get(doc_url, auth=self._auth(), timeout=15)
            if doc.status_code != 200:
                return None
            # Look for turnover/revenue in XBRL inline tags
            import re
            text = doc.text
            patterns = [
                r'ix:nonFraction[^>]*name="[^"]*[Tt]urnover[^"]*"[^>]*>\s*([\d,]+)',
                r'ix:nonFraction[^>]*name="[^"]*[Rr]evenue[^"]*"[^>]*>\s*([\d,]+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    val = float(match.group(1).replace(",", ""))
                    return val / 1_000_000  # convert to USD millions (approximate, GBP→USD)
        except Exception:
            pass
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None  # CH companies are private, no tickers
