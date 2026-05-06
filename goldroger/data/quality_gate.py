"""
Data quality gate for valuation readiness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from goldroger.data.fetcher import MarketData


@dataclass
class DataQualityReport:
    score: int
    tier: str
    is_blocked: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)


def assess_data_quality(
    company_type: str,
    market_data: Optional[MarketData],
    financials: dict,
    market_analysis: Optional[dict] = None,
    proxy_growth_used: bool = False,
    peer_count: int = 0,
    market_analysis_failed: bool = False,
    market_analysis_degraded: bool = False,
    market_analysis_skipped_quick: bool = False,
    dcf_sanity_failed: bool = False,
) -> DataQualityReport:
    score = 100
    blockers: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {}
    has_estimated_inputs = False

    rev = _num(
        (market_data.revenue_ttm if market_data else None)
        or financials.get("revenue_current")
    )
    ebitda_margin = _num(
        (market_data.ebitda_margin if market_data else None)
        or financials.get("ebitda_margin")
    )

    if rev is None or rev <= 0:
        score -= 45
        blockers.append("Missing revenue")
        checks["revenue"] = "missing"
    else:
        checks["revenue"] = "ok"

    if ebitda_margin is None:
        score -= 12
        warnings.append("Missing EBITDA margin (fallback will be used)")
        checks["ebitda_margin"] = "missing"
    else:
        if ebitda_margin > 1.0:
            ebitda_margin = ebitda_margin / 100.0
        if ebitda_margin < -0.5 or ebitda_margin > 0.8:
            score -= 15
            warnings.append("EBITDA margin outside sanity bounds")
            checks["ebitda_margin"] = "out_of_bounds"
        else:
            checks["ebitda_margin"] = "ok"

    if company_type == "public":
        score, has_estimated_inputs = _score_public(
            market_data, checks, warnings, blockers, score, has_estimated_inputs
        )
    else:
        score, has_estimated_inputs = _score_private(
            market_data, checks, warnings, score, has_estimated_inputs
        )
    if market_analysis_skipped_quick:
        checks["market_context"] = "skipped_quick_mode"
        warnings.append("Market analysis skipped in quick mode")
        # Small penalty only: this is intentional behavior in quick mode.
        score -= 5
    else:
        score, has_estimated_inputs = _score_market_context(
            market_analysis, checks, warnings, score, has_estimated_inputs
        )

    if proxy_growth_used:
        score -= 5
        has_estimated_inputs = True
        checks["forward_growth_source"] = "proxy"
        warnings.append("Forward growth uses proxy signal (not analyst revenue estimate)")
    elif market_data and market_data.forward_revenue_growth is not None:
        checks["forward_growth_source"] = "analyst_estimate"

    if company_type == "public":
        if peer_count <= 0:
            score -= 20
            warnings.append("No validated peer comps")
            checks["peer_set"] = "missing"
        elif peer_count < 3:
            score -= 12
            warnings.append("Weak peer comps set (<3)")
            checks["peer_set"] = "weak"
        elif peer_count < 5:
            score -= 8
            warnings.append("Expanded peer set (<5) with reduced confidence")
            checks["peer_set"] = "expanded"
        else:
            checks["peer_set"] = "ok"
    if market_analysis_failed:
        score -= 15
        warnings.append("Market analysis failed")
        checks["market_analysis"] = "failed"
    elif market_analysis_degraded:
        score -= 8
        warnings.append("Market analysis degraded (no usable TAM/growth context)")
        checks["market_analysis"] = "degraded"
    elif market_analysis_skipped_quick:
        checks["market_analysis"] = "skipped_quick_mode"
        if score > 75:
            score = 75
    if dcf_sanity_failed:
        score -= 12
        warnings.append("DCF sanity check failed")
        checks["dcf_sanity"] = "failed"

    score = max(0, min(100, score))
    # Credibility cap: estimates present => max 90. Perfect 100 reserved for fully verified sets.
    if has_estimated_inputs and score > 90:
        score = 90
    tier = _tier(score)
    is_blocked = "Missing revenue" in blockers
    return DataQualityReport(
        score=score,
        tier=tier,
        is_blocked=is_blocked,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
    )


def _score_public(market_data, checks, warnings, blockers, score: int, has_estimated_inputs: bool) -> tuple[int, bool]:
    if market_data is None:
        score -= 35
        blockers.append("Missing market data")
        checks["market_data"] = "missing"
        return score, has_estimated_inputs

    checks["market_data"] = "ok"

    if market_data.market_cap is None or market_data.market_cap <= 0:
        score -= 12
        warnings.append("Missing market cap")
        checks["market_cap"] = "missing"
    else:
        checks["market_cap"] = "ok"

    if market_data.ev_ebitda_market is None:
        score -= 8
        warnings.append("Missing live EV/EBITDA multiple")
        checks["ev_ebitda_market"] = "missing"
    else:
        checks["ev_ebitda_market"] = "ok"

    if market_data.beta is None:
        score -= 8
        warnings.append("Missing beta (WACC fallback likely)")
        checks["beta"] = "missing"
    else:
        checks["beta"] = "ok"

    return score, has_estimated_inputs


def _score_private(market_data, checks, warnings, score: int, has_estimated_inputs: bool) -> tuple[int, bool]:
    if market_data is None:
        score -= 25
        warnings.append("No registry/provider market data found")
        checks["provider_record"] = "missing"
        has_estimated_inputs = True
        return score, has_estimated_inputs

    checks["provider_record"] = "ok"
    conf = (market_data.confidence or "inferred").lower()
    if conf == "verified":
        checks["confidence"] = "verified"
    elif conf == "estimated":
        score -= 8
        checks["confidence"] = "estimated"
        has_estimated_inputs = True
    else:
        score -= 15
        checks["confidence"] = "inferred"
        warnings.append("Private revenue confidence is inferred")
        has_estimated_inputs = True
    return score, has_estimated_inputs


def _score_market_context(
    market_analysis, checks, warnings, score: int, has_estimated_inputs: bool
) -> tuple[int, bool]:
    if not isinstance(market_analysis, dict):
        checks["market_context"] = "not_provided"
        return score, has_estimated_inputs

    checks["market_context"] = "ok"
    market_size = market_analysis.get("market_size")
    market_growth = market_analysis.get("market_growth")
    market_segment = market_analysis.get("market_segment")
    key_trends = market_analysis.get("key_trends")

    if _text_missing(market_size):
        score -= 10
        warnings.append("Missing TAM / market size context")
        checks["market_size"] = "missing"
    else:
        checks["market_size"] = "estimated"
        score -= 5
        has_estimated_inputs = True

    if _text_missing(market_growth):
        score -= 8
        warnings.append("Missing market growth context")
        checks["market_growth"] = "missing"
    else:
        checks["market_growth"] = "estimated"
        score -= 5
        has_estimated_inputs = True

    if _text_missing(market_segment):
        score -= 6
        warnings.append("Missing market segment definition")
        checks["market_segment"] = "missing"
    else:
        checks["market_segment"] = "ok"

    if not isinstance(key_trends, list) or len([x for x in key_trends if str(x).strip()]) < 2:
        score -= 4
        warnings.append("Missing key trend depth (need 2+ material trends)")
        checks["key_trends"] = "thin"
    else:
        checks["key_trends"] = "ok"

    return score, has_estimated_inputs


def _tier(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _num(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip().replace(",", "")
        if s.endswith("%"):
            return float(s[:-1]) / 100.0
        return float(s)
    except Exception:
        return None


def _text_missing(v) -> bool:
    if v is None:
        return True
    s = str(v).strip().lower()
    if not s:
        return True
    for token in ("n/a", "not available", "unavailable", "none", "unknown"):
        if token in s:
            return True
    return False
