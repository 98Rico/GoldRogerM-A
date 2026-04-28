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
    w_dcf = weights.get("dcf", 0.5)
    w_comps = weights.get("comps", 0.3)
    w_tx = weights.get("transactions", 0.2)

    # Normalize weights so they always sum to 1.0 regardless of how they were set
    total = w_dcf + w_comps + w_tx
    if total > 0 and abs(total - 1.0) > 0.001:
        w_dcf /= total
        w_comps /= total
        w_tx /= total

    dcf_ev = dcf.enterprise_value
    tx_ev = transactions.implied_value

    low = dcf_ev * w_dcf + comps.low * w_comps + tx_ev * w_tx
    mid = dcf_ev * w_dcf + comps.mid * w_comps + tx_ev * w_tx
    high = dcf_ev * w_dcf + comps.high * w_comps + tx_ev * w_tx

    return ValuationResult(
        low=low,
        mid=mid,
        high=high,
        blended=mid,  # mid is the central estimate; (low+mid+high)/3 biases toward comps range
    )