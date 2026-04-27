from goldroger.finance.core.scenarios import run_scenarios, BEAR, BASE, BULL


def _base_kwargs():
    return dict(
        base_revenue=[100_000, 108_000, 116_640, 125_971, 136_049],
        base_ebitda_margin=0.25,
        base_wacc=0.10,
        base_terminal_growth=0.03,
        base_comps_low=8.0,
        base_comps_high=14.0,
        base_tx_multiple=2.0,
        tax_rate=0.25,
        capex_pct=0.04,
    )


def test_scenarios_ordering():
    out = run_scenarios(**_base_kwargs())
    assert out.bull.blended_ev > out.base.blended_ev > out.bear.blended_ev, \
        "Bull EV must exceed base, which must exceed bear"


def test_scenarios_dcf_ordering():
    out = run_scenarios(**_base_kwargs())
    assert out.bull.dcf_ev > out.base.dcf_ev > out.bear.dcf_ev


def test_scenarios_wacc_ordering():
    out = run_scenarios(**_base_kwargs())
    assert out.bear.wacc_used > out.base.wacc_used > out.bull.wacc_used


def test_scenarios_margin_ordering():
    out = run_scenarios(**_base_kwargs())
    assert out.bull.ebitda_margin_used > out.base.ebitda_margin_used > out.bear.ebitda_margin_used


def test_football_field_range():
    out = run_scenarios(**_base_kwargs())
    bear_ev, base_ev, bull_ev = out.blended_range
    assert bear_ev < base_ev < bull_ev
