"""
Orchestrator — Equity + M&A analysis pipelines.

Equity flow:
  0. Ticker resolution + yfinance fetch (verified data)
  1. Fundamentals agent   (qualitative)
  2. Market analysis agent
  3. Financials agent     (private companies / yfinance fallback)
  4. Assumptions agent    (WACC / multiples guidance)
  5. Valuation engine     (deterministic — CAPM, sector multiples, DCF, LBO)
  6. Thesis agent         (LLM synthesis on verified numbers)

M&A flow:
  0. Ticker + yfinance fetch for target (when public)
  1. Deal sourcing agent
  2. Strategic fit agent
  3. Due diligence agent
  4. Deal execution agent
  5. LBO agent (qualitative) + LBO engine (deterministic)
  6. IC scoring (auto + LLM scores merged)

Pipeline flow:
  0. Pipeline builder agent
  1. Light valuation per target (sector multiples, no full DCF)
  2. IC scoring per target
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
from goldroger.ma.scoring import auto_score_from_valuation, score_from_analysis
from goldroger.utils.logger import new_run

from .agents.specialists import (
    DataCollectorAgent,
    DealExecutionAgent,
    DealSourcingAgent,
    DueDiligenceAgent,
    FinancialModelerAgent,
    LBOAgent,
    PipelineBuilderAgent,
    ReportWriterAgent,
    SectorAnalystAgent,
    StrategicFitAgent,
    ValuationEngineAgent,
)

from .models import (
    AnalysisResult,
    AcquisitionPipeline,
    DCFAssumptions,
    DealExecution,
    DealSourcing,
    DueDiligence,
    Financials,
    Fundamentals,
    InvestmentThesis,
    LBOModel,
    MAResult,
    MarketAnalysis,
    StrategicFit,
    Valuation,
    ValuationMethod,
)

from .utils.json_parser import parse_model

load_dotenv()
console = Console()


# ─────────────────────────────────────────────
# ASSUMPTION MODEL
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
    return Fundamentals(company_name=company, description="N/A", business_model="N/A")


def _fin_fallback() -> Financials:
    return Financials(revenue_series=[0.0], revenue_current="0", ebitda_margin="0")


def _fin_from_market(md: MarketData) -> Financials:
    """Build verified Financials from yfinance data."""
    d_e = str(md.total_debt / md.market_cap) if md.total_debt and md.market_cap else "0"
    return Financials(
        revenue_series=md.revenue_history or [],
        revenue_current=str(md.revenue_ttm or 0.0),
        ebitda_margin=str(md.ebitda_margin or 0.0),
        net_margin=str(md.net_margin or 0.0),
        gross_margin=str(md.gross_margin or 0.0),
        debt_to_equity=d_e,
        free_cash_flow=str(md.fcf_ttm or 0.0),
        sources=["yfinance (verified)"],
    )


# ─────────────────────────────────────────────
# TIMER HELPERS
# ─────────────────────────────────────────────
def _step(name: str) -> float:
    console.rule(f"[bold cyan]{name}")
    return time.time()


def _done(name: str, start: float) -> float:
    elapsed = round(time.time() - start, 2)
    console.print(f"[green]✓ {name} done in {elapsed}s")
    return elapsed


def _client() -> Mistral:
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("Missing MISTRAL_API_KEY")
    return Mistral(api_key=api_key)


# ─────────────────────────────────────────────
# EQUITY PIPELINE
# ─────────────────────────────────────────────
def run_analysis(company: str, company_type: str = "public") -> AnalysisResult:
    log = new_run(company, company_type)
    client = _client()
    svc = ValuationService()

    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)
    fin_agent = FinancialModelerAgent(client)
    val_agent = ValuationEngineAgent(client)
    thesis_agent = ReportWriterAgent(client)

    console.rule(f"[EQUITY] {company}")

    # ── 0. REAL DATA ──────────────────────────────────────────────────────
    market_data: MarketData | None = None
    if company_type == "public":
        t0 = _step("Market Data (yfinance)")
        ticker = resolve_ticker(company)
        if ticker:
            log.ticker = ticker
            console.print(f"  Resolved ticker: [bold]{ticker}[/bold]")
            market_data = fetch_market_data(ticker)
            if market_data:
                console.print(
                    f"  [green]Verified[/green] Rev=${market_data.revenue_ttm:.0f}M "
                    f"EBITDA={market_data.ebitda_margin:.1%} β={market_data.beta} "
                    f"MCap=${market_data.market_cap:.0f}M"
                )
                if market_data.forward_revenue_growth is not None:
                    console.print(
                        f"  [cyan]Forward growth: {market_data.forward_revenue_growth:+.1%}[/cyan]"
                    )
        log.end_step("market_data", t0)
        _done("Market Data", t0)

    # ── 1. FUNDAMENTALS ───────────────────────────────────────────────────
    t0 = _step("Fundamentals")
    fund = parse_model(data_agent.run(company, company_type), Fundamentals, _fund_fallback(company))
    if market_data:
        if not fund.ticker:
            fund.ticker = market_data.ticker
        if not fund.sector:
            fund.sector = market_data.sector
    log.end_step("fundamentals", t0)
    _done("Fundamentals", t0)

    # ── 2. MARKET ─────────────────────────────────────────────────────────
    t0 = _step("Market Analysis")
    mkt = parse_model(
        market_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "description": fund.description,
        }),
        MarketAnalysis,
        MarketAnalysis(),
    )
    log.end_step("market_analysis", t0)
    _done("Market Analysis", t0)

    # ── 3. FINANCIALS ─────────────────────────────────────────────────────
    t0 = _step("Financials")
    if market_data:
        fin = _fin_from_market(market_data)
        console.print("  [green]Using verified yfinance financials[/green]")
    else:
        fin = parse_model(
            fin_agent.run(company, company_type, {
                "sector": fund.sector or "",
                "description": fund.description,
            }),
            Financials,
            _fin_fallback(),
        )
    log.end_step("financials", t0)
    _done("Financials", t0)

    # ── 4. ASSUMPTIONS ────────────────────────────────────────────────────
    t0 = _step("Assumptions")
    assumptions = parse_model(
        val_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "revenue_current": fin.revenue_current,
            "ebitda_margin": fin.ebitda_margin,
        }),
        ValuationAssumptions,
        ValuationAssumptions(),
    )
    log.end_step("assumptions", t0)
    _done("Assumptions", t0)

    # ── 5. VALUATION ENGINE ───────────────────────────────────────────────
    t0 = _step("Valuation Engine")
    result = svc.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions.model_dump(),
        market_data=market_data,
        sector=fund.sector or "",
    )

    blended_ev = result.blended.blended
    rec = result.recommendation

    val = Valuation(
        current_price=str(rec.current_price) if rec.current_price else None,
        implied_value=str(round(blended_ev, 1)),
        upside_downside=(f"{rec.upside_pct:+.1%}" if rec.upside_pct is not None else "N/A"),
        recommendation=rec.recommendation,
        dcf_assumptions=DCFAssumptions(
            wacc=f"{result.wacc_used:.2%}",
            terminal_growth=f"{result.terminal_growth_used:.2%}",
            projection_years="5",
        ),
        methods=[
            ValuationMethod(name="DCF", mid=str(round(result.dcf.enterprise_value, 1)), weight=50),
            ValuationMethod(
                name=f"Trading Comps ({result.valuation_path.upper()})",
                low=str(round(result.comps.low, 1)),
                mid=str(round(result.comps.mid, 1)),
                high=str(round(result.comps.high, 1)),
                weight=30,
            ),
            ValuationMethod(
                name="Transaction Comps",
                mid=str(round(result.transactions.implied_value, 1)),
                weight=20,
            ),
        ],
        sources=[result.data_confidence],
    )

    if result.lbo:
        lbo = result.lbo
        console.print(
            f"  LBO: {'✓ FEASIBLE' if lbo.is_feasible else '✗ INFEASIBLE'} — "
            f"IRR {lbo.irr:.1%} / {lbo.moic:.1f}x MOIC / "
            f"{lbo.leverage_at_entry:.1f}x entry leverage"
        )

    log.data_confidence = result.data_confidence
    log.wacc_method = "capm" if result.data_confidence == "verified" else "estimated"
    log.valuation_notes = result.notes
    log.recommendation = rec.recommendation
    log.upside_pct = rec.upside_pct
    log.blended_ev = blended_ev

    for note in result.notes:
        console.print(f"  [dim]• {note}[/dim]")

    log.end_step("valuation", t0)
    _done("Valuation Engine", t0)

    # ── 6. THESIS ─────────────────────────────────────────────────────────
    t0 = _step("Investment Thesis")
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
    log.end_step("thesis", t0)
    _done("Investment Thesis", t0)

    console.rule("[DONE EQUITY]")
    log.flush()

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
# M&A PIPELINE
# ─────────────────────────────────────────────
def run_ma_analysis(
    target: str,
    acquirer: str = "",
    company_type: str = "public",
    objective: str = "",
) -> MAResult:
    log = new_run(target, company_type)
    client = _client()
    svc = ValuationService()

    sourcing_agent = DealSourcingAgent(client)
    fit_agent = StrategicFitAgent(client)
    dd_agent = DueDiligenceAgent(client)
    exec_agent = DealExecutionAgent(client)
    lbo_agent = LBOAgent(client)

    console.rule(f"[M&A] {target} — acquirer: {acquirer or 'N/A'}")

    context = {
        "acquirer": acquirer,
        "objective": objective,
        "sector": "",
    }

    # ── 0. REAL DATA for target (if public) ───────────────────────────────
    market_data: MarketData | None = None
    if company_type == "public":
        t0 = _step("Target Market Data")
        ticker = resolve_ticker(target)
        if ticker:
            market_data = fetch_market_data(ticker)
            log.ticker = ticker
            if market_data:
                context["sector"] = market_data.sector
                console.print(
                    f"  [green]Verified[/green] {target} → {ticker} | "
                    f"MCap=${market_data.market_cap:.0f}M"
                )
        log.end_step("market_data", t0)
        _done("Target Market Data", t0)

    # ── 1. DEAL SOURCING ──────────────────────────────────────────────────
    t0 = _step("Deal Sourcing")
    sourcing = parse_model(
        sourcing_agent.run(target, company_type, context),
        DealSourcing,
        DealSourcing(),
    )
    if sourcing.opportunities:
        context["sector"] = context.get("sector") or ""
    _done("Deal Sourcing", t0)

    # ── 2. STRATEGIC FIT ──────────────────────────────────────────────────
    t0 = _step("Strategic Fit")
    fit = parse_model(
        fit_agent.run(target, company_type, context),
        StrategicFit,
        StrategicFit(),
    )
    _done("Strategic Fit", t0)

    # ── 3. DUE DILIGENCE ──────────────────────────────────────────────────
    t0 = _step("Due Diligence")
    dd = parse_model(
        dd_agent.run(target, company_type, context),
        DueDiligence,
        DueDiligence(),
    )
    _done("Due Diligence", t0)

    # ── 4. DEAL EXECUTION ─────────────────────────────────────────────────
    t0 = _step("Deal Execution")
    execution = parse_model(
        exec_agent.run(target, company_type, {"acquirer": acquirer}),
        DealExecution,
        DealExecution(),
    )
    _done("Deal Execution", t0)

    # ── 5. LBO (qualitative LLM + deterministic engine) ───────────────────
    t0 = _step("LBO Analysis")
    lbo_text = parse_model(
        lbo_agent.run(target, company_type, context),
        LBOModel,
        LBOModel(),
    )

    # Run deterministic LBO if we have real data
    lbo_engine = None
    if market_data and market_data.ebitda_ttm and market_data.ebitda_ttm > 0:
        valuation_result = svc.run_full_valuation(
            financials={},
            assumptions={},
            market_data=market_data,
            sector=context.get("sector", ""),
        )
        lbo_engine = valuation_result.lbo
        if lbo_engine:
            console.print(
                f"  LBO Engine: IRR {lbo_engine.irr:.1%} / "
                f"{lbo_engine.moic:.1f}x MOIC / "
                f"{'FEASIBLE' if lbo_engine.is_feasible else 'INFEASIBLE'}"
            )
            # Enrich LBOModel with engine results
            lbo_text.feasible = lbo_engine.is_feasible
            lbo_text.irr_range = f"{lbo_engine.irr:.1%} (deterministic)"
            lbo_text.leverage = f"{lbo_engine.leverage_at_entry:.1f}x"

    _done("LBO Analysis", t0)

    # ── 6. IC SCORING ─────────────────────────────────────────────────────
    t0 = _step("IC Scoring")
    upside = None
    if market_data and lbo_engine:
        val_result = svc.run_full_valuation(
            financials={},
            assumptions={},
            market_data=market_data,
            sector=context.get("sector", ""),
        )
        upside = val_result.recommendation.upside_pct

    ic = auto_score_from_valuation(
        lbo_output=lbo_engine,
        upside_pct=upside,
        sector=context.get("sector", ""),
        company=target,
    )
    console.print(
        f"  IC Score: [bold]{ic.ic_score:.0f}/100[/bold] → "
        f"[{'green' if 'BUY' in ic.recommendation else 'yellow'}]{ic.recommendation}[/]"
    )
    _done("IC Scoring", t0)

    console.rule("[DONE M&A]")
    log.flush()

    return MAResult(
        company=target,
        company_type=company_type,
        acquirer=acquirer or None,
        deal_sourcing=sourcing,
        strategic_fit=fit,
        due_diligence=dd,
        deal_execution=execution,
        lbo=lbo_text,
    )


# ─────────────────────────────────────────────
# PIPELINE BUILDER
# ─────────────────────────────────────────────
def run_pipeline(
    buyer: str,
    focus: str = "",
    company_type: str = "private",
) -> AcquisitionPipeline:
    log = new_run(buyer, "pipeline")
    client = _client()

    pipeline_agent = PipelineBuilderAgent(client)

    console.rule(f"[PIPELINE] {buyer}")

    t0 = _step("Pipeline Generation")
    raw = pipeline_agent.run(
        buyer,
        company_type,
        {"buyer": buyer, "focus": focus},
    )
    pipeline = parse_model(raw, AcquisitionPipeline, AcquisitionPipeline(
        buyer=buyer,
        thesis="N/A",
        focus=focus,
    ))
    _done("Pipeline Generation", t0)

    # ── Light IC scoring for each target ─────────────────────────────────
    t0 = _step("IC Scoring — Pipeline Targets")
    for i, tgt in enumerate(pipeline.targets):
        ic = score_from_analysis(
            strategy=6.0,
            synergies=6.0,
            financial=5.0,
            lbo=5.0,
            integration=6.0,
            risk=5.0,
            company=tgt.name,
        )
        console.print(
            f"  [{i+1}] {tgt.name}: IC {ic.ic_score:.0f}/100 → {ic.recommendation}"
        )
    _done("IC Scoring", t0)

    console.rule("[DONE PIPELINE]")
    log.flush()

    return pipeline
