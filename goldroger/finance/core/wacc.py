from dataclasses import dataclass


@dataclass
class WACCInput:
    cost_of_equity: float   # e.g. 0.10 = 10%
    cost_of_debt: float     # e.g. 0.05
    tax_rate: float         # e.g. 0.25
    equity_value: float     # market cap or estimate
    debt_value: float       # net debt


def compute_wacc(inp: WACCInput) -> float:
    """
    WACC = (E/V)*Re + (D/V)*Rd*(1-T)
    """
    e = inp.equity_value
    d = inp.debt_value
    v = e + d

    if v == 0:
        raise ValueError("Equity + Debt cannot be zero")

    weight_e = e / v
    weight_d = d / v

    wacc = (
        weight_e * inp.cost_of_equity +
        weight_d * inp.cost_of_debt * (1 - inp.tax_rate)
    )

    return wacc