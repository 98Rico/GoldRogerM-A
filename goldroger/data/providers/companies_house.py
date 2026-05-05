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
        # Anonymous access was removed — API key required as of 2024.
        # Register free at developer.company-information.service.gov.uk
        return bool(os.getenv("COMPANIES_HOUSE_API_KEY", ""))

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
        if not items:
            return None
        from goldroger.data.name_resolver import fuzzy_best_match
        candidate_names = [item.get("title", "") for item in items]
        matched = fuzzy_best_match(company_name, candidate_names, threshold=0.6)
        best = next(
            (item for item in items if item.get("title") == matched),
            items[0],
        )
        return best.get("company_number")

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None  # Companies House uses company names, not tickers

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        from goldroger.data.name_resolver import resolve
        ids = resolve(company_name)
        # Try each variant to maximise match rate
        company_number = None
        for variant in ([ids.companies_house_query] + ids.variants):
            if variant:
                company_number = self._search(variant)
                if company_number:
                    break
        if not company_number:
            return None

        return self.fetch_by_company_number(company_number, fallback_name=company_name)

    def fetch_by_company_number(self, company_number: str, fallback_name: str = "") -> Optional[MarketData]:
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
            ticker=(profile.get("company_name", fallback_name) or fallback_name or company_number).upper()[:6],
            company_name=profile.get("company_name", fallback_name or company_number),
            sector=sector,
            revenue_ttm=revenue,
            confidence="verified" if revenue else "inferred",
            data_source="companies_house",
        )

    def _fetch_revenue(self, company_number: str) -> Optional[float]:
        filings = self._get(
            f"/company/{company_number}/filing-history",
            {"category": "accounts", "items_per_page": 10},
        )
        if not filings:
            return None
        items = filings.get("items", [])
        # Prefer full/small accounts over abbreviated (abbreviated rarely have revenue)
        priority_types = {"AA", "ACCOUNTS TYPE FULL", "ACCOUNTS TYPE SMALL", "AA01"}
        sorted_filings = sorted(
            items,
            key=lambda f: (0 if f.get("type", "") in priority_types else 1),
        )
        for filing in sorted_filings:
            doc_url = filing.get("links", {}).get("document_metadata", "")
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
            doc = httpx.get(doc_url, auth=self._auth(), timeout=20)
            if doc.status_code != 200:
                return None
            text = doc.text
            import re

            # Strategy 1: iXBRL inline tags — multiple known revenue concepts
            # UK GAAP / FRS 102 / IFRS concepts in order of reliability
            xbrl_patterns = [
                # FRS 102 / UK GAAP
                r'(?:name|contextRef)="[^"]*(?:Turnover|Revenue|GrossProfit)[^"]*"[^>]*>\s*([£]?[\d,]+)',
                r'ix:nonFraction[^>]*name="[^"]*(?:Turnover|Revenue)[^"]*"[^>]*>\s*([\d,]+)',
                # Core UK taxonomy
                r'uk-core:(?:Turnover|Revenue)[^>]*>\s*([\d,]+)',
                r'bus:(?:Turnover|TotalRevenue)[^>]*>\s*([\d,]+)',
                # Inline XBRL data attributes
                r'data-xbrl-concept="[^"]*(?:turnover|revenue)[^"]*"[^>]*>\s*([£]?[\d,]+)',
            ]
            for pattern in xbrl_patterns:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    raw = m.group(1).replace(",", "").replace("£", "").strip()
                    try:
                        val = float(raw)
                        if val > 1000:  # raw pence/units below 1000 not plausible revenue
                            gbp_usd = 1.27  # approximate GBP→USD
                            # Values in CH are in GBP (£) — determine scale by magnitude
                            if val < 1_000_000:
                                # Likely in thousands
                                return val * gbp_usd / 1_000
                            else:
                                # Likely in full GBP
                                return val * gbp_usd / 1_000_000
                    except ValueError:
                        continue

            # Strategy 2: plain-text fallback — "Turnover ... £X,XXX,XXX" or "Revenue £X"
            text_patterns = [
                r'(?:Turnover|Revenue|Sales)\D{0,30}£\s*([\d,]+)',
                r'£\s*([\d,]+)\s*(?:turnover|revenue|sales)',
            ]
            for pattern in text_patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    raw = float(m.group(1).replace(",", ""))
                    if raw > 100:
                        gbp_usd = 1.27
                        return raw * gbp_usd / (1 if raw > 1_000_000 else 1_000)
        except Exception:
            pass
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None  # CH companies are private, no tickers

    def capabilities(self) -> "ProviderCapabilities":
        from .base import ProviderCapabilities
        return ProviderCapabilities(
            name="companies_house",
            display_name="Companies House",
            description="UK company registry — revenue from XBRL filings where available",
            coverage=["GB"],
            company_types=["public", "private"],
            data_fields=["revenue", "sector", "employees"],
            cost_tier="free",
            requires_key=True,
            key_env_var="COMPANIES_HOUSE_API_KEY",
            key_signup_url="https://developer.company-information.service.gov.uk/",
        )
