"""
WACC computation with CAPM-based cost of equity.

Hierarchy:
  1. Full CAPM: Re = Rf + β × ERP, then classic WACC formula
  2. Sector beta fallback when company beta is unavailable
  3. Pure sector-default WACC when capital structure data is missing
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Macroeconomic constants (update periodically)
RISK_FREE_RATE: float = 0.045        # 10-yr US Treasury proxy, April 2025
EQUITY_RISK_PREMIUM: float = 0.055   # Damodaran long-run ERP


@dataclass
class WACCInput:
    cost_of_equity: float   # decimal, e.g. 0.10
    cost_of_debt: float     # decimal, e.g. 0.05
    tax_rate: float         # decimal, e.g. 0.25
    equity_value: float     # USD millions (market cap)
    debt_value: float       # USD millions (net debt, or 0 if none)


def compute_wacc(inp: WACCInput) -> float:
    """Classic WACC = (E/V)×Re + (D/V)×Rd×(1-T)."""
    e = max(inp.equity_value, 0.0)
    d = max(inp.debt_value, 0.0)
    v = e + d

    if v == 0:
        raise ValueError("Equity + Debt cannot be zero")

    wacc = (
        (e / v) * inp.cost_of_equity
        + (d / v) * inp.cost_of_debt * (1 - inp.tax_rate)
    )
    return _clamp_wacc(wacc)


def capm_cost_of_equity(
    beta: float,
    risk_free_rate: float = RISK_FREE_RATE,
    equity_risk_premium: float = EQUITY_RISK_PREMIUM,
) -> float:
    """Re = Rf + β × ERP."""
    re = risk_free_rate + beta * equity_risk_premium
    # Clamp to reasonable range: 6% – 25%
    return max(0.06, min(re, 0.25))


def derive_cost_of_debt(
    interest_expense: Optional[float],
    total_debt: Optional[float],
    fallback: float = 0.055,
) -> float:
    """
    Rd = Interest Expense / Total Debt.
    Uses fallback (≈ BB-rated corporate bond yield) when data is missing.
    """
    if interest_expense and total_debt and total_debt > 0:
        rd = interest_expense / total_debt
        # Clamp: 2% – 15%
        return max(0.02, min(rd, 0.15))
    return fallback


def compute_capm_wacc(
    beta: float,
    market_cap: float,
    net_debt: float,
    tax_rate: float,
    interest_expense: Optional[float] = None,
    total_debt: Optional[float] = None,
    risk_free_rate: float = RISK_FREE_RATE,
    equity_risk_premium: float = EQUITY_RISK_PREMIUM,
) -> float:
    """
    Full CAPM-derived WACC given real market data.
    Re from CAPM, Rd from interest/debt ratio, weights from market cap + net debt.
    """
    re = capm_cost_of_equity(beta, risk_free_rate, equity_risk_premium)
    rd = derive_cost_of_debt(interest_expense, total_debt)

    inp = WACCInput(
        cost_of_equity=re,
        cost_of_debt=rd,
        tax_rate=tax_rate,
        equity_value=max(market_cap, 0.0),
        debt_value=max(net_debt, 0.0),
    )
    return compute_wacc(inp)


def _clamp_wacc(wacc: float) -> float:
    """WACC must be between 5% and 25% to be economically sensible."""
    return max(0.05, min(wacc, 0.25))
