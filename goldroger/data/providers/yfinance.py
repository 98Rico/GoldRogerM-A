"""yfinance data provider — wraps existing fetcher (always available, no credentials)."""
from __future__ import annotations

from typing import Optional

from goldroger.data.fetcher import MarketData, fetch_market_data, resolve_ticker
from .base import DataProvider, ProviderCapabilities


class YFinanceProvider(DataProvider):
    name = "yfinance"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return fetch_market_data(ticker)

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return resolve_ticker(company_name)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="yfinance",
            display_name="Yahoo Finance",
            description="Free real-time market data for all listed public companies",
            coverage=["GLOBAL"],
            company_types=["public"],
            data_fields=["revenue", "ebitda", "margins", "multiples", "estimates", "beta"],
            cost_tier="free",
            requires_key=False,
        )
