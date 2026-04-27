"""
Investment Committee (IC) scoring engine for M&A deals.

Replaces the previous simple weighted sum with a structured IC scorecard
that mirrors real PE / strategic M&A decision-making.

Score dimensions (0–10 each):
  - strategy:     strategic rationale, sector fit, competitive positioning
  - synergies:    revenue + cost + capital synergy potential
  - financial:    valuation attractiveness, FCF quality, balance sheet
  - lbo:          LBO feasibility (IRR vs hurdle, leverage, debt serviceability)
  - integration:  complexity, cultural fit, execution risk
  - risk:         macro, regulatory, key-man, customer concentration

IC recommendation:
  >= 75  → STRONG BUY  (priority deal, proceed to IC memo)
  >= 60  → BUY         (advance to LOI / deeper diligence)
  >= 45  → WATCH       (monitor, wait for better entry / more data)
  <  45  → NO GO       (pass — risk-adjusted return insufficient)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ICScoreInput:
    # Core dimensions (each 0–10, float)
    strategy: float       # strategic rationale & fit
    synergies: float      # synergy quality & size
    financial: float      # financial attractiveness
    lbo: float            # LBO / leverage feasibility
    integration: float    # integration complexity (inverse: 10 = easy)
    risk: float           # risk profile (inverse: 10 = low risk)

    # Optional deal metadata (used for notes)
    company: str = ""
    acquirer: str = ""
    sector: str = ""
    irr: Optional[float] = None          # from LBO model
    upside_pct: Optional[float] = None   # from equity valuation
    lbo_feasible: Optional[bool] = None


@dataclass
class ICScoreOutput:
    ic_score: float              # 0–100
    recommendation: str          # STRONG BUY / BUY / WATCH / NO GO
    dimension_scores: dict[str, float]
    weighted_scores: dict[str, float]
    gates_failed: list[str]      # hard blockers (e.g. negative IRR)
    rationale: str
    next_steps: list[str]


# Dimension weights (must sum to 1.0)
_WEIGHTS: dict[str, float] = {
    "strategy":    0.25,
    "synergies":   0.20,
    "financial":   0.20,
    "lbo":         0.15,
    "integration": 0.10,
    "risk":        0.10,
}

_GATES = {
    "lbo": 2.0,       # minimum LBO score (below = hard NO GO)
    "risk": 2.0,      # minimum risk score
    "financial": 2.0, # minimum financial score
}


def compute_ic_score(inp: ICScoreInput) -> ICScoreOutput:
    scores = {
        "strategy":    max(0.0, min(float(inp.strategy), 10.0)),
        "synergies":   max(0.0, min(float(inp.synergies), 10.0)),
        "financial":   max(0.0, min(float(inp.financial), 10.0)),
        "lbo":         max(0.0, min(float(inp.lbo), 10.0)),
        "integration": max(0.0, min(float(inp.integration), 10.0)),
        "risk":        max(0.0, min(float(inp.risk), 10.0)),
    }

    weighted = {dim: round(scores[dim] * _WEIGHTS[dim] * 10, 2) for dim in scores}
    raw_total = sum(weighted.values())

    # Hard gates
    gates_failed: list[str] = []
    for dim, threshold in _GATES.items():
        if scores[dim] < threshold:
            gates_failed.append(
                f"{dim.capitalize()} score {scores[dim]:.1f}/10 below minimum {threshold:.0f}/10"
            )

    ic_score = 0.0 if gates_failed else round(raw_total, 1)

    rec = _recommendation(ic_score, gates_failed)
    rationale = _build_rationale(inp, scores, ic_score, gates_failed)
    next_steps = _next_steps(rec, inp)

    return ICScoreOutput(
        ic_score=ic_score,
        recommendation=rec,
        dimension_scores={k: round(v, 1) for k, v in scores.items()},
        weighted_scores=weighted,
        gates_failed=gates_failed,
        rationale=rationale,
        next_steps=next_steps,
    )


def score_from_ma_agents(
    strategic_fit,       # StrategicFit pydantic model
    due_diligence,       # DueDiligence pydantic model
    lbo_output=None,     # LBOOutput | None
    upside_pct: Optional[float] = None,
    company: str = "",
    acquirer: str = "",
    sector: str = "",
) -> ICScoreOutput:
    """
    Derive IC scores directly from M&A agent outputs.
    Eliminates the 5.0/10 neutral defaults — every dimension gets a real signal.
    """
    # ── Strategy: from strategic_fit.fit_score ────────────────────────────
    fit = getattr(strategic_fit, "fit_score", None) or ""
    fit_str = str(fit).lower()
    if "high" in fit_str or "strong" in fit_str or "excellent" in fit_str:
        strategy = 8.5
    elif "medium" in fit_str or "moderate" in fit_str or "good" in fit_str:
        strategy = 6.5
    elif "low" in fit_str or "weak" in fit_str or "poor" in fit_str:
        strategy = 3.0
    else:
        strategy = 5.5

    # ── Synergies: from strategic_fit.key_synergies count + quality ───────
    synergies_list = getattr(strategic_fit, "key_synergies", []) or []
    n_syn = len(synergies_list)
    high_impact = sum(
        1 for s in synergies_list
        if "high" in str(getattr(s, "est_impact", "")).lower()
        or "significant" in str(getattr(s, "description", "")).lower()
    )
    synergies = min(3.0 + n_syn * 0.8 + high_impact * 0.6, 10.0)

    # ── Integration: from integration_complexity (inverse — easy = 10) ────
    complexity = str(getattr(strategic_fit, "integration_complexity", "")).lower()
    if "high" in complexity or "complex" in complexity or "difficult" in complexity:
        integration = 3.0
    elif "medium" in complexity or "moderate" in complexity:
        integration = 5.5
    elif "low" in complexity or "simple" in complexity or "easy" in complexity:
        integration = 8.0
    else:
        integration = 5.0

    # ── Risk: from due_diligence.red_flags severity (inverse — low risk = 10)
    red_flags = getattr(due_diligence, "red_flags", []) or []
    high_rf = sum(1 for rf in red_flags if str(getattr(rf, "severity", "")).lower() == "high")
    med_rf = sum(1 for rf in red_flags if str(getattr(rf, "severity", "")).lower() in ("medium", "med"))
    risk = max(1.0, 10.0 - high_rf * 2.5 - med_rf * 0.8)

    # ── Financial: from upside_pct (unchanged) ────────────────────────────
    if upside_pct is not None:
        if upside_pct > 0.30:
            financial = 9.0
        elif upside_pct > 0.15:
            financial = 7.5
        elif upside_pct > 0.0:
            financial = 6.0
        elif upside_pct > -0.15:
            financial = 4.5
        else:
            financial = 2.5
    else:
        financial = 5.0

    # ── LBO: from lbo_output.irr ──────────────────────────────────────────
    lbo_score = 5.0
    irr = None
    feasible = None
    if lbo_output is not None:
        irr = lbo_output.irr
        feasible = lbo_output.is_feasible
        if not feasible:
            lbo_score = 1.0
        elif irr >= 0.25:
            lbo_score = 10.0
        elif irr >= 0.20:
            lbo_score = 8.0
        elif irr >= 0.15:
            lbo_score = 6.0
        elif irr >= 0.10:
            lbo_score = 4.0
        else:
            lbo_score = 2.0

    return compute_ic_score(ICScoreInput(
        strategy=strategy,
        synergies=synergies,
        financial=financial,
        lbo=lbo_score,
        integration=integration,
        risk=risk,
        company=company,
        acquirer=acquirer,
        sector=sector,
        irr=irr,
        upside_pct=upside_pct,
        lbo_feasible=feasible,
    ))


def score_from_analysis(
    strategy: float = 5.0,
    synergies: float = 5.0,
    financial: float = 5.0,
    lbo: float = 5.0,
    integration: float = 5.0,
    risk: float = 5.0,
    irr: Optional[float] = None,
    upside_pct: Optional[float] = None,
    lbo_feasible: Optional[bool] = None,
    company: str = "",
    acquirer: str = "",
    sector: str = "",
) -> ICScoreOutput:
    """Convenience wrapper with keyword arguments."""
    return compute_ic_score(ICScoreInput(
        strategy=strategy,
        synergies=synergies,
        financial=financial,
        lbo=lbo,
        integration=integration,
        risk=risk,
        company=company,
        acquirer=acquirer,
        sector=sector,
        irr=irr,
        upside_pct=upside_pct,
        lbo_feasible=lbo_feasible,
    ))


def auto_score_from_valuation(
    lbo_output,          # LBOOutput or None
    upside_pct: Optional[float] = None,
    sector: str = "",
    company: str = "",
) -> ICScoreOutput:
    """
    Auto-derive financial and LBO sub-scores from engine outputs.
    Remaining dimensions default to 5/10 (neutral) — should be overridden
    with LLM-derived strategic scores.
    """
    # Financial score from equity upside
    if upside_pct is not None:
        if upside_pct > 0.30:
            financial = 9.0
        elif upside_pct > 0.15:
            financial = 7.5
        elif upside_pct > 0.0:
            financial = 6.0
        elif upside_pct > -0.15:
            financial = 4.5
        else:
            financial = 2.5
    else:
        financial = 5.0

    # LBO score from IRR
    lbo_score = 5.0
    irr = None
    feasible = None
    if lbo_output is not None:
        irr = lbo_output.irr
        feasible = lbo_output.is_feasible
        if not feasible:
            lbo_score = 1.0
        elif irr >= 0.25:
            lbo_score = 10.0
        elif irr >= 0.20:
            lbo_score = 8.0
        elif irr >= 0.15:
            lbo_score = 6.0
        elif irr >= 0.10:
            lbo_score = 4.0
        else:
            lbo_score = 2.0

    return compute_ic_score(ICScoreInput(
        strategy=5.0,
        synergies=5.0,
        financial=financial,
        lbo=lbo_score,
        integration=5.0,
        risk=5.0,
        company=company,
        sector=sector,
        irr=irr,
        upside_pct=upside_pct,
        lbo_feasible=feasible,
    ))


# ── Private helpers ───────────────────────────────────────────────────────────

def _recommendation(score: float, gates_failed: list[str]) -> str:
    if gates_failed:
        return "NO GO"
    if score >= 75:
        return "STRONG BUY"
    if score >= 60:
        return "BUY"
    if score >= 45:
        return "WATCH"
    return "NO GO"


def _build_rationale(
    inp: ICScoreInput,
    scores: dict[str, float],
    total: float,
    gates: list[str],
) -> str:
    parts: list[str] = []
    company_str = inp.company or "Target"

    if gates:
        parts.append(f"{company_str} fails IC gates: {'; '.join(gates)}.")
    else:
        parts.append(f"{company_str} IC score {total:.0f}/100.")

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:2]
    parts.append(
        "Strengths: " + ", ".join(f"{d} ({s:.0f}/10)" for d, s in top) + "."
    )
    bottom = sorted(scores.items(), key=lambda x: x[1])[:2]
    parts.append(
        "Risks: " + ", ".join(f"{d} ({s:.0f}/10)" for d, s in bottom) + "."
    )

    if inp.irr is not None:
        parts.append(f"LBO IRR: {inp.irr:.1%}.")
    if inp.upside_pct is not None:
        parts.append(f"Equity upside: {inp.upside_pct:+.1%}.")

    return " ".join(parts)


def _next_steps(rec: str, inp: ICScoreInput) -> list[str]:
    base: list[str] = []
    if rec == "STRONG BUY":
        base = [
            "Draft IC investment memo",
            "Initiate management outreach / NDA",
            "Kick off full diligence workstreams",
            "Engage financing banks for debt commitment letters",
        ]
    elif rec == "BUY":
        base = [
            "Submit indication of interest (IOI)",
            "Request management presentation",
            "Conduct preliminary financial diligence",
            "Assess financing availability",
        ]
    elif rec == "WATCH":
        base = [
            "Monitor company performance (quarterly check-in)",
            "Track valuation entry point",
            "Refine synergy case with additional data",
        ]
    else:
        base = [
            "Pass on current opportunity",
            "Document rationale in deal log",
            "Re-evaluate if strategic context or valuation changes",
        ]

    if inp.lbo_feasible is False:
        base.append("Reassess capital structure — current leverage profile impedes LBO.")
    if inp.upside_pct is not None and inp.upside_pct < -0.15:
        base.append("Negotiate entry price down or wait for better valuation entry point.")

    return base
