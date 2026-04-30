"""
Data source registry — priority-ordered chain of providers.

Default priority for public companies (ticker-based):
  Bloomberg → CapIQ → Refinitiv → yfinance → SEC EDGAR

Default priority for private companies (name-based), geography-aware:
  Bloomberg → CapIQ → Refinitiv
  → [local providers for country_hint]  ← tried first when country known
  → Crunchbase → yfinance (name→ticker) → SEC EDGAR

To add a provider:
  1. Write your provider in data/providers/ (copy TEMPLATE.py)
  2. Register it in build_default_registry() below
  3. That's it — the registry handles routing automatically

UI integration:
  - list_providers()  → all registered providers with status (for settings page)
  - fetch_by_name(name, country_hint="FR")  → geography-aware chain
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from goldroger.data.fetcher import MarketData
from .providers.base import DataProvider, ProviderCapabilities

logger = logging.getLogger(__name__)

# Geography routing table: ISO-2 country hint → preferred provider names (tried first)
_GEO_PRIORITY: dict[str, list[str]] = {
    "FR": ["pappers", "infogreffe"],
    "GB": ["companies_house"],
    "DE": ["handelsregister"],
    "NL": ["kvk"],
    "ES": ["registro_mercantil"],
    "US": ["sec_edgar"],
}


class DataRegistry:
    def __init__(self) -> None:
        self._providers: list[DataProvider] = []

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, provider: DataProvider) -> None:
        """Add a provider to the end of the fallback chain."""
        self._providers.append(provider)

    # ── Status / UI ───────────────────────────────────────────────────────────

    def list_providers(self) -> list[ProviderCapabilities]:
        """
        Return capabilities of all registered providers with live availability status.
        Used by the UI settings/status page to show which sources are configured.
        """
        result = []
        for p in self._providers:
            caps = p.capabilities()
            caps.is_available = p.is_available()
            caps.status = "active" if caps.is_available else (
                "needs_key" if caps.requires_key else "not_implemented"
            )
            result.append(caps)
        return result

    def available_providers(self) -> list[str]:
        """Return names of all currently available providers."""
        return [p.name for p in self._providers if p.is_available()]

    # ── Fetching ──────────────────────────────────────────────────────────────

    def fetch(self, ticker: str) -> Optional[MarketData]:
        """
        Fetch by ticker — used for public companies.
        Tries providers in registration order; returns first non-None result.
        """
        for provider in self._providers:
            if not provider.is_available():
                continue
            try:
                t0 = time.monotonic()
                data = provider.fetch(ticker)
                if data is not None:
                    data.data_source = provider.name
                    logger.debug(
                        "fetch(%s) → %s (%.0fms)",
                        ticker, provider.name, (time.monotonic() - t0) * 1000,
                    )
                    return data
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.debug("fetch(%s) failed on %s: %s", ticker, provider.name, exc)
                continue
        return None

    def fetch_by_name(
        self, company_name: str, country_hint: str = ""
    ) -> Optional[MarketData]:
        """
        Fetch by company name — used for private companies or when no ticker is known.

        country_hint: ISO-2 country code ("FR", "GB", "DE", etc.).
          When provided, local providers for that country are tried before global ones.
          Leave empty for unknown geography — global fallback chain is used.
        """
        ordered = self._name_lookup_order(country_hint)
        for provider in ordered:
            if not provider.is_available():
                continue
            try:
                t0 = time.monotonic()
                data = provider.fetch_by_name(company_name)
                if data is not None:
                    data.data_source = provider.name
                    logger.debug(
                        "fetch_by_name(%s) → %s (%.0fms)",
                        company_name, provider.name, (time.monotonic() - t0) * 1000,
                    )
                    return data
            except Exception as exc:
                logger.debug(
                    "fetch_by_name(%s) failed on %s: %s",
                    company_name, provider.name, exc,
                )
                continue
        return None

    def fetch_by_siren(
        self, siren: str, company_name: str = ""
    ) -> Optional[MarketData]:
        """Fetch by SIREN or EU registry ID — used when SIREN is known."""
        for provider in self._providers:
            if not provider.is_available():
                continue
            try:
                data = provider.fetch_by_siren(siren, company_name)
                if data is not None:
                    data.data_source = provider.name
                    return data
            except Exception as exc:
                logger.debug(
                    "fetch_by_siren(%s) failed on %s: %s", siren, provider.name, exc
                )
                continue
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        """Resolve a company name to a ticker symbol."""
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

    # ── Internal ─────────────────────────────────────────────────────────────

    def _name_lookup_order(self, country_hint: str) -> list[DataProvider]:
        """
        Build provider order for name-based lookup, geography-aware.

        Providers covering the hinted country come first (preserving sub-order),
        followed by all remaining providers in their original order.
        """
        if not country_hint:
            return list(self._providers)

        preferred_names = set(_GEO_PRIORITY.get(country_hint.upper(), []))
        preferred = [p for p in self._providers if p.name in preferred_names]
        others = [p for p in self._providers if p.name not in preferred_names]
        return preferred + others


# ── Default registry ──────────────────────────────────────────────────────────

def build_default_registry() -> DataRegistry:
    """
    Build the default provider registry.

    Tier 1 — Enterprise (require paid credentials, always highest priority):
      Bloomberg, Capital IQ, Refinitiv — add your key to .env to activate.

    Tier 2 — Free / freemium global:
      yfinance (public), Crunchbase (private, needs free key), SEC EDGAR (US public)

    Tier 3 — EU registries (geography-specific, free or low-cost):
      Pappers (FR, paid), Infogreffe (FR, free), Companies House (UK, free key),
      Handelsregister (DE, free), KVK (NL, free key), Registro Mercantil (ES, free)

    Import is deferred to avoid circular imports and allow optional dependencies.
    """
    from .providers.bloomberg import BloombergProvider
    from .providers.capitaliq import CapitalIQProvider, RefinitivProvider
    from .providers.yfinance import YFinanceProvider
    from .providers.crunchbase import CrunchbaseProvider
    from .providers.sec_edgar import SECEdgarProvider
    from .providers.pappers import PappersProvider
    from .providers.companies_house import CompaniesHouseProvider
    from .providers.infogreffe import InfogreffeProvider
    from .providers.handelsregister import HandelsregisterProvider
    from .providers.kvk import KVKProvider
    from .providers.registro_mercantil import RegistroMercantilProvider

    registry = DataRegistry()

    # Tier 1 — enterprise (highest data quality; gated on credentials)
    registry.register(BloombergProvider())
    registry.register(CapitalIQProvider())
    registry.register(RefinitivProvider())

    # Tier 2 — free/freemium global
    registry.register(YFinanceProvider())
    registry.register(CrunchbaseProvider())
    registry.register(SECEdgarProvider())

    # Tier 3 — EU registries (geography routing promotes these for matching countries)
    registry.register(PappersProvider())
    registry.register(InfogreffeProvider())
    registry.register(CompaniesHouseProvider())
    registry.register(HandelsregisterProvider())
    registry.register(KVKProvider())
    registry.register(RegistroMercantilProvider())

    return registry


DEFAULT_REGISTRY: DataRegistry = build_default_registry()
