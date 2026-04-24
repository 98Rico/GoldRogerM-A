"""
Orchestrator — coordinates the 5 agents in sequence,
passes context between them, assembles the final AnalysisResult.
"""
import os

from dotenv import load_dotenv
from mistralai.client import Mistral
from rich.console import Console

from .agents.specialists import (
    DataCollectorAgent,
    DealExecutionAgent,
    DealSourcingAgent,
    FinancialModelerAgent,
    LBOAgent,
    PipelineBuilderAgent,
    ReportWriterAgent,
    SectorAnalystAgent,
    StrategicFitAgent,
    DueDiligenceAgent,
    ValuationEngineAgent,
)
from .models import (
    AnalysisResult,
    AcquisitionPipeline,
    DealExecution,
    DealSourcing,
    Financials,
    Fundamentals,
    InvestmentThesis,
    LBOModel,
    MAResult,
    MarketAnalysis,
    StrategicFit,
    DueDiligence,
    Valuation,
)
from .utils.json_parser import parse_model

load_dotenv()
console = Console()


def _make_fundamentals_fallback(company: str) -> Fundamentals:
    return Fundamentals(
        company_name=company,
        description="Analysis unavailable.",
        business_model="Data could not be retrieved.",
    )


def run_analysis(company: str, company_type: str = "public") -> AnalysisResult:
    """Main entry point — runs all 5 agents and returns a structured AnalysisResult."""
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing `MISTRAL_API_KEY`. Set it in your environment or in a local `.env` file."
        )
    client = Mistral(api_key=api_key)

    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)
    fin_agent = FinancialModelerAgent(client)
    val_agent = ValuationEngineAgent(client)
    thesis_agent = ReportWriterAgent(client)

    console.rule(f"[bold green]Gold Roger — {company}")

    # ── Agent 1: Fundamentals ──────────────────────────────────────────────
    with console.status("[cyan]Agent 1/5 — Data Collector (fundamentals + web search)..."):
        raw_fund = data_agent.run(company, company_type)
    console.print("[green]✓[/] Agent 1 done")

    fund = parse_model(raw_fund, Fundamentals, _make_fundamentals_fallback(company))
    console.print(f"  → {fund.company_name} | {fund.sector} | {fund.headquarters}")

    # ── Agent 2: Market ────────────────────────────────────────────────────
    with console.status("[cyan]Agent 2/5 — Sector Analyst (market sizing + competition)..."):
        raw_market = market_agent.run(
            company,
            company_type,
            {
                "sector": fund.sector or "",
                "description": fund.description,
                "business_model": fund.business_model,
            },
        )
    console.print("[green]✓[/] Agent 2 done")

    mkt = parse_model(raw_market, MarketAnalysis, MarketAnalysis())
    console.print(f"  → TAM: {mkt.market_size} | Growth: {mkt.market_growth}")

    # ── Agent 3: Financials ────────────────────────────────────────────────
    with console.status("[cyan]Agent 3/5 — Financial Modeler (P&L + projections)..."):
        raw_fin = fin_agent.run(
            company,
            company_type,
            {
                "sector": fund.sector or "",
                "description": fund.description,
                "business_model": fund.business_model,
                "market_segment": mkt.market_segment or "",
            },
        )
    console.print("[green]✓[/] Agent 3 done")

    fin = parse_model(raw_fin, Financials, Financials())
    console.print(f"  → Revenue: {fin.revenue_current} | EBITDA margin: {fin.ebitda_margin}")

    # ── Agent 4: Valuation ─────────────────────────────────────────────────
    with console.status("[cyan]Agent 4/5 — Valuation Engine (DCF + comps)..."):
        raw_val = val_agent.run(
            company,
            company_type,
            {
                "sector": fund.sector or "",
                "revenue_current": fin.revenue_current or "unknown",
                "ebitda_margin": fin.ebitda_margin or "unknown",
            },
        )
    console.print("[green]✓[/] Agent 4 done")

    val = parse_model(raw_val, Valuation, Valuation(recommendation="N/A"))
    console.print(
        f"  → Implied value: {val.implied_value} | {val.recommendation} | Upside: {val.upside_downside}"
    )

    # ── Agent 5: Thesis ────────────────────────────────────────────────────
    with console.status("[cyan]Agent 5/5 — Report Writer (investment thesis)..."):
        raw_thesis = thesis_agent.run(
            company,
            company_type,
            {
                "recommendation": val.recommendation or "HOLD",
                "upside_downside": val.upside_downside or "",
            },
        )
    console.print("[green]✓[/] Agent 5 done")

    thesis = parse_model(raw_thesis, InvestmentThesis, InvestmentThesis(thesis="Thesis unavailable."))

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


