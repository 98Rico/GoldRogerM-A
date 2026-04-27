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
from goldroger.ma.scoring import score_from_analysis
from goldroger.utils.logger import new_run

from .agents.specialists import (
    DataCollectorAgent,
    DealExecutionAgent,
    DealSourcingAgent,
    DueDiligenceAgent,
    FinancialModelerAgent,
    LBOAgent,
    PeerFinderAgent,
    PipelineBuilderAgent,
    ReportWriterAgent,
    SectorAnalystAgent,
    StrategicFitAgent,
    ValuationEngineAgent,
)
from .data.comparables import (
    build_peer_multiples,
    parse_peer_agent_output,
    resolve_peer_tickers,
    PeerMultiples,
)
from .data.registry import DEFAULT_REGISTRY
from .finance.core.scenarios import run_scenarios, ScenariosOutput
from .ma.scoring import score_from_ma_agents

from .models import (
    AnalysisResult,
    AcquisitionPipeline,
    DCFAssumptions,
    DealExecution,
    DealSourcing,
    DueDiligence,
    Financials,
    FootballField,
    Fundamentals,
    ICScoreSummary,
    InvestmentThesis,
    LBOModel,
    MAResult,
    MarketAnalysis,
    PeerComp,
    PeerCompsTable,
    ScenarioSummary,
    StrategicFit,
    Valuation,
    ValuationMethod,
)

from .utils.json_parser import parse_model, did_fallback


