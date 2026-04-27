"""
Bear / Base / Bull scenario engine — PE-style full scenario analysis.

Each scenario applies delta assumptions to every key driver:
  - Revenue growth (per year)
  - EBITDA margin
  - WACC
  - Terminal growth
  - Exit multiple (for LBO)

Each scenario produces a full independent DCF + comps + blended valuation.
Output is a football field: Low / Base / High EV per method.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from goldroger.finance.valuation.dcf import DCFInput, compute_dcf
from goldroger.finance.valuation.comps import CompsInput, compute_comps
from goldroger.finance.valuation.transactions import TransactionInput, compute_transaction
from goldroger.finance.valuation.aggregator import compute_weighted_valuation


@dataclass
class ScenarioDeltas:
    """Additive deltas applied to base-case assumptions."""
    name: str                        # "Bear" / "Base" / "Bull"
    label: str                       # Short label for display
    revenue_growth_delta: float      # e.g. -0.05 knocks 5pp off each year's growth
    ebitda_margin_delta: float       # e.g. -0.02 = -200bps
    wacc_delta: float                # e.g. +0.01 = WACC +100bps
    terminal_growth_delta: float     # e.g. -0.005
    exit_multiple_factor: float      # multiplicative, e.g. 0.80 for bear exit


# Standard PE scenario set — calibrated for M&A / buyout analysis
BEAR = ScenarioDeltas(
    name="Bear",
    label="Downside",
    revenue_growth_delta=-0.05,
    ebitda_margin_delta=-0.02,
    wacc_delta=+0.015,
    terminal_growth_delta=-0.005,
    exit_multiple_factor=0.80,
)

BASE = ScenarioDeltas(
    name="Base",
    label="Base Case",
    revenue_growth_delta=0.0,
    ebitda_margin_delta=0.0,
    wacc_delta=0.0,
    terminal_growth_delta=0.0,
    exit_multiple_factor=1.0,
)

BULL = ScenarioDeltas(
    name="Bull",
    label="Upside",
    revenue_growth_delta=+0.05,
    ebitda_margin_delta=+0.02,
    wacc_delta=-0.01,
    terminal_growth_delta=+0.005,
    exit_multiple_factor=1.20,
)

STANDARD_SCENARIOS = [BEAR, BASE, BULL]


@dataclass
class ScenarioResult:
    name: str
    label: str
    dcf_ev: float
    comps_ev_low: float
    comps_ev_mid: float
    comps_ev_high: float
    tx_ev: float
    blended_ev: float
    wacc_used: float
    terminal_growth_used: float
    ebitda_margin_used: float
    revenue_year1: float


@dataclass
class ScenariosOutput:
    bear: ScenarioResult
    base: ScenarioResult
    bull: ScenarioResult

    # Football field ranges per method
    @property
    def dcf_range(self) -> tuple[float, float, float]:
        return self.bear.dcf_ev, self.base.dcf_ev, self.bull.dcf_ev

    @property
    def comps_range(self) -> tuple[float, float, float]:
        return self.bear.comps_ev_mid, self.base.comps_ev_mid, self.bull.comps_ev_mid

    @property
    def blended_range(self) -> tuple[float, float, float]:
        return self.bear.blended_ev, self.base.blended_ev, self.bull.blended_ev


def _apply_scenario(
    base_revenue: list[float],
    base_ebitda_margin: float,
    base_wacc: float,
    base_terminal_growth: float,
    base_comps_low: float,
    base_comps_high: float,
    base_tx_multiple: float,
    tax_rate: float,
    capex_pct: float,
    nwc_pct: float,
    da_pct: Optional[float],
    weights: dict,
    delta: ScenarioDeltas,
) -> ScenarioResult:
    # Apply revenue growth delta to each projected year
    base_growth = (base_revenue[-1] / base_revenue[0]) ** (1 / max(len(base_revenue) - 1, 1)) - 1
    adj_growth = max(-0.10, base_growth + delta.revenue_growth_delta)
    base_year = base_revenue[0]
    adj_revenue = [base_year * (1 + adj_growth) ** i for i in range(1, len(base_revenue) + 1)]

    adj_margin = max(0.01, min(base_ebitda_margin + delta.ebitda_margin_delta, 0.80))
    adj_wacc = max(0.05, min(base_wacc + delta.wacc_delta, 0.25))
    adj_tg = max(0.0, min(base_terminal_growth + delta.terminal_growth_delta, adj_wacc - 0.005))

    dcf_input = DCFInput(
        revenue=adj_revenue,
        ebitda_margin=adj_margin,
        tax_rate=tax_rate,
        capex_pct=capex_pct,
        nwc_pct=nwc_pct,
        wacc=adj_wacc,
        terminal_growth=adj_tg,
        da_pct=da_pct,
    )
    dcf_out = compute_dcf(dcf_input)

    # Comps — scale multiples by exit multiple factor
    adj_low = base_comps_low * delta.exit_multiple_factor
    adj_high = base_comps_high * delta.exit_multiple_factor
    current_revenue = adj_revenue[-1] if adj_revenue else base_revenue[-1]
    current_ebitda = current_revenue * adj_margin

    comps_out = compute_comps(CompsInput(
        metric_value=current_ebitda,
        multiple_range=(adj_low, adj_high),
    ))

    # Transaction comps (EV/Revenue)
    adj_tx = base_tx_multiple * delta.exit_multiple_factor
    tx_out = compute_transaction(TransactionInput(
        revenue=current_revenue,
        multiple=adj_tx,
    ))

    blended = compute_weighted_valuation(dcf_out, comps_out, tx_out, weights)

    return ScenarioResult(
        name=delta.name,
        label=delta.label,
        dcf_ev=round(dcf_out.enterprise_value, 1),
        comps_ev_low=round(comps_out.low, 1),
        comps_ev_mid=round(comps_out.mid, 1),
        comps_ev_high=round(comps_out.high, 1),
        tx_ev=round(tx_out.implied_value, 1),
        blended_ev=round(blended.blended, 1),
        wacc_used=adj_wacc,
        terminal_growth_used=adj_tg,
        ebitda_margin_used=adj_margin,
        revenue_year1=adj_revenue[0] if adj_revenue else 0.0,
    )


def run_scenarios(
    base_revenue: list[float],
    base_ebitda_margin: float,
    base_wacc: float,
    base_terminal_growth: float,
    base_comps_low: float,
    base_comps_high: float,
    base_tx_multiple: float,
    tax_rate: float,
    capex_pct: float,
    nwc_pct: float = 0.02,
    da_pct: Optional[float] = None,
    weights: Optional[dict] = None,
    scenarios: Optional[list[ScenarioDeltas]] = None,
) -> ScenariosOutput:
    if weights is None:
        weights = {"dcf": 0.5, "comps": 0.3, "transactions": 0.2}
    if scenarios is None:
        scenarios = STANDARD_SCENARIOS

    results = [
        _apply_scenario(
            base_revenue, base_ebitda_margin, base_wacc, base_terminal_growth,
            base_comps_low, base_comps_high, base_tx_multiple,
            tax_rate, capex_pct, nwc_pct, da_pct, weights, s,
        )
        for s in scenarios
    ]

    return ScenariosOutput(
        bear=results[0],
        base=results[1],
        bull=results[2],
    )