def run_ma_analysis(
    company: str,
    company_type: str = "public",
    *,
    acquirer: str | None = None,
    objective: str | None = None,
) -> MAResult:
    """M&A mode — deal sourcing, strategic fit, diligence, execution, and LBO view."""
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing `MISTRAL_API_KEY`. Set it in your environment or in a local `.env` file."
        )
    client = Mistral(api_key=api_key)

    # Reuse fundamentals/market context as inputs (helps for private companies too)
    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)

    sourcing_agent = DealSourcingAgent(client)
    fit_agent = StrategicFitAgent(client)
    dd_agent = DueDiligenceAgent(client)
    exec_agent = DealExecutionAgent(client)
    lbo_agent = LBOAgent(client)

    console.rule(f"[bold green]Gold Roger — M&A — {company}")

    with console.status("[cyan]Context 1/2 — Company snapshot..."):
        raw_fund = data_agent.run(company, company_type)
    fund = parse_model(raw_fund, Fundamentals, _make_fundamentals_fallback(company))
    console.print("[green]✓[/] Snapshot done")

    with console.status("[cyan]Context 2/2 — Market context..."):
        raw_market = market_agent.run(company, company_type, {"sector": fund.sector or ""})
    mkt = parse_model(raw_market, MarketAnalysis, MarketAnalysis())
    console.print("[green]✓[/] Market context done")

    base_ctx = {
        "acquirer": acquirer or "",
        "objective": objective or "",
        "sector": fund.sector or "",
        "market_segment": mkt.market_segment or "",
    }

    with console.status("[cyan]M&A 1/5 — Deal sourcing (pipeline)..."):
        raw_src = sourcing_agent.run(company, company_type, base_ctx)
    src = parse_model(raw_src, DealSourcing, DealSourcing())
    console.print("[green]✓[/] Deal sourcing done")

    with console.status("[cyan]M&A 2/5 — Strategic fit (synergies + structure)..."):
        raw_fit = fit_agent.run(company, company_type, base_ctx)
    fit = parse_model(raw_fit, StrategicFit, StrategicFit())
    console.print("[green]✓[/] Strategic fit done")

    with console.status("[cyan]M&A 3/5 — Due diligence (red flags + requests)..."):
        raw_dd = dd_agent.run(company, company_type, base_ctx)
    dd = parse_model(raw_dd, DueDiligence, DueDiligence())
    console.print("[green]✓[/] Due diligence done")

    with console.status("[cyan]M&A 4/5 — Deal execution (workplan)..."):
        raw_ex = exec_agent.run(company, company_type, base_ctx)
    ex = parse_model(raw_ex, DealExecution, DealExecution())
    console.print("[green]✓[/] Deal execution done")

    with console.status("[cyan]M&A 5/5 — LBO view (if relevant)..."):
        raw_lbo = lbo_agent.run(company, company_type, base_ctx)
    lbo = parse_model(raw_lbo, LBOModel, LBOModel())
    console.print("[green]✓[/] LBO view done")

    console.rule("[bold green]M&A analysis complete")

    return MAResult(
        company=company,
        company_type=company_type,
        acquirer=acquirer,
        deal_sourcing=src,
        strategic_fit=fit,
        due_diligence=dd,
        deal_execution=ex,
        lbo=lbo,
    )


def run_pipeline(
    *,
    buyer: str,
    focus: str,
) -> AcquisitionPipeline:
    """Pipeline mode — generate target shortlist + private valuation estimates."""
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing `MISTRAL_API_KEY`. Set it in your environment or in a local `.env` file."
        )
    client = Mistral(api_key=api_key)
    agent = PipelineBuilderAgent(client)

    console.rule("[bold green]Gold Roger — Pipeline")
    with console.status("[cyan]Pipeline — generating targets + valuations..."):
        raw = agent.run(
            company="pipeline",
            company_type="private",
            context={"buyer": buyer, "focus": focus},
        )
    pipe = parse_model(raw, AcquisitionPipeline, AcquisitionPipeline(buyer=buyer, thesis="", focus=focus))
    console.print("[green]✓[/] Pipeline done")
    return pipe
