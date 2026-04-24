from dataclasses import dataclass
from typing import List


@dataclass
class DCFInput:
    revenue: List[float]
    ebitda_margin: float
    tax_rate: float
    capex_pct: float
    nwc_pct: float
    wacc: float
    terminal_growth: float


@dataclass
class DCFOutput:
    free_cash_flows: List[float]
    discounted_cash_flows: List[float]
    terminal_value: float
    enterprise_value: float


def compute_dcf(inp: DCFInput) -> DCFOutput:
    """
    IB-grade deterministic DCF model
    """

    fcf_list = []
    discounted = []

    for i, rev in enumerate(inp.revenue, start=1):

        # 1. Operating profit proxy
        ebitda = rev * inp.ebitda_margin

        # 2. Simplified tax shield (NOPAT approximation)
        nopat = ebitda * (1 - inp.tax_rate)

        # 3. Reinvestment needs
        capex = rev * inp.capex_pct
        nwc = rev * inp.nwc_pct

        # 4. True free cash flow
        fcf = nopat - capex - nwc

        fcf_list.append(fcf)

        # 5. Discounting
        discounted.append(fcf / ((1 + inp.wacc) ** i))

    # -----------------------------
    # Terminal Value (Gordon Growth)
    # -----------------------------

    terminal_fcf = fcf_list[-1] * (1 + inp.terminal_growth)

    terminal_value = terminal_fcf / (inp.wacc - inp.terminal_growth)

    discounted_terminal = terminal_value / ((1 + inp.wacc) ** len(inp.revenue))

    enterprise_value = sum(discounted) + discounted_terminal

    return DCFOutput(
        free_cash_flows=fcf_list,
        discounted_cash_flows=discounted,
        terminal_value=discounted_terminal,
        enterprise_value=enterprise_value
    )