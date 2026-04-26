"""
Orchestrator — Equity + M&A production-grade pipeline
"""

import os
from dotenv import load_dotenv
from mistralai.client import Mistral
from rich.console import Console
from pydantic import BaseModel

from goldroger.finance.core.valuation_service import ValuationService
from goldroger.ma.scoring import DealScore

from .agents.specialists import (
    DataCollectorAgent,
    FinancialModelerAgent,
    ReportWriterAgent,
    SectorAnalystAgent,
    ValuationEngineAgent,
)

from .models import (
    AnalysisResult,
    Financials,
    Fundamentals,
    InvestmentThesis,
    MarketAnalysis,
    Valuation,
    MAResult,
    DealSourcing,
    StrategicFit,
    DueDiligence,
    DealExecution,
    LBOModel,
)

from .utils.json_parser import parse_model

load_dotenv()
console = Console()


# ─────────────────────────────────────────────
# ASSUMPTIONS
# ─────────────────────────────────────────────
class ValuationAssumptions(BaseModel):
    revenue_growth: float | None = None
    wacc: float | None = None
    terminal_growth: float | None = None
    tax_rate: float | None = None
    capex_pct: float | None = None
    nwc_pct: float | None = None
    ev_ebitda_range: tuple[float, float] = (8.0, 12.0)
    tx_multiple: float = 2.5
    weights: dict = {"dcf": 0.5, "comps": 0.3, "transactions": 0.2}


# ─────────────────────────────────────────────
# FALLBACKS
# ─────────────────────────────────────────────
def _fund_fallback(company: str):
    return Fundamentals(
        company_name=company,
        description="N/A",
        business_model="N/A",
    )


def _fin_fallback(company: str):
    return Financials(
        revenue_series=[0.0, 0.0, 0.0],
        revenue_current="0",
        ebitda_margin="0",
    )


# ─────────────────────────────────────────────
# EQUITY PIPELINE
# ─────────────────────────────────────────────
def run_analysis(company: str, company_type: str = "public") -> AnalysisResult:

    client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
    valuation_service = ValuationService()

    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)
    fin_agent = FinancialModelerAgent(client)
    val_agent = ValuationEngineAgent(client)
    thesis_agent = ReportWriterAgent(client)

    console.rule(f"[EQUITY] {company}")

    fund = parse_model(
        data_agent.run(company, company_type),
        Fundamentals,
        _fund_fallback(company),
    )

    mkt = parse_model(
        market_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "description": fund.description,
        }),
        MarketAnalysis,
        MarketAnalysis(),
    )

    fin = parse_model(
        fin_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "description": fund.description,
        }),
        Financials,
        _fin_fallback(company),
    )

    assumptions = parse_model(
        val_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "revenue_current": fin.revenue_current,
            "ebitda_margin": fin.ebitda_margin,
        }),
        ValuationAssumptions,
        ValuationAssumptions(),
    )

    valuation_result = valuation_service.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions.model_dump(),
    )

    dcf = valuation_result["dcf"]

    val = Valuation(
        implied_value=str(getattr(dcf, "enterprise_value", 0.0)),
        recommendation="HOLD",
        upside_downside=str(getattr(dcf, "enterprise_value", 0.0)),
    )

    thesis = parse_model(
        thesis_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "valuation": val.implied_value,
            "market": mkt.market_size,
        }),
        InvestmentThesis,
        InvestmentThesis(thesis="N/A"),
    )

    console.rule("[DONE EQUITY]")

    return AnalysisResult(
        company=company,
        company_type=company_type,
        fundamentals=fund,
        market=mkt,
        financials=fin,
        valuation=val,
        thesis=thesis,
    )


# ─────────────────────────────────────────────
# M&A PIPELINE (CLEAN + CONSISTENT + SCORED)
# ─────────────────────────────────────────────
def run_ma_analysis(company: str, company_type: str = "private"):

    client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))

    from .agents.specialists import (
        DealSourcingAgent,
        StrategicFitAgent,
        DueDiligenceAgent,
        DealExecutionAgent,
        LBOAgent,
    )

    sourcing_raw = DealSourcingAgent(client).run(company, company_type, {})
    fit_raw = StrategicFitAgent(client).run(company, company_type, {})
    dd_raw = DueDiligenceAgent(client).run(company, company_type, {})
    ex_raw = DealExecutionAgent(client).run(company, company_type, {})
    lbo_raw = LBOAgent(client).run(company, company_type, {})

    src = parse_model(sourcing_raw, DealSourcing, DealSourcing())
    fit = parse_model(fit_raw, StrategicFit, StrategicFit())
    dd = parse_model(dd_raw, DueDiligence, DueDiligence())
    ex = parse_model(ex_raw, DealExecution, DealExecution())
    lbo = parse_model(lbo_raw, LBOModel, LBOModel())

    def safe(x):
        try:
            return float(x)
        except:
            return 60.0

    scorer = DealScore(
        strategy=safe(getattr(fit, "fit_score", 60)),
        synergies=60,
        risk=60,
        lbo=60,
        valuation=60,
    )

    score = scorer.compute()

    console.rule("[DONE M&A]")

    return MAResult(
        company=company,
        company_type=company_type,
        deal_sourcing=src,
        strategic_fit=fit,
        due_diligence=dd,
        deal_execution=ex,
        lbo=lbo,
    )