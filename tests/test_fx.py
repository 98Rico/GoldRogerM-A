from goldroger.data import fx as fx_mod
from goldroger.utils.cache import fx_rate_cache


def test_identity_fx_rate_is_1():
    out = fx_mod.get_fx_rate("USD", "USD")
    assert out.ok is True
    assert out.rate == 1.0
    assert out.source.source_name == "fx_identity"


def test_uses_cached_rate_when_live_unavailable(monkeypatch):
    fx_rate_cache.clear()
    fx_rate_cache.set(
        "fx:GBP->USD",
        {
            "rate": 1.255,
            "source_name": "frankfurter",
            "source_url": "https://api.frankfurter.dev/v2/rates?base=GBP&quotes=USD",
            "as_of_date": "2026-05-12",
        },
    )
    monkeypatch.setattr(fx_mod, "_from_frankfurter", lambda *_args, **_kwargs: None)
    out = fx_mod.get_fx_rate("GBP", "USD")
    assert out.ok is True
    assert out.rate == 1.255
    assert out.source.cached is True
    assert out.source.source_confidence == "medium"


def test_static_fallback_when_live_and_cache_missing(monkeypatch):
    fx_rate_cache.clear()
    monkeypatch.setattr(fx_mod, "_from_frankfurter", lambda *_args, **_kwargs: None)
    out = fx_mod.get_fx_rate("NOK", "USD")
    assert out.ok is True
    assert out.source.source_name == "static_fx_table"
    assert out.source.is_fallback is True
    assert out.source.source_confidence == "low"

