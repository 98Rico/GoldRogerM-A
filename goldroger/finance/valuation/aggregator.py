from dataclasses import dataclass
from typing import Dict


@dataclass
class ValuationResult:
    low: float
    mid: float
    high: float
    blended: float


def compute_weighted_valuation(dcf, comps, transactions,
                                weights: Dict[str, float]) -> ValuationResult:

    low = (
        dcf.enterprise_value * weights["dcf"] +
        comps.low * weights["comps"] +
        transactions.implied_value * weights["transactions"]
    )

    mid = (
        dcf.enterprise_value * weights["dcf"] +
        comps.mid * weights["comps"] +
        transactions.implied_value * weights["transactions"]
    )

    high = (
        dcf.enterprise_value * weights["dcf"] +
        comps.high * weights["comps"] +
        transactions.implied_value * weights["transactions"]
    )

    return ValuationResult(
        low=low,
        mid=mid,
        high=high,
        blended=(low + mid + high) / 3
    )