def _parse_with_retry(agent, company, company_type, context, model_class, fallback):
    """Parse LLM output; retry once with strict JSON hint if parse fails."""
    raw = agent.run(company, company_type, context)
    result = parse_model(raw, model_class, fallback)
    if did_fallback(result):
        console.print(f"  [yellow]JSON parse failed — retrying with strict prompt[/yellow]")
        raw2 = agent.run(company, company_type, context, _strict_json=True)
        result = parse_model(raw2, model_class, fallback, _retry=True)
    return result

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
    return Financials()


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
    peer_agent = PeerFinderAgent(client)

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

    # ── 2b. PEER COMPARABLES ──────────────────────────────────────────────
    t0 = _step("Peer Comparables")
    peer_comps_table: PeerCompsTable | None = None
    peer_multiples = None
    try:
        raw_peers = peer_agent.run(company, company_type, {
            "sector": fund.sector or "",
            "description": fund.description or "",
        })
        peer_list = parse_peer_agent_output(raw_peers)
        peer_tickers = resolve_peer_tickers(peer_list)
        if peer_tickers:
            peer_multiples = build_peer_multiples(peer_tickers)
            if peer_multiples.n_peers > 0:
                console.print(
                    f"  [cyan]{peer_multiples.n_peers} peers found:[/cyan] "
                    + ", ".join(p.ticker for p in peer_multiples.peers[:6])
                )
                if peer_multiples.ev_ebitda_median:
                    console.print(
                        f"  Median EV/EBITDA: {peer_multiples.ev_ebitda_median:.1f}x  "
                        f"EV/Rev: {peer_multiples.ev_revenue_median:.1f}x"
                        if peer_multiples.ev_revenue_median else
                        f"  Median EV/EBITDA: {peer_multiples.ev_ebitda_median:.1f}x"
                    )
                peer_comps_table = PeerCompsTable(
                    peers=[
                        PeerComp(
                            name=p.name,
                            ticker=p.ticker,
                            ev_ebitda=f"{p.ev_ebitda:.1f}x" if p.ev_ebitda else None,
                            ev_revenue=f"{p.ev_revenue:.1f}x" if p.ev_revenue else None,
                            ebitda_margin=f"{p.ebitda_margin:.1%}" if p.ebitda_margin else None,
                            revenue_growth=f"{p.revenue_growth:+.1%}" if p.revenue_growth else None,
                        )
                        for p in peer_multiples.peers
                    ],
                    median_ev_ebitda=f"{peer_multiples.ev_ebitda_median:.1f}x" if peer_multiples.ev_ebitda_median else None,
                    median_ev_revenue=f"{peer_multiples.ev_revenue_median:.1f}x" if peer_multiples.ev_revenue_median else None,
                    median_ebitda_margin=f"{peer_multiples.ebitda_margin_median:.1%}" if peer_multiples.ebitda_margin_median else None,
                    n_peers=peer_multiples.n_peers,
                )
    except Exception as e:
        console.print(f"  [yellow]Peer finder skipped: {e}[/yellow]")
    _done("Peer Comparables", t0)

    # ── 3. FINANCIALS ─────────────────────────────────────────────────────
    t0 = _step("Financials")
    if market_data:
        fin = _fin_from_market(market_data)
        console.print("  [green]Using verified yfinance financials[/green]")
    else:
        fin = _parse_with_retry(
            fin_agent, company, company_type,
            {"sector": fund.sector or "", "description": fund.description},
            Financials, _fin_fallback(),
        )
    log.end_step("financials", t0)
    _done("Financials", t0)

    # ── 4. ASSUMPTIONS ────────────────────────────────────────────────────
    t0 = _step("Assumptions")
    assumptions = _parse_with_retry(
        val_agent, company, company_type,
        {"sector": fund.sector or "", "revenue_current": fin.revenue_current, "ebitda_margin": fin.ebitda_margin},
        ValuationAssumptions, ValuationAssumptions(),
    )
    log.end_step("assumptions", t0)
    _done("Assumptions", t0)

    # ── 5. VALUATION ENGINE ───────────────────────────────────────────────
    t0 = _step("Valuation Engine")

    # If peer multiples available, override comps range with real peer data
    assumptions_dict = assumptions.model_dump()
    if peer_multiples and peer_multiples.ev_ebitda_low and peer_multiples.ev_ebitda_high:
        assumptions_dict["ev_ebitda_range"] = [
            peer_multiples.ev_ebitda_low,
            peer_multiples.ev_ebitda_high,
        ]
        console.print(
            f"  [cyan]Comps anchored to {peer_multiples.n_peers} real peers: "
            f"{peer_multiples.ev_ebitda_low:.1f}x–{peer_multiples.ev_ebitda_high:.1f}x EV/EBITDA[/cyan]"
        )

    result = svc.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions_dict,
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

    for note in result.notes:
        console.print(f"  [dim]• {note}[/dim]")

    # ── 5b. BEAR / BASE / BULL SCENARIOS ─────────────────────────────────
    football_field: FootballField | None = None
    ic_summary: ICScoreSummary | None = None
    try:
        revenue_series, _ = svc._build_revenue_series(fin.model_dump(), market_data, [])
        scenarios_out = run_scenarios(
            base_revenue=revenue_series,
            base_ebitda_margin=svc._resolve_ebitda_margin(fin.model_dump(), market_data, [])[0],
            base_wacc=result.wacc_used,
            base_terminal_growth=result.terminal_growth_used,
            base_comps_low=result.comps.low,
            base_comps_high=result.comps.high,
            base_tx_multiple=result.transactions.implied_value / revenue_series[-1]
                if revenue_series and revenue_series[-1] > 0 else 2.0,
            tax_rate=svc._resolve_tax_rate(fin.model_dump(), market_data),
            capex_pct=svc._resolve_capex_pct(fin.model_dump(), market_data, revenue_series[-1] if revenue_series else 1000),
            nwc_pct=float(fin.model_dump().get("nwc_pct") or 0.02),
            da_pct=svc._resolve_da_pct(market_data, revenue_series[-1] if revenue_series else None),
        )

        def _fmt_ev(v: float) -> str:
            return f"${v/1000:.1f}B" if v >= 1000 else f"${v:.0f}M"

        football_field = FootballField(
            bear=ScenarioSummary(
                name="Bear",
                dcf_ev=_fmt_ev(scenarios_out.bear.dcf_ev),
                comps_ev=_fmt_ev(scenarios_out.bear.comps_ev_mid),
                blended_ev=_fmt_ev(scenarios_out.bear.blended_ev),
                wacc=f"{scenarios_out.bear.wacc_used:.1%}",
                ebitda_margin=f"{scenarios_out.bear.ebitda_margin_used:.1%}",
            ),
            base=ScenarioSummary(
                name="Base",
                dcf_ev=_fmt_ev(scenarios_out.base.dcf_ev),
                comps_ev=_fmt_ev(scenarios_out.base.comps_ev_mid),
                blended_ev=_fmt_ev(scenarios_out.base.blended_ev),
                wacc=f"{scenarios_out.base.wacc_used:.1%}",
                ebitda_margin=f"{scenarios_out.base.ebitda_margin_used:.1%}",
            ),
            bull=ScenarioSummary(
                name="Bull",
                dcf_ev=_fmt_ev(scenarios_out.bull.dcf_ev),
                comps_ev=_fmt_ev(scenarios_out.bull.comps_ev_mid),
                blended_ev=_fmt_ev(scenarios_out.bull.blended_ev),
                wacc=f"{scenarios_out.bull.wacc_used:.1%}",
                ebitda_margin=f"{scenarios_out.bull.ebitda_margin_used:.1%}",
            ),
            dcf_range=f"{_fmt_ev(scenarios_out.bear.dcf_ev)} — {_fmt_ev(scenarios_out.bull.dcf_ev)}",
            comps_range=f"{_fmt_ev(scenarios_out.bear.comps_ev_mid)} — {_fmt_ev(scenarios_out.bull.comps_ev_mid)}",
            blended_range=f"{_fmt_ev(scenarios_out.bear.blended_ev)} — {_fmt_ev(scenarios_out.bull.blended_ev)}",
        )
        console.print(
            f"  [bold]Football field:[/bold] Bear {_fmt_ev(scenarios_out.bear.blended_ev)} "
            f"/ Base {_fmt_ev(scenarios_out.base.blended_ev)} "
            f"/ Bull {_fmt_ev(scenarios_out.bull.blended_ev)}"
        )
    except Exception as e:
        console.print(f"  [yellow]Scenarios skipped: {e}[/yellow]")

    # ── 5c. IC SCORING (equity standalone) ───────────────────────────────
    try:
        from goldroger.ma.scoring import auto_score_from_valuation
        ic_result = auto_score_from_valuation(
            lbo_output=result.lbo,
            upside_pct=rec.upside_pct,
            sector=fund.sector or "",
            company=company,
        )
        ic_summary = ICScoreSummary(
            ic_score=f"{ic_result.ic_score:.0f}/100",
            recommendation=ic_result.recommendation,
            strategy=f"{ic_result.dimension_scores.get('strategy', 5):.1f}/10",
            synergies=f"{ic_result.dimension_scores.get('synergies', 5):.1f}/10",
            financial=f"{ic_result.dimension_scores.get('financial', 5):.1f}/10",
            lbo=f"{ic_result.dimension_scores.get('lbo', 5):.1f}/10",
            integration=f"{ic_result.dimension_scores.get('integration', 5):.1f}/10",
            risk=f"{ic_result.dimension_scores.get('risk', 5):.1f}/10",
            rationale=ic_result.rationale,
            next_steps=ic_result.next_steps,
        )
        console.print(
            f"  IC Score: [bold]{ic_result.ic_score:.0f}/100[/bold] → {ic_result.recommendation}"
        )
    except Exception as e:
        console.print(f"  [yellow]IC scoring skipped: {e}[/yellow]")

    log.data_confidence = result.data_confidence
    log.wacc_method = "capm" if result.data_confidence == "verified" else "estimated"
    log.valuation_notes = result.notes
    log.recommendation = rec.recommendation
    log.upside_pct = rec.upside_pct
    log.blended_ev = blended_ev

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
        football_field=football_field,
        peer_comps=peer_comps_table,
        ic_score=ic_summary,
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

    # ── 6. IC SCORING (enriched from all agent outputs) ───────────────────
    t0 = _step("IC Scoring")
    upside = None
    if market_data:
        try:
            val_result = svc.run_full_valuation(
                financials={},
                assumptions={},
                market_data=market_data,
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
