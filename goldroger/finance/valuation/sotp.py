"""
Sum-of-the-Parts (SOTP) valuation.

Used for conglomerates and diversified companies where different business
segments warrant different valuation multiples.

Each segment is valued independently using EV/EBITDA or EV/Revenue,
then summed. A holding-company discount (typically 10–20%) is applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from goldroger.data.sector_multiples import get_sector_multiples


@dataclass
class Segment:
    name: str
    revenue: float            # USD millions
    ebitda_margin: float      # decimal
    sector: str               # used to look up multiples
    multiple_override: Optional[float] = None   # EV/EBITDA override if known


@dataclass
class SegmentValue:
    name: str
    ebitda: float
    multiple_used: float
    ev: float
    sector: str


@dataclass
class SOTPOutput:
    segments: list[SegmentValue]
    gross_ev: float                    # sum of segment EVs
    holdco_discount_pct: float         # e.g. 0.15 = 15%
    holdco_discount: float             # absolute USD millions
    net_ev: float                      # gross_ev × (1 − discount)
    net_debt: float                    # to derive equity value
    equity_value: float
    notes: list[str] = field(default_factory=list)


def compute_sotp(
    segments: list[Segment],
    net_debt: float = 0.0,
    holdco_discount_pct: float = 0.15,
) -> SOTPOutput:
    """
    Value each segment at sector mid EV/EBITDA, then apply holding discount.
    """
    notes: list[str] = []
    valued: list[SegmentValue] = []

    for seg in segments:
        ebitda = seg.revenue * seg.ebitda_margin
        if seg.multiple_override is not None:
            multiple = seg.multiple_override
        else:
            sm = get_sector_multiples(seg.sector)
            multiple = sm.ev_ebitda[1]  # mid

        ev = ebitda * multiple
        valued.append(SegmentValue(
            name=seg.name,
            ebitda=round(ebitda, 1),
            multiple_used=multiple,
            ev=round(ev, 1),
            sector=seg.sector,
        ))
        notes.append(
            f"{seg.name}: EBITDA ${ebitda:.0f}M × {multiple:.1f}x ({seg.sector}) = ${ev:.0f}M"
        )

    gross_ev = sum(s.ev for s in valued)
    discount = gross_ev * holdco_discount_pct
    net_ev = gross_ev - discount
    equity_value = max(net_ev - net_debt, 0.0)

    notes.append(
        f"Gross EV ${gross_ev:.0f}M − {holdco_discount_pct:.0%} holdco discount "
        f"= Net EV ${net_ev:.0f}M"
    )

    return SOTPOutput(
        segments=valued,
        gross_ev=round(gross_ev, 1),
        holdco_discount_pct=holdco_discount_pct,
        holdco_discount=round(discount, 1),
        net_ev=round(net_ev, 1),
        net_debt=round(net_debt, 1),
        equity_value=round(equity_value, 1),
        notes=notes,
    )
