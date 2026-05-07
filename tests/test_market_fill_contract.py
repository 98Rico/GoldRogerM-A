from goldroger.models import (
    AnalysisResult,
    Financials,
    Fundamentals,
    InvestmentThesis,
    MarketAnalysis,
    Valuation,
)
from goldroger.pipelines.fill_gaps import fill_gaps


def test_fill_gaps_market_does_not_invent_tam_or_growth_numbers():
    result = AnalysisResult(
        company="TEST",
        company_type="public",
        fundamentals=Fundamentals(
            company_name="Test Co",
            description="Test",
            business_model="Test",
            sector="Technology",
        ),
        market=MarketAnalysis(),
        financials=Financials(),
        valuation=Valuation(),
        thesis=InvestmentThesis(thesis="Test thesis"),
    )
    out = fill_gaps(result, "Technology")
    assert out.market.market_size == "Not available from current queries"
    assert out.market.market_growth == "Not available from current queries"
