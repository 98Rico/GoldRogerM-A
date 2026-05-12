from goldroger.data.fetcher import MarketData
from goldroger.data.filings import FilingRecord, FilingsPack, build_filings_pack, classify_filing_url
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
    assert any((r.filing_type == "ANNUAL_REPORT") for r in pack.records)


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
    assert pack.source_backed is False
    assert pack.fallback_used is True
    assert pack.relevant_source_count == 0


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
    assert pack.source_backed is False
    assert pack.fallback_used is True
    assert pack.relevant_source_count >= 1


def test_market_context_relevance_filters_out_irrelevant_aapl_items(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr(
        "goldroger.data.market_context._extract_news_entries",
        lambda ticker, count=10: [
            {"title": "Fast food stocks rally on consumer traffic", "url": "https://news.example.com/fast-food", "source": "ExampleWire", "date": "2026-05-10"},
            {"title": "Alibaba earnings beat expectations", "url": "https://news.example.com/alibaba", "source": "ExampleWire", "date": "2026-05-10"},
            {"title": "Tower Semiconductor posts mixed quarter", "url": "https://news.example.com/tower", "source": "ExampleWire", "date": "2026-05-10"},
            {"title": "Apple updates App Store policy guidance", "url": "https://news.example.com/apple-app-store", "source": "ExampleWire", "date": "2026-05-10"},
        ],
    )
    filing = FilingRecord(
        filing_type="10-K",
        filing_date="2026-03-01",
        accession_number="0000320193-26-000001",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/test.htm",
        source_name="sec_edgar_submissions",
        confidence="verified",
    )
    pack = build_market_context_pack(
        company="Apple Inc.",
        ticker="AAPL_RELEVANCE",
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
    rendered = " ".join([x.text for x in [*pack.trends, *pack.catalysts, *pack.risks]]).lower()
    assert "fast food" not in rendered
    assert "alibaba" not in rendered
    assert "tower semiconductor" not in rendered
    assert "app store" in rendered
    assert pack.relevant_source_count >= 2
    assert pack.fetched_source_count >= 4
    assert pack.source_backed is True


def test_market_context_requires_at_least_two_relevant_sources(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr(
        "goldroger.data.market_context._extract_news_entries",
        lambda ticker, count=10: [
            {"title": "Apple launches new iPhone cycle", "url": "https://news.example.com/apple-launch", "source": "ExampleWire", "date": "2026-05-10"},
            {"title": "Global macro roundup", "url": "https://news.example.com/macro", "source": "ExampleWire", "date": "2026-05-10"},
        ],
    )
    pack = build_market_context_pack(
        company="Apple Inc.",
        ticker="AAPL_MIN_SRC",
        sector="Technology",
        industry="Consumer Electronics",
        filings_pack=None,
    )
    assert pack.relevant_source_count <= 1
    assert pack.source_backed is False
    assert pack.fallback_used is True


def test_market_context_accepts_tobacco_relevant_items(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr(
        "goldroger.data.market_context._extract_news_entries",
        lambda ticker, count=10: [
            {"title": "BAT pricing and nicotine category update", "url": "https://news.example.com/bat-pricing", "source": "ExampleWire", "date": "2026-05-10"},
            {"title": "PM and MO discuss reduced-risk product momentum", "url": "https://news.example.com/pm-mo-rrp", "source": "ExampleWire", "date": "2026-05-09"},
            {"title": "Excise tax proposals could pressure tobacco margins", "url": "https://news.example.com/excise", "source": "ExampleWire", "date": "2026-05-08"},
        ],
    )
    pack = build_market_context_pack(
        company="British American Tobacco p.l.c.",
        ticker="BATS.L_CTX",
        sector="Consumer Staples",
        industry="Tobacco",
        filings_pack=None,
    )
    rendered = " ".join([x.text for x in [*pack.trends, *pack.catalysts, *pack.risks]]).lower()
    assert "tobacco" in rendered or "nicotine" in rendered
    assert pack.relevant_source_count >= 2


def test_market_context_accepts_nhy_aluminum_relevant_items(monkeypatch):
    market_context_cache.clear()
    monkeypatch.setattr(
        "goldroger.data.market_context._extract_news_entries",
        lambda ticker, count=10: [
            {"title": "Norsk Hydro updates low-carbon aluminum and recycling strategy", "url": "https://news.example.com/hydro-recycling", "source": "ExampleWire", "date": "2026-05-10"},
            {"title": "LME aluminum pricing and energy costs remain key for margins", "url": "https://news.example.com/lme-energy", "source": "ExampleWire", "date": "2026-05-09"},
            {"title": "CBAM policy updates may affect European aluminum flows", "url": "https://news.example.com/cbam", "source": "ExampleWire", "date": "2026-05-08"},
        ],
    )
    pack = build_market_context_pack(
        company="Norsk Hydro ASA",
        ticker="NHY.OL_CTX",
        sector="Materials",
        industry="Aluminum",
        filings_pack=None,
    )
    rendered = " ".join([x.text for x in [*pack.trends, *pack.catalysts, *pack.risks]]).lower()
    assert any(k in rendered for k in ("aluminum", "aluminium", "lme", "recycling", "cbam"))
    assert pack.relevant_source_count >= 2


def test_filing_url_classification_examples():
    assert classify_filing_url("https://www.bat.com/investors-and-reporting/results-centre/consensus") == "CONSENSUS_PAGE"
    assert classify_filing_url("https://www.company.com/investors/annual-report-2025.pdf") == "ANNUAL_REPORT"
    assert classify_filing_url("https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/aapl-20250329-10q.htm") == "SEC_10Q"
    assert classify_filing_url("https://www.sec.gov/Archives/edgar/data/320193/000032019325000055/aapl-20241228-8k.htm") == "SEC_8K"
    assert classify_filing_url("https://www.company.com/news/first-quarter-2026-results") in {
        "QUARTERLY_REPORT",
        "PRESS_RELEASE",
        "RESULTS_CENTRE",
    }
