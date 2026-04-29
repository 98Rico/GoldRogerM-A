"""
Deterministic LBO model.

Entry → leverage → annual FCF debt paydown → exit → IRR / MOIC.

Methodology:
  - Entry equity = EV − entry_debt + transaction_fees
  - Annual FCF sweeps senior debt (cash_sweep % of FCF)
  - Exit EV = exit_year_EBITDA × exit_multiple
  - Exit equity = exit_EV − remaining_debt
  - IRR = (exit_equity / entry_equity)^(1/years) − 1  (single entry/exit)
  - Feasibility gates: leverage < max_leverage, IRR > min_irr

All monetary values in USD millions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from goldroger.config import DEFAULT_CONFIG as _cfg


@dataclass
class LBOInput:
    entry_ev: float                       # USD millions
    entry_ebitda: float                   # USD millions at close
    revenue_growth: float                 # annual (decimal)
    ebitda_margin: float                  # stable forward margin
    capex_pct: float                      # % of revenue
    tax_rate: float
    leverage_ratio: float                 # net debt / EBITDA at entry
    senior_rate: float                    # interest rate on debt
    exit_multiple: float                  # EV/EBITDA at exit
    hold_period: int = 5
    transaction_fees_pct: float = 0.015   # % of EV
    cash_sweep: float = field(default_factory=lambda: _cfg.lbo.fcf_sweep_rate)
    min_irr: float = field(default_factory=lambda: _cfg.lbo.min_irr)
    max_leverage: float = field(default_factory=lambda: _cfg.lbo.max_leverage)


@dataclass
class LBOOutput:
    entry_equity: float
    entry_debt: float
    exit_ev: float
    exit_equity: float
    exit_ebitda: float
    irr: float
    moic: float
    leverage_at_entry: float
    leverage_at_exit: float
    debt_schedule: list[float]
    fcf_schedule: list[float]
    interest_schedule: list[float]
    is_feasible: bool
    feasibility_notes: list[str] = field(default_factory=list)


def compute_lbo(inp: LBOInput) -> LBOOutput:
    notes: list[str] = []

    fees = inp.entry_ev * inp.transaction_fees_pct
    entry_debt = inp.entry_ebitda * inp.leverage_ratio
    entry_equity = inp.entry_ev - entry_debt + fees

    if entry_equity <= 0:
        notes.append("Entry equity negative — leverage too high for this EV.")
        entry_equity = inp.entry_ev * 0.30

    leverage_at_entry = entry_debt / inp.entry_ebitda if inp.entry_ebitda > 0 else 0.0

    # Derive entry revenue from known entry EBITDA and margin — correct and direct.
    # (Previous approach used exit_multiple which is only valid when entry == exit multiple.)
    if inp.ebitda_margin > 0:
        revenue = inp.entry_ebitda / inp.ebitda_margin
    else:
        revenue = inp.entry_ev / max(inp.exit_multiple, 1.0)  # last-resort fallback
    debt = entry_debt

    debt_schedule: list[float] = []
    fcf_schedule: list[float] = []
    interest_schedule: list[float] = []
    ebitda_by_year: list[float] = []

    for _ in range(inp.hold_period):
        revenue *= (1 + inp.revenue_growth)
        ebitda = revenue * inp.ebitda_margin
        ebitda_by_year.append(ebitda)

        interest = debt * inp.senior_rate
        nopat = max(ebitda - interest, 0) * (1 - inp.tax_rate)
        capex = revenue * inp.capex_pct
        fcf = nopat - capex

        fcf_schedule.append(fcf)
        interest_schedule.append(interest)

        debt_payment = max(fcf * inp.cash_sweep, 0)
        debt = max(debt - debt_payment, 0)
        debt_schedule.append(debt)

    exit_ebitda = ebitda_by_year[-1]
    exit_ev = exit_ebitda * inp.exit_multiple
    remaining_debt = debt_schedule[-1]
    exit_equity = exit_ev - remaining_debt
    leverage_at_exit = remaining_debt / exit_ebitda if exit_ebitda > 0 else 0.0

    moic = exit_equity / entry_equity if entry_equity > 0 else 0.0
    irr = _irr(entry_equity, exit_equity, inp.hold_period)

    is_feasible = True
    if leverage_at_entry > inp.max_leverage:
        notes.append(
            f"Leverage {leverage_at_entry:.1f}x exceeds max {inp.max_leverage:.1f}x — "
            "debt may be hard to place."
        )
        is_feasible = False
    if irr < inp.min_irr:
        notes.append(
            f"IRR {irr:.1%} below hurdle {inp.min_irr:.0%} — "
            "deal does not meet return threshold."
        )
        is_feasible = False
    if exit_equity <= 0:
        notes.append("Exit equity negative — company cannot service debt to exit.")
        is_feasible = False
    if is_feasible:
        notes.append(
            f"LBO feasible: {irr:.1%} IRR / {moic:.1f}x MOIC over {inp.hold_period}y."
        )

    return LBOOutput(
        entry_equity=round(entry_equity, 1),
        entry_debt=round(entry_debt, 1),
        exit_ev=round(exit_ev, 1),
        exit_equity=round(exit_equity, 1),
        exit_ebitda=round(exit_ebitda, 1),
        irr=round(irr, 4),
        moic=round(moic, 2),
        leverage_at_entry=round(leverage_at_entry, 2),
        leverage_at_exit=round(leverage_at_exit, 2),
        debt_schedule=[round(d, 1) for d in debt_schedule],
        fcf_schedule=[round(f, 1) for f in fcf_schedule],
        interest_schedule=[round(i, 1) for i in interest_schedule],
        is_feasible=is_feasible,
        feasibility_notes=notes,
    )


def lbo_from_valuation(
    blended_ev: float,
    ebitda: float,
    revenue_growth: float,
    ebitda_margin: float,
    capex_pct: float = 0.04,
    tax_rate: float = 0.25,
    leverage_ratio: float = 4.5,
    senior_rate: float = 0.07,
    exit_multiple: Optional[float] = None,
    hold_period: int = 5,
) -> LBOOutput:
    """Convenience wrapper: runs LBO directly from ValuationService blended EV."""
    entry_multiple = blended_ev / ebitda if ebitda > 0 else 10.0
    if exit_multiple is None:
        exit_multiple = entry_multiple

    return compute_lbo(LBOInput(
        entry_ev=blended_ev,
        entry_ebitda=ebitda,
        revenue_growth=revenue_growth,
        ebitda_margin=ebitda_margin,
        capex_pct=capex_pct,
        tax_rate=tax_rate,
        leverage_ratio=min(leverage_ratio, 6.0),
        senior_rate=senior_rate,
        exit_multiple=exit_multiple,
        hold_period=hold_period,
    ))


def _irr(equity_in: float, equity_out: float, years: int) -> float:
    """Single entry/exit IRR = (exit/entry)^(1/n) − 1."""
    if equity_in <= 0 or equity_out <= 0 or years <= 0:
        return 0.0
    return (equity_out / equity_in) ** (1.0 / years) - 1.0
