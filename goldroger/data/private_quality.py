"""
Deterministic private-company data quality merge.

Goal:
- merge multiple provider outputs into one consistent MarketData record
- prioritize verified registry/filing data
- down-weight low-confidence and weak-source estimates
- trim outliers before selecting final revenue
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from goldroger.data.fetcher import MarketData


@dataclass
class RevenueCandidate:
    source: str
    confidence: str
    revenue_m: float
    weight: float


@dataclass
class PrivateMergeResult:
    market_data: Optional[MarketData]
    candidates: list[RevenueCandidate]
    dropped_outliers: list[RevenueCandidate]
    notes: list[str]


_SOURCE_WEIGHT: dict[str, float] = {
    "manual_user_input": 1.30,
    "manual (user input)": 1.30,  # backward compatibility with older source tag
    "pappers": 1.00,
    "sec_edgar": 0.95,
    "companies_house": 0.92,
    "handelsregister": 0.85,
    "infogreffe": 0.55,
    "registro_mercantil": 0.45,
    "kvk": 0.45,
    "crunchbase": 0.60,
    "triangulation": 0.55,
}

_CONFIDENCE_WEIGHT: dict[str, float] = {
    "verified": 1.00,
    "estimated": 0.65,
    "inferred": 0.35,
}

_MIN_REVENUE_M = 0.1
_MAX_REVENUE_M = 2_000_000.0
_MAX_DEVIATION_PCT = 0.65


def _norm_source(source: str) -> str:
    return (source or "").strip().lower()


def _source_weight(source: str) -> float:
    s = _norm_source(source)
    if s in _SOURCE_WEIGHT:
        return _SOURCE_WEIGHT[s]
    # Any custom provider/source starts neutral
    return 0.55


def _confidence_weight(confidence: str) -> float:
    return _CONFIDENCE_WEIGHT.get((confidence or "").strip().lower(), 0.35)


def _weighted_median(cands: list[RevenueCandidate]) -> float:
    ordered = sorted(cands, key=lambda c: c.revenue_m)
    total_w = sum(max(c.weight, 0.01) for c in ordered)
    threshold = total_w / 2.0
    cum = 0.0
    for c in ordered:
        cum += max(c.weight, 0.01)
        if cum >= threshold:
            return c.revenue_m
    return ordered[-1].revenue_m


def _weighted_mean(cands: list[RevenueCandidate]) -> float:
    num = sum(c.revenue_m * max(c.weight, 0.01) for c in cands)
    den = sum(max(c.weight, 0.01) for c in cands)
    if den <= 0:
        return cands[0].revenue_m
    return num / den


def _build_candidates(records: list[MarketData]) -> list[RevenueCandidate]:
    out: list[RevenueCandidate] = []
    seen: set[tuple[str, int]] = set()
    for r in records:
        rev = r.revenue_ttm
        if rev is None:
            continue
        try:
            value = float(rev)
        except (TypeError, ValueError):
            continue
        if value < _MIN_REVENUE_M or value > _MAX_REVENUE_M:
            continue
        source = _norm_source(r.data_source)
        key = (source, int(round(value)))
        if key in seen:
            continue
        seen.add(key)
        weight = _source_weight(source) * _confidence_weight(r.confidence)
        out.append(
            RevenueCandidate(
                source=source,
                confidence=(r.confidence or "inferred"),
                revenue_m=value,
                weight=weight,
            )
        )
    return out


def _trim_outliers(cands: list[RevenueCandidate]) -> tuple[list[RevenueCandidate], list[RevenueCandidate]]:
    if len(cands) < 3:
        return cands, []
    center = _weighted_median(cands)
    if center <= 0:
        return cands, []
    kept: list[RevenueCandidate] = []
    dropped: list[RevenueCandidate] = []
    for c in cands:
        dev = abs(c.revenue_m - center) / center
        if dev <= _MAX_DEVIATION_PCT:
            kept.append(c)
        else:
            dropped.append(c)
    # Avoid over-pruning
    if len(kept) < 2:
        return cands, []
    return kept, dropped


def merge_private_market_data(
    base: Optional[MarketData],
    additional: list[MarketData] | None = None,
) -> PrivateMergeResult:
    """
    Merge private-company provider results into one deterministic record.
    """
    records: list[MarketData] = []
    if base is not None:
        records.append(base)
    if additional:
        records.extend([r for r in additional if r is not None])

    if not records:
        return PrivateMergeResult(
            market_data=None,
            candidates=[],
            dropped_outliers=[],
            notes=["No private-company provider data available."],
        )

    # Pick best non-revenue anchor (used when no revenue candidate exists).
    def _record_score(r: MarketData) -> float:
        return _source_weight(r.data_source) * _confidence_weight(r.confidence)

    anchor = max(records, key=_record_score)

    candidates = _build_candidates(records)
    notes: list[str] = []

    if not candidates:
        # Keep best structural fields (sector/company) even without revenue.
        merged = anchor
        if not merged.sector:
            for r in sorted(records, key=_record_score, reverse=True):
                if r.sector:
                    merged.sector = r.sector
                    break
        notes.append("No revenue candidates found in selected data sources.")
        return PrivateMergeResult(
            market_data=merged,
            candidates=[],
            dropped_outliers=[],
            notes=notes,
        )

    # Explicit user override is always authoritative.
    manual = [c for c in candidates if c.source in {"manual_user_input", "manual (user input)"}]
    if manual:
        selected = manual[0]
        source_record = next(
            (
                r
                for r in records
                if _norm_source(r.data_source) == selected.source
                and r.revenue_ttm is not None
            ),
            anchor,
        )
        merged = source_record
        merged.revenue_ttm = round(selected.revenue_m, 1)
        merged.data_source = "manual_user_input"
        merged.confidence = "manual"
        notes.append("Manual revenue override applied.")
        return PrivateMergeResult(
            market_data=merged,
            candidates=manual,
            dropped_outliers=[],
            notes=notes,
        )

    kept, dropped = _trim_outliers(candidates)
    if dropped:
        dropped_names = ", ".join(f"{d.source}:{d.revenue_m:.0f}M" for d in dropped[:4])
        notes.append(f"Dropped outlier revenue candidates: {dropped_names}.")

    # Prefer verified cohort when available after trimming.
    verified = [c for c in kept if c.confidence.lower() == "verified"]
    active = verified if verified else kept
    if verified:
        notes.append(f"Using verified revenue cohort ({len(verified)} source(s)).")

    final_revenue = round(_weighted_mean(active), 1)
    final_center = _weighted_median(active)

    # Attach to the closest high-weight source for provenance.
    selected = sorted(
        active,
        key=lambda c: (abs(c.revenue_m - final_center), -c.weight),
    )[0]

    # Start from the selected record for fields provenance.
    source_record = next(
        (
            r
            for r in records
            if _norm_source(r.data_source) == selected.source
            and r.revenue_ttm is not None
            and abs(float(r.revenue_ttm) - selected.revenue_m) < 0.51
        ),
        anchor,
    )
    merged = source_record
    merged.revenue_ttm = final_revenue
    merged.data_source = selected.source
    merged.confidence = "verified" if verified else selected.confidence

    # Fill missing fields from highest-quality remaining records.
    for r in sorted(records, key=_record_score, reverse=True):
        if not merged.sector and r.sector:
            merged.sector = r.sector
        if merged.ebitda_margin is None and r.ebitda_margin is not None:
            merged.ebitda_margin = r.ebitda_margin
        if merged.net_margin is None and r.net_margin is not None:
            merged.net_margin = r.net_margin
        if merged.gross_margin is None and r.gross_margin is not None:
            merged.gross_margin = r.gross_margin

    notes.append(
        f"Revenue quality merge selected ${final_revenue:.1f}M from {len(active)} candidate(s)."
    )
    return PrivateMergeResult(
        market_data=merged,
        candidates=active,
        dropped_outliers=dropped,
        notes=notes,
    )
