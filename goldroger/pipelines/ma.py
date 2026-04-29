"""M&A analysis pipeline — run_ma_analysis()."""
from __future__ import annotations

from dotenv import load_dotenv

from goldroger.agents.specialists import (
    DealExecutionAgent,
    DealSourcingAgent,
    DueDiligenceAgent,
    LBOAgent,
    StrategicFitAgent,
)
from goldroger.data.fetcher import fetch_market_data, resolve_ticker
from goldroger.finance.core.valuation_service import ValuationService
from goldroger.ma.scoring import score_from_ma_agents
from goldroger.models import (
    DealExecution,
    DealSourcing,
    DueDiligence,
    ICScoreSummary,
    LBOModel,
    MAResult,
    StrategicFit,
)
from goldroger.utils.json_parser import parse_model
from goldroger.utils.logger import new_run

from ._shared import _client, _done, _step, console

load_dotenv()


def run_ma_analysis(
    target: str,
    acquirer: str = "",
    company_type: str = "public",
    objective: str = "",
    llm: str | None = None,
) -> MAResult:
    log = new_run(target, company_type)
    client = _client(llm)
    svc = ValuationService()

    sourcing_agent = DealSourcingAgent(client)
    fit_agent = StrategicFitAgent(client)
    dd_agent = DueDiligenceAgent(client)
    exec_agent = DealExecutionAgent(client)
    lbo_agent = LBOAgent(client)

    console.rule(f"[M&A] {target} — acquirer: {acquirer or 'N/A'}")

    context = {"acquirer": acquirer, "objective": objective, "sector": ""}

    # ── 0. REAL DATA for target (if public) ───────────────────────────────
    market_data = None
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
        sourcing_agent.run(target, company_type, context), DealSourcing, DealSourcing()
    )
    _done("Deal Sourcing", t0)

    # ── 2. STRATEGIC FIT ──────────────────────────────────────────────────
    t0 = _step("Strategic Fit")
    fit = parse_model(
        fit_agent.run(target, company_type, context), StrategicFit, StrategicFit()
    )
    _done("Strategic Fit", t0)

    # ── 3. DUE DILIGENCE ──────────────────────────────────────────────────
    t0 = _step("Due Diligence")
    dd = parse_model(
        dd_agent.run(target, company_type, context), DueDiligence, DueDiligence()
    )
    _done("Due Diligence", t0)

    # ── 4. DEAL EXECUTION ─────────────────────────────────────────────────
    t0 = _step("Deal Execution")
    execution = parse_model(
        exec_agent.run(target, company_type, {"acquirer": acquirer}),
        DealExecution, DealExecution(),
    )
    _done("Deal Execution", t0)

    # ── 5. LBO (qualitative LLM + deterministic engine) ───────────────────
    t0 = _step("LBO Analysis")
    lbo_text = parse_model(
        lbo_agent.run(target, company_type, context), LBOModel, LBOModel()
    )
    lbo_engine = None
    if market_data and market_data.ebitda_ttm and market_data.ebitda_ttm > 0:
        valuation_result = svc.run_full_valuation(
            financials={}, assumptions={}, market_data=market_data,
            sector=context.get("sector", ""),
        )
        lbo_engine = valuation_result.lbo
        if lbo_engine:
            console.print(
                f"  LBO Engine: IRR {lbo_engine.irr:.1%} / "
                f"{lbo_engine.moic:.1f}x MOIC / "
                f"{'FEASIBLE' if lbo_engine.is_feasible else 'INFEASIBLE'}"
            )
            lbo_text.feasible = lbo_engine.is_feasible
            lbo_text.irr_range = f"{lbo_engine.irr:.1%} (deterministic)"
            lbo_text.leverage = f"{lbo_engine.leverage_at_entry:.1f}x"
    _done("LBO Analysis", t0)

    # ── 6. IC SCORING ─────────────────────────────────────────────────────
    t0 = _step("IC Scoring")
    upside = None
    if market_data:
        try:
            val_result = svc.run_full_valuation(
                financials={}, assumptions={}, market_data=market_data,
                sector=context.get("sector", ""),
            )
            upside = val_result.recommendation.upside_pct
        except Exception:
            pass

    ic = score_from_ma_agents(
        strategic_fit=fit,
        due_diligence=dd,
        lbo_output=lbo_engine,
        upside_pct=upside,
        company=target,
        acquirer=acquirer or "",
        sector=context.get("sector", ""),
    )
    console.print(
        f"  IC Score: [bold]{ic.ic_score:.0f}/100[/bold] → "
        f"[{'green' if 'BUY' in ic.recommendation else 'yellow'}]{ic.recommendation}[/]"
    )

    ic_summary = ICScoreSummary(
        ic_score=f"{ic.ic_score:.0f}/100",
        recommendation=ic.recommendation,
        strategy=f"{ic.dimension_scores.get('strategy', 5):.1f}/10",
        synergies=f"{ic.dimension_scores.get('synergies', 5):.1f}/10",
        financial=f"{ic.dimension_scores.get('financial', 5):.1f}/10",
        lbo=f"{ic.dimension_scores.get('lbo', 5):.1f}/10",
        integration=f"{ic.dimension_scores.get('integration', 5):.1f}/10",
        risk=f"{ic.dimension_scores.get('risk', 5):.1f}/10",
        rationale=ic.rationale,
        next_steps=ic.next_steps,
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
        ic_score=ic_summary,
    )
