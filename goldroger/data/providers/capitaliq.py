"""
S&P Capital IQ data provider — stub.

Requires:
  - Capital IQ Platform subscription (~$10k-30k/year)
  - CAPITALIQ_USERNAME + CAPITALIQ_PASSWORD env vars

Capital IQ is the gold standard for M&A comps, precedent transactions,
private company financials, and credit data. When available, it provides
richer M&A transaction data than any free source.

To activate: set CAPITALIQ_USERNAME and CAPITALIQ_PASSWORD in your .env file.

See also: Refinitiv Eikon (similar tier), FactSet (similar tier).
"""
from __future__ import annotations

import os
from typing import Optional

from goldroger.data.fetcher import MarketData
from .base import DataProvider, ProviderCapabilities


class CapitalIQProvider(DataProvider):
    name = "capital_iq"
    requires_credentials = True

    def is_available(self) -> bool:
        return bool(
            os.getenv("CAPITALIQ_USERNAME") and os.getenv("CAPITALIQ_PASSWORD")
        )

    def fetch(self, ticker: str) -> Optional[MarketData]:
        if not self.is_available():
            return None
        # TODO: implement via Capital IQ API
        raise NotImplementedError(
            "Capital IQ integration is not yet implemented. "
            "Remove CAPITALIQ_USERNAME/PASSWORD or implement the API calls."
        )

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="capital_iq",
            display_name="S&P Capital IQ",
            description="M&A transaction comps, private company financials, credit data",
            coverage=["GLOBAL"],
            company_types=["public", "private"],
            data_fields=["revenue", "ebitda", "comps", "transactions", "credit"],
            cost_tier="paid",
            requires_key=True,
            key_env_var="CAPITALIQ_USERNAME",
            key_signup_url="https://www.spglobal.com/marketintelligence/en/solutions/capital-iq-platform",
        )


class RefinitivProvider(DataProvider):
    """Refinitiv Eikon / LSEG stub — similar tier to Capital IQ."""
    name = "refinitiv"
    requires_credentials = True

    def is_available(self) -> bool:
        return bool(os.getenv("REFINITIV_APP_KEY"))

    def fetch(self, ticker: str) -> Optional[MarketData]:
        if not self.is_available():
            return None
        raise NotImplementedError("Refinitiv integration pending.")

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="refinitiv",
            display_name="Refinitiv / LSEG",
            description="M&A transactions, news analytics, real-time market data",
            coverage=["GLOBAL"],
            company_types=["public", "private"],
            data_fields=["revenue", "ebitda", "transactions", "news"],
            cost_tier="paid",
            requires_key=True,
            key_env_var="REFINITIV_APP_KEY",
            key_signup_url="https://www.lseg.com/en/data-analytics",
        )
