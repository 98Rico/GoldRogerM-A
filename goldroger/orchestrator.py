"""
Orchestrator — Equity + M&A clean architecture (STABLE VERSION)
Fix: valuation object access + safer pipeline + faster failure handling
"""

import os
import time
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
# TIMER
# ─────────────────────────────────────────────
def step(name: str):
    console.rule(f"[bold cyan]{name}")
    return time.time()


def done(name: str, start: float):
    console.print(f"[green]✓ {name} done in {round(time.time() - start, 2)}s")


# ─────────────────────────────────────────────
# EQUITY PIPELINE
# ─────────────────────────────────────────────
def run_analysis(company: str, company_type: str = "public") -> AnalysisResult:

    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("Missing MISTRAL_API_KEY")

    client = Mistral(api_key=api_key)
    valuation_service = ValuationService()

    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)
    fin_agent = FinancialModelerAgent(client)
    val_agent = ValuationEngineAgent(client)
    thesis_agent = ReportWriterAgent(client)

    console.rule(f"[EQUITY] {company}")

    # ───────── 1. FUNDAMENTALS ─────────
    t0 = step("Fundamentals")

    fund_raw = data_agent.run(company, company_type)
    fund = parse_model(fund_raw, Fundamentals, _fund_fallback(company))

    done("Fundamentals", t0)

    # ───────── 2. MARKET ─────────
    t0 = step("Market")

    mkt_raw = market_agent.run(company, company_type, {
        "sector": fund.sector or "",
        "description": fund.description,
    })

    mkt = parse_model(mkt_raw, MarketAnalysis, MarketAnalysis())

    done("Market", t0)

    # ───────── 3. FINANCIALS ─────────
    t0 = step("Financials")

    fin_raw = fin_agent.run(company, company_type, {
        "sector": fund.sector or "",
        "description": fund.description,
    })

    fin = parse_model(fin_raw, Financials, _fin_fallback(company))

    done("Financials", t0)

    # ───────── 4. ASSUMPTIONS ─────────
    t0 = step("Assumptions")

    assumptions = parse_model(
        val_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "revenue_current": fin.revenue_current,
            "ebitda_margin": fin.ebitda_margin,
        }),
        ValuationAssumptions,
        ValuationAssumptions(),
    )

    done("Assumptions", t0)

    # ───────── 5. VALUATION ENGINE ─────────
    t0 = step("Valuation Engine")

    valuation_result = valuation_service.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions.model_dump(),
    )

    # ✅ FIX IMPORTANT : object access (NOT dict)
    dcf = valuation_result.dcf

    val = Valuation(
        implied_value=str(dcf.enterprise_value),
        recommendation="HOLD",
        upside_downside=str(dcf.enterprise_value),
    )

    done("Valuation Engine", t0)

    # ───────── 6. THESIS ─────────
    t0 = step("Investment Thesis")

    thesis = parse_model(
        thesis_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "valuation": val.implied_value,
            "market": mkt.market_size,
        }),
        InvestmentThesis,
        InvestmentThesis(thesis="N/A"),
    )

    done("Investment Thesis", t0)

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