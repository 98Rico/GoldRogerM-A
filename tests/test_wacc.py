from goldroger.finance.core.wacc import compute_capm_wacc, capm_cost_of_equity


def test_capm_beta_one():
    re = capm_cost_of_equity(beta=1.0)
    assert abs(re - 0.105) < 0.001, f"Expected ~10.5%, got {re:.3%}"


def test_capm_high_beta():
    re = capm_cost_of_equity(beta=2.0)
    assert abs(re - 0.165) < 0.001, f"Expected ~16.5%, got {re:.3%}"


def test_wacc_equity_only():
    wacc = compute_capm_wacc(beta=1.0, market_cap=1000.0, net_debt=0.0, tax_rate=0.25)
    assert abs(wacc - 0.105) < 0.001, f"Expected ~10.5% (equity-only), got {wacc:.3%}"


def test_wacc_with_debt():
    wacc = compute_capm_wacc(
        beta=1.0, market_cap=800.0, net_debt=200.0, tax_rate=0.25,
        interest_expense=10.0, total_debt=200.0,
    )
    assert 0.07 < wacc < 0.10, f"WACC with debt should be lower: {wacc:.3%}"


def test_wacc_clamped():
    wacc = compute_capm_wacc(beta=10.0, market_cap=100.0, net_debt=0.0, tax_rate=0.25)
    assert wacc <= 0.25, "WACC must be clamped at 25%"
