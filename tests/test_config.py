"""Tests that DEFAULT_CONFIG has the expected default values."""
from goldroger.config import DEFAULT_CONFIG, GoldRogerConfig


def test_config_is_singleton_type():
    assert isinstance(DEFAULT_CONFIG, GoldRogerConfig)


def test_wacc_defaults():
    w = DEFAULT_CONFIG.wacc
    assert 0.0 < w.risk_free_rate < 0.10
    assert 0.0 < w.equity_risk_premium < 0.15


def test_lbo_defaults():
    lbo = DEFAULT_CONFIG.lbo
    assert lbo.min_irr == pytest.approx(0.15)
    assert lbo.max_leverage == pytest.approx(6.5)
    assert 0.0 < lbo.fcf_sweep_rate <= 1.0
    assert lbo.mega_cap_skip_usd_bn > 0


def test_ic_score_defaults():
    ic = DEFAULT_CONFIG.ic_score
    assert ic.strong_buy_threshold > ic.buy_threshold > ic.watch_threshold > 0


def test_agent_defaults():
    ag = DEFAULT_CONFIG.agent
    assert ag.min_call_gap_s >= 0
    assert ag.max_tool_rounds >= 1
    assert ag.parallel_workers >= 1


import pytest
