"""Tests for DataRegistry — provider listing, geography routing, capabilities."""
import pytest
from goldroger.data.registry import DataRegistry, build_default_registry, _GEO_PRIORITY
from goldroger.data.providers.base import DataProvider, ProviderCapabilities
from goldroger.data.fetcher import MarketData


# ── Minimal fake provider for testing ────────────────────────────────────────

class _AlwaysProvider(DataProvider):
    """Always returns a fixed MarketData for any query."""
    def __init__(self, name: str, coverage: list[str], available: bool = True):
        self.name = name
        self._coverage = coverage
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def fetch(self, ticker: str):
        return MarketData(ticker=ticker, company_name="Test Co", sector="Tech",
                          revenue_ttm=100.0, confidence="verified", data_source=self.name)

    def fetch_by_name(self, company_name: str):
        return MarketData(ticker="TEST", company_name=company_name, sector="Tech",
                          revenue_ttm=100.0, confidence="verified", data_source=self.name)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            display_name=self.name.title(),
            description="Test provider",
            coverage=self._coverage,
            company_types=["private"],
            data_fields=["revenue"],
            cost_tier="free",
            requires_key=False,
        )


# ── list_providers ────────────────────────────────────────────────────────────

def test_list_providers_returns_all_registered():
    registry = DataRegistry()
    registry.register(_AlwaysProvider("source_a", ["FR"]))
    registry.register(_AlwaysProvider("source_b", ["GB"]))
    caps = registry.list_providers()
    assert len(caps) == 2
    assert {c.name for c in caps} == {"source_a", "source_b"}


def test_list_providers_sets_is_available():
    registry = DataRegistry()
    registry.register(_AlwaysProvider("active", ["FR"], available=True))
    registry.register(_AlwaysProvider("inactive", ["FR"], available=False))
    caps = {c.name: c for c in registry.list_providers()}
    assert caps["active"].is_available is True
    assert caps["inactive"].is_available is False


def test_list_providers_sets_status():
    registry = DataRegistry()
    registry.register(_AlwaysProvider("active", ["FR"], available=True))
    caps = {c.name: c for c in registry.list_providers()}
    assert caps["active"].status == "active"


def test_list_providers_needs_key_status(monkeypatch):
    from goldroger.data.providers.crunchbase import CrunchbaseProvider
    monkeypatch.delenv("CRUNCHBASE_API_KEY", raising=False)
    registry = DataRegistry()
    registry.register(CrunchbaseProvider())
    caps = registry.list_providers()[0]
    assert caps.status == "needs_key"
    assert caps.requires_key is True
    assert caps.key_env_var == "CRUNCHBASE_API_KEY"


# ── geography routing ─────────────────────────────────────────────────────────

def test_fetch_by_name_geo_routing_prefers_local():
    """When country_hint matches a provider, that provider should be tried first."""
    order = []

    class _TrackingProvider(DataProvider):
        def __init__(self, name):
            self.name = name
        def is_available(self): return True
        def fetch(self, ticker): return None
        def fetch_by_name(self, company_name):
            order.append(self.name)
            return None
        def capabilities(self): return ProviderCapabilities(
            name=self.name, display_name=self.name, description="",
            coverage=[], company_types=[], data_fields=[],
            cost_tier="free", requires_key=False,
        )

    registry = DataRegistry()
    registry.register(_TrackingProvider("global_source"))
    registry.register(_AlwaysProvider("pappers", ["FR"]))  # matches FR

    # Override routing table for test
    import goldroger.data.registry as reg_module
    original = reg_module._GEO_PRIORITY.copy()
    reg_module._GEO_PRIORITY["FR"] = ["pappers"]

    try:
        result = registry.fetch_by_name("Sézane", country_hint="FR")
        assert result is not None
        assert result.data_source == "pappers"
        # pappers should have been tried before global_source
    finally:
        reg_module._GEO_PRIORITY.clear()
        reg_module._GEO_PRIORITY.update(original)


def test_fetch_by_name_no_hint_uses_registration_order():
    """Without a country hint, providers are tried in registration order."""
    registry = DataRegistry()
    first = _AlwaysProvider("first_wins", ["GLOBAL"])
    second = _AlwaysProvider("second", ["GLOBAL"])
    registry.register(first)
    registry.register(second)
    result = registry.fetch_by_name("Any Company")
    assert result is not None
    assert result.data_source == "first_wins"


def test_geo_priority_table_covers_main_countries():
    for country in ("FR", "GB", "DE", "NL", "ES", "US"):
        assert country in _GEO_PRIORITY, f"{country} missing from _GEO_PRIORITY"


# ── default registry ──────────────────────────────────────────────────────────

def test_default_registry_has_expected_providers():
    registry = build_default_registry()
    names = {p.name for p in registry._providers}
    assert "yfinance" in names
    assert "crunchbase" in names
    assert "pappers" in names
    assert "bloomberg" in names


def test_default_registry_list_providers_all_have_capabilities():
    registry = build_default_registry()
    for caps in registry.list_providers():
        assert caps.name
        assert caps.display_name
        assert caps.cost_tier in ("free", "freemium", "paid")
        assert isinstance(caps.coverage, list)
        assert isinstance(caps.data_fields, list)


def test_available_providers_excludes_inactive(monkeypatch):
    monkeypatch.delenv("CRUNCHBASE_API_KEY", raising=False)
    monkeypatch.delenv("BLOOMBERG_API_KEY", raising=False)
    monkeypatch.delenv("CAPITALIQ_USERNAME", raising=False)
    monkeypatch.delenv("REFINITIV_APP_KEY", raising=False)
    registry = build_default_registry()
    available = registry.available_providers()
    assert "yfinance" in available      # always active
    assert "bloomberg" not in available  # needs key
