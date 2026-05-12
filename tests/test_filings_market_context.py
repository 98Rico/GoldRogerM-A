from goldroger.data.fetcher import MarketData
from goldroger.data.filings import FilingRecord, FilingsPack, build_filings_pack
from goldroger.data.market_context import build_market_context_pack
from goldroger.utils.cache import filings_meta_cache, market_context_cache


def _md_with_website(ticker: str = "BTI") -> MarketData:
    return MarketData(
        ticker=ticker,
        company_name="British American Tobacco p.l.c.",
        sector="Consumer Staples",
        current_price=35.0,
        market_cap=100000.0,
        shares_outstanding=2800.0,
        revenue_ttm=25000.0,
        additional_metadata={"website": "https://www.example.com"},
    )


def test_build_filings_pack_prefers_sec_and_caches(monkeypatch):
    filings_meta_cache.clear()
    rec = FilingRecord(
        filing_type="10-K",
        filing_date="2026-02-01",
        accession_number="0000320193-26-000001",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/test.htm",
        source_name="sec_edgar_submissions",
        confidence="verified",
    )

    monkeypatch.setattr("goldroger.data.filings._fetch_sec_recent_filings", lambda _t: [rec])
    pack = build_filings_pack(company="Apple Inc.", ticker="AAPL", market_data=None)
    assert pack.source_backed is True
    assert pack.source_count == 1
    assert pack.latest is not None
    assert pack.latest.filing_type == "10-K"

    # Cache should keep the prior result even if provider now returns empty.
    monkeypatch.setattr("goldroger.data.filings._fetch_sec_recent_filings", lambda _t: [])
    pack_cached = build_filings_pack(company="Apple Inc.", ticker="AAPL", market_data=None)
    assert pack_cached.latest is not None
    assert pack_cached.latest.filing_type == "10-K"


def test_build_filings_pack_uses_website_fallback(monkeypatch):
    filings_meta_cache.clear()
    monkeypatch.setattr("goldroger.data.filings._fetch_sec_recent_filings", lambda _t: [])
    monkeypatch.setattr("goldroger.data.filings._guess_ir_url", lambda _u: "https://www.example.com/investors")
    class _Resp:
        status_code = 200
        text = (
            '<html><body>'
            '<a href="/investors/annual-report-2025.pdf">Annual report</a>'
            '<a href="https://www.example.com/results/full-year-results">Results</a>'
            "</body></html>"
        )
    monkeypatch.setattr("goldroger.data.filings._HTTP.get", lambda *args, **kwargs: _Resp())

    pack = build_filings_pack(
        company="British American Tobacco",
        ticker="BTI",
        market_data=_md_with_website("BTI"),
    )
    assert pack.fallback_used is True
    assert pack.latest is not None
    assert pack.latest.filing_type == "IR_PROFILE"
    assert pack.latest.source_url == "https://www.example.com/investors"
    assert any((r.filing_type == "ANNUAL_REPORT_IR") for r in pack.records)


def test_market_context_pack_source_backed_from_news(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr(
        "goldroger.data.market_context._extract_news_entries",
        lambda ticker, count=10: [
            {
                "title": "Company reports earnings and raises guidance",
                "url": "https://news.example.com/earnings",
                "source": "ExampleWire",
                "date": "2026-05-10",
            },
            {
                "title": "Regulatory probe expands in key market",
                "url": "https://news.example.com/regulatory",
                "source": "ExampleWire",
                "date": "2026-05-09",
            },
        ],
    )
    pack = build_market_context_pack(
        company="Apple Inc.",
        ticker="AAPL_TEST",
        sector="Technology",
        industry="Consumer Electronics",
        filings_pack=None,
    )
    assert pack.source_backed is True
    assert pack.source_count >= 1
    assert any("guidance" in x.text.lower() for x in pack.catalysts)
    assert any("probe" in x.text.lower() for x in pack.risks)


def test_market_context_pack_falls_back_to_sector_profile(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr("goldroger.data.market_context._extract_news_entries", lambda ticker, count=10: [])
    pack = build_market_context_pack(
        company="British American Tobacco",
        ticker="BTI_TEST",
        sector="Consumer Staples",
        industry="Tobacco",
        filings_pack=None,
    )
    assert pack.source_backed is False
    assert pack.fallback_used is True
    assert pack.trends
    assert all(x.source == "sector_profile_fallback" for x in pack.trends)


def test_market_context_pack_uses_filings_anchor_without_news(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr("goldroger.data.market_context._extract_news_entries", lambda ticker, count=10: [])
    filing = FilingRecord(
        filing_type="10-K",
        filing_date="2026-03-01",
        accession_number="0000123456-26-000001",
        source_url="https://www.sec.gov/Archives/edgar/data/123456/abcd.htm",
        source_name="sec_edgar_submissions",
        confidence="verified",
    )
    pack = build_market_context_pack(
        company="Apple Inc.",
        ticker="AAPL_FILING_ONLY",
        sector="Technology",
        industry="Consumer Electronics",
        filings_pack=FilingsPack(
            company="Apple Inc.",
            ticker="AAPL",
            source_backed=True,
            source_count=1,
            records=[filing],
        ),
    )
    assert pack.source_backed is True
    assert pack.source_count >= 1
    assert any("Latest 10-K filing" in x.text for x in pack.trends)
