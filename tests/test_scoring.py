"""Tests for goldroger.ma.scoring — IC scorecard engine."""
import pytest
from goldroger.ma.scoring import (
    compute_ic_score,
    auto_score_from_valuation,
    score_from_analysis,
    ICScoreInput,
)


def _neutral_input(**overrides) -> ICScoreInput:
    base = dict(strategy=5.0, synergies=5.0, financial=5.0, lbo=5.0, integration=5.0, risk=5.0)
    base.update(overrides)
    return ICScoreInput(**base)


def test_strong_buy_all_high_dimensions():
    """All dimensions high → STRONG BUY."""
    out = score_from_analysis(
        strategy=9.0, synergies=8.5, financial=9.0,
        lbo=8.5, integration=8.0, risk=8.0,
        company="ACME",
    )
    assert out.recommendation == "STRONG BUY"
    assert out.ic_score >= 75


def test_buy_moderate_scores():
    """Moderate scores should yield at least BUY."""
    out = score_from_analysis(
        strategy=7.0, synergies=6.5, financial=7.0,
        lbo=6.5, integration=6.0, risk=6.5,
    )
    assert out.recommendation in ("STRONG BUY", "BUY")
    assert out.ic_score >= 60


def test_auto_score_high_irr_and_upside():
    """High IRR + high equity upside should give a strong score."""
    class _FakeLBO:
        irr = 0.30
        is_feasible = True

    out = auto_score_from_valuation(_FakeLBO(), upside_pct=0.35, company="Test")
    assert out.recommendation in ("STRONG BUY", "BUY")
    assert out.ic_score >= 60


def test_watch_for_low_irr():
    """Near-hurdle IRR + near-zero equity upside → modest score."""
    class _FakeLBO:
        irr = 0.12
        is_feasible = True

    out = auto_score_from_valuation(_FakeLBO(), upside_pct=0.02)
    assert out.ic_score < 75


def test_no_go_when_gate_fails():
    """Any dimension below gate threshold triggers NO GO regardless of total."""
    out = compute_ic_score(_neutral_input(lbo=1.5))  # lbo gate = 2.0
    assert out.recommendation == "NO GO"
    assert out.ic_score == 0.0
    assert len(out.gates_failed) > 0


def test_no_go_infeasible_lbo():
    class _FakeLBO:
        irr = 0.05
        is_feasible = False

    out = auto_score_from_valuation(_FakeLBO(), upside_pct=-0.20)
    assert out.recommendation == "NO GO"


def test_score_clamped_0_to_100():
    out = compute_ic_score(_neutral_input(
        strategy=10, synergies=10, financial=10, lbo=10, integration=10, risk=10
    ))
    assert 0 <= out.ic_score <= 100


def test_next_steps_populated():
    out = compute_ic_score(_neutral_input(strategy=8, synergies=8, financial=8,
                                          lbo=8, integration=8, risk=8))
    assert len(out.next_steps) > 0


def test_rationale_contains_company():
    inp = ICScoreInput(strategy=7, synergies=6, financial=7, lbo=6,
                       integration=6, risk=6, company="Acme Corp")
    out = compute_ic_score(inp)
    assert "Acme Corp" in out.rationale
