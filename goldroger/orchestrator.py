"""
Orchestrator — coordinates the 5 agents in sequence,
passes context between them, assembles the final AnalysisResult.
"""

import os

from dotenv import load_dotenv
from mistralai.client import Mistral
from rich.console import Console
from pydantic import BaseModel

from goldroger.finance.core.valuation_service import ValuationService

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
)

from .utils.json_parser import parse_model

load_dotenv()
console = Console()


# ─────────────────────────────────────────────
# TYPES (STRICT INPUTS FOR DCF ENGINE)
# ─────────────────────────────────────────────
class ValuationAssumptions(BaseModel):
    revenue_growth: float | None = None
    wacc: float | None = None
    terminal_growth: float | None = None
    tax_rate: float | None = None
    capex_pct: float | None = None
    nwc_pct: float | None = None


# ─────────────────────────────────────────────
# FALLBACK
# ─────────────────────────────────────────────
def _make_fundamentals_fallback(company: str) -> Fundamentals:
    return Fundamentals(
        company_name=company,
        description="Analysis unavailable.",
        business_model="Data could not be retrieved.",
    )


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
def run_analysis(company: str, company_type: str = "public") -> AnalysisResult:
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("Missing MISTRAL_API_KEY")

    client = Mistral(api_key=api_key)
    valuation_service = ValuationService()

    # ── Agents ─────────────────────────────
    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)
    fin_agent = FinancialModelerAgent(client)
    val_agent = ValuationEngineAgent(client)
    thesis_agent = ReportWriterAgent(client)

    console.rule(f"[bold green]Gold Roger — {company}")

    # ─────────────────────────────
    # 1. FUNDAMENTALS
    # ─────────────────────────────
    raw_fund = data_agent.run(company, company_type)
    fund = parse_model(raw_fund, Fundamentals, _make_fundamentals_fallback(company))

    console.print(f"✓ Fundamentals: {fund.company_name}")

    # ─────────────────────────────
    # 2. MARKET
    # ─────────────────────────────
    raw_market = market_agent.run(company, company_type, {
        "sector": fund.sector or "",
        "description": fund.description,
    })

    mkt = parse_model(raw_market, MarketAnalysis, MarketAnalysis())
    console.print(f"✓ Market: {mkt.market_size}")

    # ─────────────────────────────
    # 3. FINANCIALS
    # ─────────────────────────────
    raw_fin = fin_agent.run(company, company_type, {
        "sector": fund.sector or "",
        "description": fund.description,
    })

    fin = parse_model(raw_fin, Financials, Financials())
    console.print(f"✓ Financials: {fin.revenue_current}")

    # ─────────────────────────────
    # 4A. ASSUMPTIONS (LLM ONLY)
    # ─────────────────────────────
    raw_assumptions = val_agent.run(company, company_type, {
        "sector": fund.sector or "",
        "revenue_current": fin.revenue_current,
        "ebitda_margin": fin.ebitda_margin,
    })

    assumptions = parse_model(
        raw_assumptions,
        ValuationAssumptions,
        ValuationAssumptions()
    )

    console.print("✓ Assumptions generated")

    # ─────────────────────────────
    # 4B. DETERMINISTIC VALUATION ENGINE
    # ─────────────────────────────
    valuation_result = valuation_service.run_full_valuation(
        financials=fin.model_dump() if hasattr(fin, "model_dump") else fin.dict(),
        assumptions=assumptions.model_dump() if hasattr(assumptions, "model_dump") else assumptions.dict(),
    )

    dcf = valuation_result["dcf"]

    val = Valuation(
        implied_value=dcf.enterprise_value,
        recommendation="HOLD",
        upside_downside=f"DCF EV: {dcf.enterprise_value}",
    )

    console.print(f"✓ Valuation complete: {val.implied_value}")

    # ─────────────────────────────
    # 5. INVESTMENT THESIS (LLM ONLY)
    # ─────────────────────────────
    raw_thesis = thesis_agent.run(company, company_type, {
        "sector": fund.sector or "",
        "valuation": val.implied_value,
        "market": mkt.market_size,
    })

    thesis = parse_model(
        raw_thesis,
        InvestmentThesis,
        InvestmentThesis(thesis="N/A")
    )

    console.rule("[bold green]Analysis complete")

    return AnalysisResult(
        company=company,
        company_type=company_type,
        fundamentals=fund,
        market=mkt,
        financials=fin,
        valuation=val,
        thesis=thesis,
    )