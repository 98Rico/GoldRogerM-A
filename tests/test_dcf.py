from goldroger.finance.valuation.dcf import DCFInput, compute_dcf


def _apple_like() -> DCFInput:
    return DCFInput(
        revenue=[400_000, 430_000, 460_000, 490_000, 520_000],
        ebitda_margin=0.33,
        tax_rate=0.16,
        capex_pct=0.03,
        nwc_pct=0.02,
        wacc=0.10,
        terminal_growth=0.03,
        da_pct=0.03,
    )


def test_dcf_positive_ev():
    out = compute_dcf(_apple_like())
    assert out.enterprise_value > 0


def test_dcf_apple_range():
    out = compute_dcf(_apple_like())
    # Apple-like inputs should give EV in the multi-trillion range (USD millions)
    assert 1_000_000 < out.enterprise_value < 20_000_000, f"EV={out.enterprise_value:.0f}M"


def test_dcf_terminal_value_pct():
    out = compute_dcf(_apple_like())
    # Terminal value typically 60-85% for mature companies
    assert 0.50 < out.terminal_value_pct < 0.95


def test_dcf_higher_wacc_lower_ev():
    base = compute_dcf(_apple_like())
    high_wacc = DCFInput(**{**_apple_like().__dict__, "wacc": 0.15})
    out_high = compute_dcf(high_wacc)
    assert out_high.enterprise_value < base.enterprise_value


def test_dcf_forward_projections():
    """Revenue series must be forward-looking (increasing), not historical slice."""
    inp = DCFInput(
        revenue=[100_000, 108_000, 116_640, 125_971, 136_049],
        ebitda_margin=0.20,
        tax_rate=0.25,
        capex_pct=0.04,
        nwc_pct=0.02,
        wacc=0.10,
        terminal_growth=0.03,
    )
    out = compute_dcf(inp)
    assert out.enterprise_value > 0
    assert out.free_cash_flows[-1] > out.free_cash_flows[0], \
        "Last year FCF should exceed first (growing revenues)"
