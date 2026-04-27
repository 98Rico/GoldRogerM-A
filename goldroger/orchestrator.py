"""
Orchestrator — Equity analysis pipeline.

Data flow:
  1. Ticker resolution + yfinance fetch (verified financial data)
  2. Fundamentals agent   (qualitative — LLM enriches real data)
  3. Market analysis agent
  4. Financials agent     (only for private companies or when yfinance fails)
  5. Assumptions agent    (WACC / multiples guidance)
  6. Valuation engine     (deterministic — CAPM WACC, sector multiples, DCF)
  7. Thesis agent         (LLM synthesis of verified numbers)
"""
from __future__ import annotations

import os
import time
from dotenv import load_dotenv
from mistralai.client import Mistral
from rich.console import Console
from pydantic import BaseModel

from goldroger.data.fetcher import fetch_market_data, resolve_ticker, MarketData
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
    ValuationMethod,
    DCFAssumptions,
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
def _fund_fallback(company: str) -> Fundamentals:
    return Fundamentals(
        company_name=company,
        description="N/A",
        business_model="N/A",
    )


def _fin_fallback() -> Financials:
    return Financials(
        revenue_series=[0.0, 0.0, 0.0],
        revenue_current="0",
        ebitda_margin="0",
    )


def _fin_from_market(md: MarketData) -> Financials:
    """Build a verified Financials object from real yfinance data."""
    return Financials(
        revenue_series=md.revenue_history or [],
        revenue_current=str(md.revenue_ttm or 0.0),
        ebitda_margin=str(md.ebitda_margin or 0.0),
        net_margin=str(md.net_margin or 0.0),
        gross_margin=str(md.gross_margin or 0.0),
        debt_to_equity=str(md.total_debt / md.market_cap if md.total_debt and md.market_cap else 0.0),
        free_cash_flow=str(md.fcf_ttm or 0.0),
        sources=["yfinance (verified)"],
    )


# ─────────────────────────────────────────────
# TIMER
# ─────────────────────────────────────────────
def step(name: str) -> float:
    console.rule(f"[bold cyan]{name}")
    return time.time()


def done(name: str, start: float) -> None:
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

    # ───────── 0. REAL DATA FETCH (public companies) ─────────
    market_data: MarketData | None = None

    if company_type == "public":
        t0 = step("Market Data (yfinance)")
        ticker = resolve_ticker(company)
        if ticker:
            console.print(f"  Resolved ticker: [bold]{ticker}[/bold]")
            market_data = fetch_market_data(ticker)
            if market_data:
                console.print(
                    f"  [green]Verified data loaded[/green]: "
                    f"Rev=${market_data.revenue_ttm:.0f}M  "
                    f"EBITDA margin={market_data.ebitda_margin:.1%}  "
                    f"β={market_data.beta}  "
                    f"Market cap=${market_data.market_cap:.0f}M"
                )
            else:
                console.print(f"  [yellow]yfinance returned no data for {ticker}[/yellow]")
        else:
            console.print(f"  [yellow]Could not resolve ticker for '{company}'[/yellow]")
        done("Market Data", t0)

    # ───────── 1. FUNDAMENTALS ─────────
    t0 = step("Fundamentals")

    fund_raw = data_agent.run(company, company_type)
    fund = parse_model(fund_raw, Fundamentals, _fund_fallback(company))

    # Back-fill ticker from real data if LLM missed it
    if market_data and not fund.ticker:
        fund.ticker = market_data.ticker
    if market_data and not fund.sector:
        fund.sector = market_data.sector

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

    if market_data:
        # Use verified yfinance data; skip LLM financial extraction
        fin = _fin_from_market(market_data)
        console.print("  [green]Using verified yfinance financials[/green]")
    else:
        fin_raw = fin_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "description": fund.description,
        })
        fin = parse_model(fin_raw, Financials, _fin_fallback())

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

    result = valuation_service.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions.model_dump(),
        market_data=market_data,
        sector=fund.sector or "",
    )

    blended_ev = result.blended.blended
    rec = result.recommendation

    # Build rich Valuation model
    val = Valuation(
        current_price=str(rec.current_price) if rec.current_price else None,
        implied_value=str(round(blended_ev, 1)),
        upside_downside=(
            f"{rec.upside_pct:+.1%}" if rec.upside_pct is not None else "N/A"
        ),
        recommendation=rec.recommendation,
        dcf_assumptions=DCFAssumptions(
            wacc=f"{result.wacc_used:.2%}",
            terminal_growth=f"{result.terminal_growth_used:.2%}",
            projection_years="5",
        ),
        methods=[
            ValuationMethod(
                name="DCF",
                mid=str(round(result.dcf.enterprise_value, 1)),
                weight=50,
            ),
            ValuationMethod(
                name="Trading Comps (EV/EBITDA)",
                low=str(round(result.comps.low, 1)),
                mid=str(round(result.comps.mid, 1)),
                high=str(round(result.comps.high, 1)),
                weight=30,
            ),
            ValuationMethod(
                name="Transaction Comps (EV/Revenue)",
                mid=str(round(result.transactions.implied_value, 1)),
                weight=20,
            ),
        ],
        sources=[result.data_confidence],
    )

    if result.notes:
        console.print("[dim]Notes:[/dim]")
        for note in result.notes:
            console.print(f"  [dim]• {note}[/dim]")

    done("Valuation Engine", t0)

    # ───────── 6. THESIS ─────────
    t0 = step("Investment Thesis")

    thesis = parse_model(
        thesis_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "valuation": val.implied_value,
            "recommendation": val.recommendation,
            "upside": val.upside_downside,
            "wacc": result.wacc_used,
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
