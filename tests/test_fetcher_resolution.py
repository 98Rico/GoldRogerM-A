from goldroger.data.fetcher import fetch_market_data, resolve_ticker_with_context
from goldroger.utils.cache import market_data_cache


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def test_resolver_prefers_local_primary_listing_for_foreign_name_query(monkeypatch):
    payload = {
        "quotes": [
            {
                "symbol": "BTI",
                "quoteType": "EQUITY",
                "exchange": "NYQ",
                "region": "US",
                "shortname": "British American Tobacco plc ADR",
                "longname": "British American Tobacco p.l.c.",
            },
            {
                "symbol": "BATS.L",
                "quoteType": "EQUITY",
                "exchange": "LSE",
                "region": "GB",
                "shortname": "British American Tobacco",
                "longname": "British American Tobacco p.l.c.",
            },
        ]
    }
    monkeypatch.setattr("goldroger.data.fetcher._HTTP.get", lambda *args, **kwargs: _FakeResp(payload))
    ctx = resolve_ticker_with_context("British American Tobacco")
    assert ctx is not None
    assert ctx["selected_symbol"] == "BATS.L"
    assert ctx["primary_listing_symbol"] == "BATS.L"


def test_resolver_preserves_explicit_ticker_for_us_input(monkeypatch):
    payload = {
        "quotes": [
            {
                "symbol": "AAPL",
                "quoteType": "EQUITY",
                "exchange": "NMS",
                "region": "US",
                "shortname": "Apple Inc.",
                "longname": "Apple Inc.",
            },
            {
                "symbol": "AAPL34.BA",
                "quoteType": "EQUITY",
                "exchange": "BUE",
                "region": "AR",
                "shortname": "Apple Inc. CEDEAR",
                "longname": "Apple Inc. CEDEAR",
            },
        ]
    }
    monkeypatch.setattr("goldroger.data.fetcher._HTTP.get", lambda *args, **kwargs: _FakeResp(payload))
    ctx = resolve_ticker_with_context("AAPL")
    assert ctx is not None
    assert ctx["selected_symbol"] == "AAPL"


def test_resolver_prefers_nhy_local_primary_listing_for_name_query(monkeypatch):
    payload = {
        "quotes": [
            {
                "symbol": "NHYDY",
                "quoteType": "EQUITY",
                "exchange": "OTC",
                "region": "US",
                "shortname": "Norsk Hydro ASA ADR",
                "longname": "Norsk Hydro ASA",
            },
            {
                "symbol": "NHYKF",
                "quoteType": "EQUITY",
                "exchange": "OTC",
                "region": "US",
                "shortname": "Norsk Hydro ASA F",
                "longname": "Norsk Hydro ASA",
            },
            {
                "symbol": "NHY.OL",
                "quoteType": "EQUITY",
                "exchange": "OSL",
                "region": "NO",
                "shortname": "Norsk Hydro ASA",
                "longname": "Norsk Hydro ASA",
            },
        ]
    }
    monkeypatch.setattr("goldroger.data.fetcher._HTTP.get", lambda *args, **kwargs: _FakeResp(payload))
    ctx = resolve_ticker_with_context("Norsk Hydro")
    assert ctx is not None
    assert ctx["selected_symbol"] == "NHY.OL"
    assert ctx["primary_listing_symbol"] == "NHY.OL"


def test_resolver_preserves_explicit_adr_symbol_when_user_inputs_it(monkeypatch):
    payload = {
        "quotes": [
            {
                "symbol": "NHYDY",
                "quoteType": "EQUITY",
                "exchange": "OTC",
                "region": "US",
                "shortname": "Norsk Hydro ASA ADR",
                "longname": "Norsk Hydro ASA",
            },
            {
                "symbol": "NHY.OL",
                "quoteType": "EQUITY",
                "exchange": "OSL",
                "region": "NO",
                "shortname": "Norsk Hydro ASA",
                "longname": "Norsk Hydro ASA",
            },
        ]
    }
    monkeypatch.setattr("goldroger.data.fetcher._HTTP.get", lambda *args, **kwargs: _FakeResp(payload))
    ctx = resolve_ticker_with_context("NHYDY")
    assert ctx is not None
    assert ctx["selected_symbol"] == "NHYDY"
    assert ctx["primary_listing_symbol"] == "NHY.OL"


def test_fetch_market_data_does_not_print_raw_http_errors(monkeypatch, capsys):
    class _BoomTicker:
        @property
        def info(self):
            raise RuntimeError("HTTP Error 404: Quote not found for symbol: DAY")

    market_data_cache.clear()
    monkeypatch.setattr("goldroger.data.fetcher.yf.Ticker", lambda _symbol: _BoomTicker())
    result = fetch_market_data("DAY")
    captured = capsys.readouterr()
    assert result is None
    assert "HTTP Error 404" not in captured.out
