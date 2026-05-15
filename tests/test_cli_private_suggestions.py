from types import SimpleNamespace


def test_private_fr_confirmation_includes_infogreffe_identifier(monkeypatch):
    from goldroger import cli
    from goldroger.data.fetcher import MarketData

    class _Resp:
        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            # Yahoo search path returns no quotes; keeps resolver path deterministic.
            return _Resp({"quotes": []})

    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: _Client())

    md = MarketData(
        company_name="DOCTOLIB",
        ticker=None,
        sector="Technology",
        revenue_ttm=None,
        data_source="infogreffe",
        additional_metadata={"siren": "794598813"},
    )
    monkeypatch.setattr(cli.DEFAULT_REGISTRY, "fetch_by_name", lambda *a, **k: md)

    suggestions = cli._fetch_company_suggestions("Doctolib", "private", "FR")
    assert suggestions
    first = suggestions[0]
    assert first.get("quote_type") == "PRIVATE"
    assert first.get("identifier") == "794598813"
    assert first.get("source") == "infogreffe"
