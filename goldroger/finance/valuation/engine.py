from dataclasses import dataclass
from typing import Dict, Any

from goldroger.finance.valuation.dcf import DCFInput, compute_dcf
from goldroger.finance.core.wacc import WACCInput, compute_wacc


@dataclass
class ValuationResult:
    enterprise_value: float
    dcf_value: float
    wacc: float


def build_wacc(context: Dict[str, Any]) -> float:
    """
    Converts raw context into WACC.
    In real systems, this would use:
    - CAPM (risk-free rate, beta, equity risk premium)
    - market data feeds
    """

    inp = WACCInput(
        cost_of_equity=context.get("cost_of_equity", 0.10),
        cost_of_debt=context.get("cost_of_debt", 0.05),
        tax_rate=context.get("tax_rate", 0.25),
        equity_value=context.get("equity_value", 1e9),
        debt_value=context.get("debt_value", 0.0),
    )

    return compute_wacc(inp)


def build_dcf_input(financials: Dict[str, Any], wacc: float) -> DCFInput:
    """
    Converts structured financials into DCF input.
    """

    return DCFInput(
        revenue=financials["revenue_projections"],
        ebitda_margin=financials["ebitda_margin"],
        tax_rate=financials.get("tax_rate", 0.25),
        capex_pct=financials.get("capex_pct", 0.05),
        nwc_pct=financials.get("nwc_pct", 0.02),
        wacc=wacc,
        terminal_growth=financials.get("terminal_growth", 0.02),
    )


def run_valuation(financials: Dict[str, Any], context: Dict[str, Any]) -> ValuationResult:
    """
    MAIN ENTRY POINT:
    deterministic valuation pipeline
    """

    wacc = build_wacc(context)
    dcf_input = build_dcf_input(financials, wacc)

    dcf_result = compute_dcf(dcf_input)

    return ValuationResult(
        enterprise_value=dcf_result.enterprise_value,
        dcf_value=dcf_result.enterprise_value,
        wacc=wacc
    )