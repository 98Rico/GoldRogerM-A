from goldroger.finance.valuation.lbo import LBOInput, compute_lbo


def _standard_lbo() -> LBOInput:
    return LBOInput(
        entry_ev=500.0,
        entry_ebitda=100.0,
        revenue_growth=0.15,
        ebitda_margin=0.25,
        capex_pct=0.04,
        tax_rate=0.25,
        leverage_ratio=4.5,
        senior_rate=0.07,
        exit_multiple=8.0,
        hold_period=5,
    )


def test_lbo_feasible():
    out = compute_lbo(_standard_lbo())
    assert out.is_feasible, f"Expected feasible, IRR={out.irr:.1%}"


def test_lbo_irr_range():
    out = compute_lbo(_standard_lbo())
    assert 0.15 <= out.irr <= 0.80, f"IRR {out.irr:.1%} out of reasonable range"


def test_lbo_moic_positive():
    out = compute_lbo(_standard_lbo())
    assert out.moic > 1.0, "MOIC must exceed 1x (positive return)"


def test_lbo_high_leverage_infeasible():
    inp = LBOInput(
        entry_ev=1000.0,
        entry_ebitda=50.0,   # 20x leverage — way too high
        revenue_growth=0.05,
        ebitda_margin=0.10,
        capex_pct=0.05,
        tax_rate=0.25,
        leverage_ratio=4.5,
        senior_rate=0.09,
        exit_multiple=6.0,
    )
    out = compute_lbo(inp)
    assert not out.is_feasible, "Over-leveraged deal should be infeasible"


def test_lbo_irr_improves_with_growth():
    base = compute_lbo(_standard_lbo())
    high_growth = LBOInput(**{**_standard_lbo().__dict__, "revenue_growth": 0.30})
    out_high = compute_lbo(high_growth)
    assert out_high.irr > base.irr, "Higher growth should improve IRR"
