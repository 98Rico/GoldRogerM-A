"""Tests for data providers — availability gating and response parsing."""
import pytest
from unittest.mock import patch, MagicMock


# ── Infogreffe (French gov open data — no key required) ───────────────────────

def test_infogreffe_always_available():
    from goldroger.data.providers.infogreffe import InfogreffeProvider
    assert InfogreffeProvider().is_available() is True


def test_infogreffe_fetch_returns_none_on_network_error(monkeypatch):
    from goldroger.data.providers.infogreffe import InfogreffeProvider

    with patch("httpx.Client.get", side_effect=Exception("network error")):
        result = InfogreffeProvider().fetch_by_name("Unknown Corp XYZ")
    assert result is None


# ── Pappers ───────────────────────────────────────────────────────────────────

def test_pappers_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("PAPPERS_API_KEY", raising=False)
    from goldroger.data.providers import pappers
    # Reload to pick up cleared env
    import importlib
    importlib.reload(pappers)
    from goldroger.data.providers.pappers import PappersProvider
    assert PappersProvider().is_available() is False


def test_pappers_available_with_key(monkeypatch):
    monkeypatch.setenv("PAPPERS_API_KEY", "test-key-456")
    from goldroger.data.providers.pappers import PappersProvider
    assert PappersProvider().is_available() is True


def test_pappers_fetch_by_siren_returns_none_on_404(monkeypatch):
    monkeypatch.setenv("PAPPERS_API_KEY", "test-key-456")
    from goldroger.data.providers.pappers import PappersProvider

    fake_response = MagicMock()
    fake_response.status_code = 404

    with patch("httpx.Client.get", return_value=fake_response):
        result = PappersProvider().fetch_by_siren("000000000")
    assert result is None


# ── Crunchbase ────────────────────────────────────────────────────────────────

def test_crunchbase_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("CRUNCHBASE_API_KEY", raising=False)
    from goldroger.data.providers.crunchbase import CrunchbaseProvider
    assert CrunchbaseProvider().is_available() is False


def test_crunchbase_available_with_key(monkeypatch):
    monkeypatch.setenv("CRUNCHBASE_API_KEY", "test-cb-key")
    from goldroger.data.providers.crunchbase import CrunchbaseProvider
    assert CrunchbaseProvider().is_available() is True


# ── SEC EDGAR (no key required) ───────────────────────────────────────────────

def test_sec_edgar_always_available():
    from goldroger.data.providers.sec_edgar import SECEdgarProvider
    assert SECEdgarProvider().is_available() is True
