"""
Institution-grade DCF model.

Formula: FCFF = EBITDA(1-T) + D&A×T - CapEx - ΔNWC

Key correctness fixes vs previous version:
- NWC drag uses revenue INCREMENT (not revenue level) — avoids massive overstatement
- D&A tax shield (D&A × T) added when D&A data is available
- Guardrails on WACC > terminal_growth enforced before terminal value
- All inputs clamped to economically sensible ranges
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DCFInput:
    revenue: List[float]           # projected revenue by year (USD millions)
    ebitda_margin: float           # decimal, e.g. 0.25
    tax_rate: float                # decimal, e.g. 0.25
    capex_pct: float               # CapEx as % of revenue
    nwc_pct: float                 # NWC as % of revenue (for incremental calc)
    wacc: float                    # decimal
    terminal_growth: float         # decimal, long-run FCF growth
    da_pct: Optional[float] = None # D&A as % of revenue (enables tax shield)


@dataclass
class DCFOutput:
    free_cash_flows: List[float]
    discounted_cash_flows: List[float]
    terminal_value: float           # discounted terminal value
    enterprise_value: float
    terminal_value_pct: float       # TV as % of total EV (sanity indicator)


def compute_dcf(inp: DCFInput) -> DCFOutput:
    # ── Input guardrails ────────────────────────────────────────────────────
    ebitda_margin = max(-0.50, min(inp.ebitda_margin, 0.80))
    tax_rate = max(0.0, min(inp.tax_rate, 0.50))
    capex_pct = max(0.0, min(inp.capex_pct, 0.30))
    nwc_pct = max(0.0, min(inp.nwc_pct, 0.15))
    wacc = max(0.05, min(inp.wacc, 0.25))
    terminal_growth = max(0.0, min(inp.terminal_growth, 0.04))

    # Terminal growth must be strictly below WACC
    if terminal_growth >= wacc:
        terminal_growth = wacc - 0.01

    da_pct = inp.da_pct
    if da_pct is not None:
        da_pct = max(0.0, min(da_pct, 0.20))

    revenues = inp.revenue
    prev_rev = revenues[0] / (1 + 0.001)  # approximate prior year

    fcf_list: List[float] = []
    discounted: List[float] = []

    for i, rev in enumerate(revenues, start=1):
        ebitda = rev * ebitda_margin

        # FCFF = EBITDA(1-T) + D&A×T - CapEx - ΔNWC
        nopat_approx = ebitda * (1 - tax_rate)

        da_shield = 0.0
        if da_pct is not None:
            da = rev * da_pct
            da_shield = da * tax_rate

        capex = rev * capex_pct
        delta_nwc = (rev - prev_rev) * nwc_pct  # incremental NWC, not level

        fcf = nopat_approx + da_shield - capex - delta_nwc

        fcf_list.append(fcf)
        discounted.append(fcf / ((1 + wacc) ** i))

        prev_rev = rev

    # ── Terminal Value (Gordon Growth Model) ───────────────────────────────
    terminal_fcf = fcf_list[-1] * (1 + terminal_growth)
    terminal_value_undiscounted = terminal_fcf / (wacc - terminal_growth)
    discounted_terminal = terminal_value_undiscounted / ((1 + wacc) ** len(revenues))

    pv_fcfs = sum(discounted)
    enterprise_value = pv_fcfs + discounted_terminal

    tv_pct = discounted_terminal / enterprise_value if enterprise_value > 0 else 0.0

    return DCFOutput(
        free_cash_flows=fcf_list,
        discounted_cash_flows=discounted,
        terminal_value=discounted_terminal,
        enterprise_value=enterprise_value,
        terminal_value_pct=tv_pct,
    )
