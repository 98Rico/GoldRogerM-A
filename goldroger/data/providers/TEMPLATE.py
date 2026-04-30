"""
TEMPLATE — copy this file to create a new data provider.

Steps:
  1. cp goldroger/data/providers/TEMPLATE.py goldroger/data/providers/my_source.py
  2. Rename MySourceProvider → your class name
  3. Implement fetch(), fetch_by_name(), and capabilities()
  4. Add it to build_default_registry() in data/registry.py

Naming conventions:
  - provider name (self.name): snake_case, lowercase, e.g. "my_source"
  - env var:  SCREAMING_SNAKE, e.g. "MY_SOURCE_API_KEY"
  - file name: snake_case, e.g. my_source.py

Units:
  - All monetary values in USD millions (convert from local currency)
  - Margins as decimals: 18% → 0.18
  - MarketData.confidence: "verified" (from filings), "estimated" (computed), "inferred" (proxy)
"""
from __future__ import annotations

import os
from typing import Optional

import httpx  # already in project dependencies

from goldroger.data.fetcher import MarketData
from .base import DataProvider, ProviderCapabilities

_BASE_URL = "https://api.my-source.com/v1"  # replace with real URL


class MySourceProvider(DataProvider):
    name = "my_source"  # unique identifier used in registry routing and logs

    # ── Availability ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True only if all required credentials are present."""
        return bool(os.getenv("MY_SOURCE_API_KEY"))

    # ── Fetching ─────────────────────────────────────────────────────────────

    def fetch(self, ticker: str) -> Optional[MarketData]:
        """
        Fetch by stock ticker (public companies).
        Return None if this provider doesn't support ticker-based lookup.
        """
        # Example:
        # api_key = os.getenv("MY_SOURCE_API_KEY")
        # resp = httpx.get(f"{_BASE_URL}/company/{ticker}", params={"key": api_key}, timeout=10)
        # if resp.status_code != 200:
        #     return None
        # data = resp.json()
        # return MarketData(
        #     ticker=ticker,
        #     company_name=data["name"],
        #     sector=data.get("sector", ""),
        #     revenue_ttm=data.get("revenue_usd_millions"),
        #     confidence="verified",
        #     data_source=self.name,
        # )
        return None  # remove this line when implemented

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        """
        Fetch by company name (supports private companies).
        Return None if this provider doesn't support name-based lookup.

        IMPORTANT: always set data_source and confidence on the returned MarketData.
        confidence choices:
          "verified"  — from official filings or exchange data
          "estimated" — computed from ranges or proxies
          "inferred"  — low-confidence approximation
        """
        # Example:
        # api_key = os.getenv("MY_SOURCE_API_KEY")
        # resp = httpx.get(f"{_BASE_URL}/search", params={"name": company_name, "key": api_key}, timeout=10)
        # if resp.status_code != 200:
        #     return None
        # results = resp.json().get("results", [])
        # if not results:
        #     return None
        # best = results[0]  # add fuzzy matching if needed
        # return MarketData(
        #     ticker=company_name.upper()[:6],
        #     company_name=best["legal_name"],
        #     sector=best.get("sector", ""),
        #     revenue_ttm=best.get("revenue_usd_millions"),
        #     confidence="verified",
        #     data_source=self.name,
        # )
        return None  # remove this line when implemented

    def fetch_by_siren(self, siren: str, company_name: str = "") -> Optional[MarketData]:
        """
        Optional: fetch by SIREN / EU registry ID.
        Only needed for EU registry providers (FR, DE, etc.).
        Leave this method out (inherits None return) if not applicable.
        """
        return None

    # ── Metadata ─────────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        """
        Describe this provider for the UI status page and registry router.

        coverage: ISO-2 country codes your source covers, or ["GLOBAL"].
        company_types: ["public"], ["private"], or ["public", "private"].
        data_fields: fields you reliably populate in MarketData.
          Common values: "revenue", "ebitda", "margins", "multiples",
                         "estimates", "beta", "sector", "employees", "funding",
                         "comps", "transactions"
        cost_tier: "free" | "freemium" | "paid"
        """
        return ProviderCapabilities(
            name="my_source",
            display_name="My Source",
            description="One-line description of what this source provides",
            coverage=["GLOBAL"],          # or ["FR"], ["GB", "IE"], etc.
            company_types=["private"],    # adjust as appropriate
            data_fields=["revenue", "sector"],
            cost_tier="freemium",
            requires_key=True,
            key_env_var="MY_SOURCE_API_KEY",
            key_signup_url="https://my-source.com/api",
            rate_limit="1000 req/day",    # or "" if unlimited
        )
