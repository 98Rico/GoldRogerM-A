from __future__ import annotations

from openpyxl import load_workbook

from goldroger.exporters.excel import generate_excel
from goldroger.exporters.pptx import _parse_ev_m, _result_currency
from goldroger.models import (
    AnalysisResult,
    DCFAssumptions,
    Financials,
    Fundamentals,
    InvestmentThesis,
    MarketAnalysis,
    Valuation,
    ValuationMethod,
)


def _analysis_with_currency(ccy: str) -> AnalysisResult:
    return AnalysisResult(
        company="TestCo",
        company_type="public",
        fundamentals=Fundamentals(
            company_name="TestCo plc",
            sector="Consumer Staples",
            description="Test description",
            business_model="Test business model",
        ),
        market=MarketAnalysis(
            market_size="N/A",
            market_growth="N/A",
            key_trends=[],
            main_competitors=[],
        ),
        financials=Financials(
            revenue_current=f"{ccy} 25610M",
            revenue_growth="+2.0%",
            ebitda_margin="33.0%",
            net_margin="18.0%",
            gross_margin="62.0%",
            free_cash_flow=f"{ccy} 3000M",
            projections=[],
            key_metrics=[],
            income_statement=[],
        ),
        valuation=Valuation(
            currency=ccy,
            current_price=f"{ccy} 35.00",
            implied_value=f"{ccy} 282.8B",
            target_price=f"{ccy} 112.54",
            upside_downside="+12.0%",
            recommendation="HOLD / LOW CONVICTION",
            dcf_assumptions=DCFAssumptions(wacc="9.0%", terminal_growth="2.5%", projection_years="5"),
            methods=[
                ValuationMethod(name="DCF", low="250000", mid="282800", high="320000", weight=60),
                ValuationMethod(name="Trading Comps", low="240000", mid="270000", high="300000", weight=40),
            ],
        ),
        thesis=InvestmentThesis(thesis="Test thesis"),
        data_quality={
            "pipeline_status": {
                "quote_currency": ccy,
            }
        },
    )


def test_excel_sheet_labels_use_run_currency(tmp_path):
    r = _analysis_with_currency("GBP")
    out = tmp_path / "analysis_gbp.xlsx"
    generate_excel(r, str(out))

    wb = load_workbook(out)
    assert "GBP" in str(wb["DCF Model"]["A2"].value)
    assert "(GBPM)" in str(wb["Dashboard"]["A21"].value)
    assert "GBPM" in str(wb["P&L"]["A2"].value)
    assert "GBPM" in str(wb["Balance Sheet"]["A2"].value)
    assert "GBPM" in str(wb["Cash Flow"]["A2"].value)


def test_ppt_ev_parser_handles_non_usd_prefixes():
    assert _parse_ev_m("GBP 282.8B") == 282800.0
    assert _parse_ev_m("NOK 405.1B") == 405100.0
    assert _parse_ev_m("$1.2T") == 1200000.0


def test_ppt_result_currency_prefers_pipeline_status_quote_currency():
    r = _analysis_with_currency("NOK")
    assert _result_currency(r) == "NOK"

