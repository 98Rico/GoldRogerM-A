"""
Abstract base class for all Gold Roger data providers.

To add a new provider:
  1. Copy data/providers/TEMPLATE.py to data/providers/your_name.py
  2. Implement fetch(), fetch_by_name(), and capabilities()
  3. Add it to build_default_registry() in data/registry.py

Provider contract:
  - fetch(ticker)         → public companies via ticker symbol
  - fetch_by_name(name)   → any company by name (private + public)
  - fetch_by_siren(siren) → French/EU companies via registry ID
  - capabilities()        → metadata for UI status page and registry routing

All fetch methods return Optional[MarketData]: None means "not found or not supported",
not an error. Raise exceptions only for unrecoverable failures (the registry catches them).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from goldroger.data.fetcher import MarketData


@dataclass
class ProviderCapabilities:
    """
    Metadata about a provider — consumed by the UI status page and registry router.

    Fields marked with * are set at runtime by the registry; others are static.
    """
    name: str
    display_name: str
    description: str
    coverage: list[str]          # ISO-2 codes ("FR", "GB") or ["GLOBAL"]
    company_types: list[str]     # subsets of ["public", "private"]
    data_fields: list[str]       # fields reliably populated, e.g. ["revenue", "sector"]
    cost_tier: str               # "free" | "freemium" | "paid"
    requires_key: bool
    key_env_var: str = ""        # env variable name, e.g. "PAPPERS_API_KEY"
    key_signup_url: str = ""     # where to get the key
    rate_limit: str = ""         # human-readable, e.g. "200 req/day"
    source_type: str = "api"     # "api" | "filing" | "registry" | "fallback"
    freshness: str = "unknown"   # e.g. "intraday", "daily", "quarterly", "unknown"
    confidence_level: str = "inferred"  # "verified" | "estimated" | "inferred"
    limitations: list[str] = field(default_factory=list)
    raw_fields: list[str] = field(default_factory=list)
    normalized_fields: list[str] = field(default_factory=list)
    failure_reason: str = ""
    # Set at runtime:
    is_available: bool = False   # * True if provider can be called right now
    status: str = "unknown"      # * "active" | "needs_key" | "not_implemented"


class DataProvider(ABC):
    """
    Base class for all data providers. Subclass this and implement at minimum:
      - fetch()
      - fetch_by_name()
      - capabilities()

    Optional:
      - fetch_by_siren()  (EU registry ID lookup)
      - resolve_ticker()  (name → ticker symbol)
    """

    name: str = "base"

    # ── Availability ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if this provider can be called (credentials present, etc.)."""
        return True

    # ── Data fetching ─────────────────────────────────────────────────────────

    @abstractmethod
    def fetch(self, ticker: str) -> Optional[MarketData]:
        """
        Fetch MarketData for a public company by ticker symbol.
        Return None if ticker not found or provider doesn't support ticker-based lookup.
        """
        ...

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        """
        Fetch MarketData by company name (supports private companies).
        Return None if company not found or provider doesn't support name lookup.
        Override this in providers that support name-based search.
        """
        return None

    def fetch_by_siren(self, siren: str, company_name: str = "") -> Optional[MarketData]:
        """
        Fetch MarketData by SIREN/EU registry ID.
        Return None if not supported by this provider.
        Override this in EU registry providers.
        """
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        """Resolve a company name to a ticker. Return None if not supported."""
        return None

    # ── Metadata ──────────────────────────────────────────────────────────────

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """
        Return static metadata about this provider.
        The registry calls this to build the UI status page and to route requests.

        Example:
            return ProviderCapabilities(
                name="pappers",
                display_name="Pappers",
                description="French company registry with verified revenue (RNCS/INPI)",
                coverage=["FR"],
                company_types=["private", "public"],
                data_fields=["revenue", "net_income", "sector", "employees"],
                cost_tier="paid",
                requires_key=True,
                key_env_var="PAPPERS_API_KEY",
                key_signup_url="https://www.pappers.fr/api",
            )
        """
        ...
