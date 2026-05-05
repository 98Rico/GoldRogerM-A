"""Shared utilities for all pipeline modules."""
from __future__ import annotations

import time
from typing import Any

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


def _reconcile_financials(
    fin: Financials,
    market_data: MarketData | None,
    console: Console | None = None,
) -> Financials:
    """Override LLM-derived financial figures with registry-verified values when available.

    Called after the FinancialModelerAgent returns so that any drift between LLM
    output and real registry data is corrected before the valuation engine runs.
    """
    if market_data is None:
        return fin

    changed: list[str] = []

    # Revenue override — registry always wins over LLM estimate
    if market_data.revenue_ttm:
        llm_rev: float | None = None
        try:
            llm_rev = float(fin.revenue_current) if fin.revenue_current else None
        except (ValueError, TypeError):
            pass
        registry_rev = market_data.revenue_ttm
        if llm_rev and abs(llm_rev - registry_rev) / registry_rev > 0.20:
            changed.append(
                f"revenue {llm_rev:.0f}→{registry_rev:.0f}M "
                f"(delta {abs(llm_rev - registry_rev) / registry_rev:.0%})"
            )
        fin.revenue_current = str(registry_rev)

    # EBITDA margin override — registry wins if available
    if market_data.ebitda_margin is not None:
        llm_margin: float | None = None
        try:
            llm_margin = float(fin.ebitda_margin) if fin.ebitda_margin else None
        except (ValueError, TypeError):
            pass
        registry_margin = market_data.ebitda_margin
        if llm_margin and abs(llm_margin - registry_margin) > 0.10:
            changed.append(
                f"ebitda_margin {llm_margin:.1%}→{registry_margin:.1%}"
            )
        fin.ebitda_margin = str(registry_margin)

    if changed and console is not None:
        console.print(
            f"  [yellow]⚠ Reconciled (LLM→registry): {'; '.join(changed)}[/yellow]"
        )

    return fin


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
def _parse_with_retry(
    agent,
    company,
    company_type,
    context,
    model_class,
    fallback,
    *,
    fatal_on_fail: bool = False,
    retry_on_fail: bool = True,
    log_raw_errors: bool = False,
):
    """Run agent, parse JSON output; retry once with strict hint on failure."""
    def _log_bad_json(raw_text: Any, stage: str) -> None:
        if not log_raw_errors:
            return
        try:
            _raw = str(raw_text or "")
            # Keep a large slice so debugging has real context.
            _full = _raw[:12000]
            console.print(f"  [dim]Invalid JSON ({stage}) raw output (truncated 12k):[/]\n{_full}")
        except Exception:
            pass

    raw = agent.run(company, company_type, context)
    result = parse_model(raw, model_class, fallback)
    if did_fallback(result):
        _log_bad_json(raw, "first pass")
        if not retry_on_fail:
            if fatal_on_fail:
                raise ValueError(f"{agent.__class__.__name__}: invalid JSON (retry disabled)")
            return result
        console.print("  [yellow]JSON parse failed — retrying with strict prompt[/yellow]")
        strict_ctx = {**context, "__strict_json_hint": True}
        try:
            raw2 = agent.run(company, company_type, strict_ctx, _strict_json=True)
        except TypeError:
            raw2 = agent.run(company, company_type, strict_ctx)
        result = parse_model(raw2, model_class, fallback, _retry=True)
        if did_fallback(result):
            _log_bad_json(raw2, "strict retry")
            if fatal_on_fail:
                raise ValueError(f"{agent.__class__.__name__}: invalid JSON after strict retry")
    return result
