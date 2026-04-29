"""Shared utilities for all pipeline modules."""
from __future__ import annotations

import time

from pydantic import BaseModel
from rich.console import Console

from goldroger.agents.llm_client import LLMProvider, build_llm_provider
from goldroger.data.fetcher import MarketData
from goldroger.models import Financials, Fundamentals
from goldroger.utils.json_parser import parse_model, did_fallback

console = Console()


# ── LLM assumption model ───────────────────────────────────────────────────
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


# ── Fallback constructors ──────────────────────────────────────────────────
def _fund_fallback(company: str) -> Fundamentals:
    return Fundamentals(company_name=company, description="N/A", business_model="N/A")


def _fin_fallback() -> Financials:
    return Financials()


def _fin_from_market(md: MarketData) -> Financials:
    """Build verified Financials from yfinance MarketData."""
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


# ── Timer helpers ──────────────────────────────────────────────────────────
def _step(name: str) -> float:
    console.rule(f"[bold cyan]{name}")
    return time.time()


def _done(name: str, start: float) -> float:
    elapsed = round(time.time() - start, 2)
    console.print(f"[green]✓ {name} done in {elapsed}s")
    return elapsed


# ── LLM client factory ─────────────────────────────────────────────────────
def _client(llm_override: str | None = None) -> LLMProvider:
    return build_llm_provider(llm_override)


# ── Formatting helpers ─────────────────────────────────────────────────────
def _fmt_ev_human(v_m: float) -> str:
    """Format EV in USD millions to human-readable string."""
    if v_m >= 1_000_000:
        return f"${v_m / 1_000_000:.2f}T"
    if v_m >= 1_000:
        return f"${v_m / 1_000:.1f}B"
    return f"${v_m:.0f}M"


# ── Agent runner with retry ────────────────────────────────────────────────
def _parse_with_retry(agent, company, company_type, context, model_class, fallback):
    """Run agent, parse JSON output; retry once with strict hint on failure."""
    raw = agent.run(company, company_type, context)
    result = parse_model(raw, model_class, fallback)
    if did_fallback(result):
        console.print("  [yellow]JSON parse failed — retrying with strict prompt[/yellow]")
        raw2 = agent.run(company, company_type, context, _strict_json=True)
        result = parse_model(raw2, model_class, fallback, _retry=True)
    return result
