"""
Bloomberg BLP data provider — stub.

Requires:
  - Bloomberg Terminal or Bloomberg Anywhere subscription (~$25k/year)
  - blpapi Python SDK (install separately: pip install blpapi)
  - BLOOMBERG_API_KEY env var (set to any non-empty value to activate routing)

When credentials are present, this provider is queried FIRST (highest priority).
Bloomberg provides: real-time prices, consensus estimates, private company data,
M&A comps, covenant data, credit ratings, and much more.

To activate: set BLOOMBERG_API_KEY=your_key in your .env file.
Contact bloomberg@goldroger.ai for enterprise integration support.
"""
from __future__ import annotations

import os
from typing import Optional

from goldroger.data.fetcher import MarketData
from .base import DataProvider, ProviderCapabilities


class BloombergProvider(DataProvider):
    name = "bloomberg"
    requires_credentials = True

    def is_available(self) -> bool:
        return bool(os.getenv("BLOOMBERG_API_KEY"))

    def fetch(self, ticker: str) -> Optional[MarketData]:
        if not self.is_available():
            return None
        # TODO: implement using blpapi
        # from blpapi import SessionOptions, Session
        # session = Session(SessionOptions())
        # session.start()
        # ... fetch BDP/BDH fields
        raise NotImplementedError(
            "Bloomberg BLP integration is not yet implemented. "
            "Remove BLOOMBERG_API_KEY or implement blpapi calls."
        )

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="bloomberg",
            display_name="Bloomberg Terminal",
            description="Real-time prices, private company data, M&A comps, consensus estimates",
            coverage=["GLOBAL"],
            company_types=["public", "private"],
            data_fields=["revenue", "ebitda", "margins", "multiples", "comps", "estimates"],
            cost_tier="paid",
            requires_key=True,
            key_env_var="BLOOMBERG_API_KEY",
            key_signup_url="https://www.bloomberg.com/professional/",
            rate_limit="",
        )
