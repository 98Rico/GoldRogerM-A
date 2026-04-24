from dataclasses import dataclass
from typing import List, Dict


@dataclass
class CompsInput:
    metric_value: float  # e.g. EBITDA or Revenue
    multiple_range: tuple  # (low, high)


@dataclass
class CompsOutput:
    low: float
    mid: float
    high: float


def compute_comps(inp: CompsInput) -> CompsOutput:
    low_mult, high_mult = inp.multiple_range
    mid_mult = (low_mult + high_mult) / 2

    return CompsOutput(
        low=inp.metric_value * low_mult,
        mid=inp.metric_value * mid_mult,
        high=inp.metric_value * high_mult
    )