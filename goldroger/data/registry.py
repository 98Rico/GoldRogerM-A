"""
Data source registry — priority-ordered chain of data providers.

Default priority (highest to lowest):
  1. Bloomberg      (if BLOOMBERG_API_KEY set)
  2. Capital IQ     (if CAPITALIQ_USERNAME + CAPITALIQ_PASSWORD set)
  3. Refinitiv      (if REFINITIV_APP_KEY set)
  4. yfinance       (always available — free, public companies)
  5. SEC EDGAR      (always available — free, US public companies only)

Add new providers via register(). The first provider returning non-None data wins.
"""
from __future__ import annotations

from typing import Optional

from goldroger.data.fetcher import MarketData
from .providers.base import DataProvider
from .providers.bloomberg import BloombergProvider
from .providers.capitaliq import CapitalIQProvider, RefinitivProvider
from .providers.crunchbase import CrunchbaseProvider
from .providers.yfinance import YFinanceProvider
from .providers.sec_edgar import SECEdgarProvider


class DataRegistry:
    def __init__(self) -> None:
        self._providers: list[DataProvider] = []

    def register(self, provider: DataProvider) -> None:
        self._providers.append(provider)

    def available_providers(self) -> list[str]:
        return [p.name for p in self._providers if p.is_available()]

    def fetch(self, ticker: str) -> Optional[MarketData]:
        for provider in self._providers:
            if not provider.is_available():
                continue
            try:
                data = provider.fetch(ticker)
                if data is not None:
                    data.data_source = provider.name
                    return data
            except NotImplementedError:
                continue
            except Exception:
                continue
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        for provider in self._providers:
            if not provider.is_available():
                continue
            try:
                ticker = provider.resolve_ticker(company_name)
                if ticker:
                    return ticker
            except Exception:
                continue
        return None


def build_default_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.register(BloombergProvider())
    registry.register(CapitalIQProvider())
    registry.register(RefinitivProvider())
    registry.register(YFinanceProvider())
    registry.register(CrunchbaseProvider())
    registry.register(SECEdgarProvider())
    return registry


DEFAULT_REGISTRY: DataRegistry = build_default_registry()
