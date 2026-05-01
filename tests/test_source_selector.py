"""Tests for CLI/non-interactive source selection behavior."""

from goldroger.data.source_selector import (
    provider_names,
    provider_table,
    resolve_source_selection,
)


def test_provider_names_contains_core_sources():
    names = set(provider_names())
    assert "infogreffe" in names
    assert "sec_edgar" in names
    assert "crunchbase" in names


def test_provider_table_no_country_returns_all():
    rows = provider_table()
    names = {r["name"] for r in rows}
    # When country is unknown, we should expose the full list.
    assert "infogreffe" in names
    assert "companies_house" in names
    assert "handelsregister" in names
    assert "sec_edgar" in names


def test_resolve_auto_skips_missing_credentials(monkeypatch):
    monkeypatch.delenv("PAPPERS_API_KEY", raising=False)
    monkeypatch.delenv("CRUNCHBASE_API_KEY", raising=False)
    monkeypatch.delenv("BLOOMBERG_API_KEY", raising=False)
    monkeypatch.delenv("CAPITALIQ_USERNAME", raising=False)

    sel = resolve_source_selection(["auto"], country_hint="FR")
    # FR local free source should be selected.
    assert "infogreffe" in sel.selected_providers
    # Credential-gated sources should be skipped when key is absent.
    assert "pappers" in sel.skipped_missing_credentials
    assert "crunchbase" in sel.skipped_missing_credentials
    # Premium stubs are excluded from auto mode.
    assert "bloomberg" not in sel.requested_sources
    assert "capitaliq" not in sel.requested_sources


def test_resolve_explicit_records_unknown_and_skips(monkeypatch):
    monkeypatch.delenv("PAPPERS_API_KEY", raising=False)
    sel = resolve_source_selection(
        ["infogreffe", "pappers", "does_not_exist"],
        country_hint="FR",
    )
    assert "infogreffe" in sel.selected_providers
    assert "pappers" in sel.skipped_missing_credentials
    assert "does_not_exist" in sel.unknown_sources
