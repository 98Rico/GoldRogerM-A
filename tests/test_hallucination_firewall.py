"""Tests for P0.3 hallucination firewall — _reconcile_financials()."""
from unittest.mock import MagicMock

from goldroger.data.fetcher import MarketData
from goldroger.models import Financials
from goldroger.pipelines._shared import _reconcile_financials


def _md(revenue_ttm: float | None = None, ebitda_margin: float | None = None) -> MarketData:
    return MarketData(
        ticker="TEST",
        company_name="Test Corp",
        sector="Technology",
        revenue_ttm=revenue_ttm,
        ebitda_margin=ebitda_margin,
        confidence="verified",
        data_source="yfinance",
    )


def _fin(revenue: str = "100.0", margin: str = "0.20") -> Financials:
    return Financials(revenue_current=revenue, ebitda_margin=margin)


# ── No-op cases ──────────────────────────────────────────────────────────────

def test_no_market_data_returns_unchanged():
    fin = _fin(revenue="150.0")
    result = _reconcile_financials(fin, market_data=None)
    assert result.revenue_current == "150.0"


def test_market_data_no_revenue_leaves_fin_unchanged():
    fin = _fin(revenue="150.0")
    result = _reconcile_financials(fin, _md(revenue_ttm=None))
    assert result.revenue_current == "150.0"


# ── Revenue override ──────────────────────────────────────────────────────────

def test_registry_revenue_overrides_llm():
    fin = _fin(revenue="100.0")
    result = _reconcile_financials(fin, _md(revenue_ttm=200.0))
    assert result.revenue_current == "200.0"


def test_revenue_override_when_llm_matches_registry():
    """Even when LLM is close, registry value is used (no divergence warning)."""
    fin = _fin(revenue="199.0")
    result = _reconcile_financials(fin, _md(revenue_ttm=200.0))
    assert result.revenue_current == "200.0"


def test_revenue_override_logs_large_discrepancy():
    mock_console = MagicMock()
    fin = _fin(revenue="50.0")
    _reconcile_financials(fin, _md(revenue_ttm=200.0), console=mock_console)
    mock_console.print.assert_called_once()
    msg = mock_console.print.call_args[0][0]
    assert "revenue" in msg
    assert "50" in msg
    assert "200" in msg


def test_revenue_no_log_when_discrepancy_small():
    """Less than 20% delta — override happens silently."""
    mock_console = MagicMock()
    fin = _fin(revenue="195.0")
    _reconcile_financials(fin, _md(revenue_ttm=200.0), console=mock_console)
    mock_console.print.assert_not_called()


def test_revenue_override_when_fin_revenue_missing():
    fin = _fin(revenue="")
    result = _reconcile_financials(fin, _md(revenue_ttm=300.0))
    assert result.revenue_current == "300.0"


def test_revenue_override_when_fin_revenue_bad_string():
    fin = _fin(revenue="N/A")
    result = _reconcile_financials(fin, _md(revenue_ttm=300.0))
    assert result.revenue_current == "300.0"


# ── EBITDA margin override ────────────────────────────────────────────────────

def test_ebitda_margin_overrides_llm():
    fin = _fin(margin="0.30")
    result = _reconcile_financials(fin, _md(revenue_ttm=100.0, ebitda_margin=0.20))
    assert result.ebitda_margin == "0.2"


def test_ebitda_margin_no_market_data_leaves_unchanged():
    fin = _fin(margin="0.30")
    result = _reconcile_financials(fin, _md(revenue_ttm=100.0, ebitda_margin=None))
    assert result.ebitda_margin == "0.30"


def test_ebitda_margin_logs_large_discrepancy():
    mock_console = MagicMock()
    fin = _fin(revenue="100.0", margin="0.50")
    _reconcile_financials(fin, _md(revenue_ttm=100.0, ebitda_margin=0.15), console=mock_console)
    mock_console.print.assert_called_once()
    msg = mock_console.print.call_args[0][0]
    assert "ebitda_margin" in msg


def test_both_overrides_in_single_log_line():
    """When both revenue and margin differ significantly, one log line covers both."""
    mock_console = MagicMock()
    fin = _fin(revenue="50.0", margin="0.50")
    _reconcile_financials(fin, _md(revenue_ttm=200.0, ebitda_margin=0.15), console=mock_console)
    assert mock_console.print.call_count == 1
    msg = mock_console.print.call_args[0][0]
    assert "revenue" in msg
    assert "ebitda_margin" in msg


# ── Return value ─────────────────────────────────────────────────────────────

def test_returns_same_fin_object():
    fin = _fin(revenue="100.0")
    result = _reconcile_financials(fin, _md(revenue_ttm=100.0))
    assert result is fin
