"""Abstract base class for all data providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from goldroger.data.fetcher import MarketData


class DataProvider(ABC):
    name: str = "base"
    requires_credentials: bool = False

    def is_available(self) -> bool:
        """Returns True if this provider can be used (credentials present, service reachable)."""
        return True

    @abstractmethod
    def fetch(self, ticker: str) -> Optional[MarketData]:
        """Fetch MarketData for a given ticker. Return None if unavailable."""
        ...

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        """Resolve a company name to a ticker symbol. Return None if not supported."""
        return None
