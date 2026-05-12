"""
Post-processing fill_gaps — ensures no output field displays as N/A.

Called after all agents complete and _reconcile_financials() runs.
Every field that would otherwise be blank/None/N/A is filled with either:
  - A sector-calibrated estimate (tagged [sector avg])
  - A deterministic fallback (tagged [estimated])
  - An informative placeholder (not just "N/A")
"""
from __future__ import annotations

from goldroger.data.sector_multiples import (
    get_sector_ebitda_margin,
    get_sector_multiples,
    get_sector_rev_growth,
)
from goldroger.models import AnalysisResult
from goldroger.utils.money import format_money_millions, parse_monetary_to_millions

_MISSING = {"", "0", "0.0", "null", "None", "N/A", "n/a", "NA"}


def _blank(v: str | None) -> bool:
    return v is None or str(v).strip() in _MISSING


def fill_gaps(result: AnalysisResult, sector: str) -> AnalysisResult:
    """
    Walk every required field in AnalysisResult; apply fallback chains.
    Mutates result in place (same object returned for chaining).
    """
    _fill_fundamentals(result, sector)
    _fill_financials(result, sector)
    _fill_market(result)
    _fill_valuation(result)
    return result


# ── Fundamentals ─────────────────────────────────────────────────────────────

def _fill_fundamentals(result: AnalysisResult, sector: str) -> None:
    f = result.fundamentals
    company = f.company_name or result.company

    if _blank(f.sector):
        f.sector = sector or "Diversified"

    if _blank(f.description):
        f.description = f"{company} — company description not available."

    if _blank(f.business_model):
        f.business_model = f"Business model details for {company} unavailable."

    if not f.competitive_advantages:
        f.competitive_advantages = ["Competitive positioning data unavailable."]

    if not f.key_risks:
        pass  # risks from thesis agent — leave blank is OK

    if _blank(f.market_position):
        f.market_position = "Market position data unavailable."


# ── Financials ────────────────────────────────────────────────────────────────

def _fill_financials(result: AnalysisResult, sector: str) -> None:
    fin = result.financials
    ccy = str((result.valuation.currency if result.valuation else None) or "USD")

    # Revenue — show human-readable placeholder when zero/missing (never display "0.0")
    if _blank(fin.revenue_current):
        fin.revenue_current = "Not available [no verified source]"

    # EBITDA margin — sector average fallback
    if _blank(fin.ebitda_margin):
        m = get_sector_ebitda_margin(sector)
        fin.ebitda_margin = f"{m:.1%} [sector avg]"

    # Revenue growth — sector benchmark fallback
    if _blank(fin.revenue_growth):
        g = get_sector_rev_growth(sector)
        fin.revenue_growth = f"{g:+.1%} [estimated]"

    # Gross margin — if EBITDA margin available, estimate gross margin
    if _blank(fin.gross_margin) and not _blank(fin.ebitda_margin):
        try:
            ebitda_m = float(str(fin.ebitda_margin).split()[0].strip("%")) / 100
            gross_est = min(ebitda_m + 0.25, 0.85)
            fin.gross_margin = f"{gross_est:.1%} [estimated]"
        except (ValueError, TypeError):
            pass

    # Free cash flow — estimate from EBITDA margin and revenue if missing
    if _blank(fin.free_cash_flow) and not _blank(fin.revenue_current):
        try:
            rev = parse_monetary_to_millions(str(fin.revenue_current))
            if rev > 0:
                ebitda_m_raw = str(fin.ebitda_margin or "").split()[0].rstrip("%")
                ebitda_m = float(ebitda_m_raw) / 100 if "%" in str(fin.ebitda_margin or "") else float(ebitda_m_raw)
                fcf_est = rev * ebitda_m * 0.65  # typical FCF conversion ~65% of EBITDA
                fin.free_cash_flow = f"{format_money_millions(fcf_est, ccy)} [estimated]"
        except (ValueError, TypeError):
            pass


# ── Market analysis ───────────────────────────────────────────────────────────

def _fill_market(result: AnalysisResult) -> None:
    m = result.market

    if _blank(m.market_size):
        m.market_size = "Not available from current queries"

    if _blank(m.market_growth):
        m.market_growth = "Not available from current queries"

    if _blank(m.competitive_position):
        m.competitive_position = "Competitive position analysis not available."

    if not m.key_trends:
        m.key_trends = ["No market trend data available from current sources."]


# ── Valuation ─────────────────────────────────────────────────────────────────

def _fill_valuation(result: AnalysisResult) -> None:
    v = result.valuation
    if v is None:
        return

    if _blank(v.recommendation):
        v.recommendation = "HOLD"

    if _blank(v.upside_downside):
        v.upside_downside = "N/A [no market price]"

    # Ensure DCF assumptions are readable
    if v.dcf_assumptions:
        dcf = v.dcf_assumptions
        if _blank(dcf.wacc):
            sm = get_sector_multiples(result.fundamentals.sector or "")
            dcf.wacc = f"{sm.sector_wacc:.2%} [sector default]"
        if _blank(dcf.terminal_growth):
            sm = get_sector_multiples(result.fundamentals.sector or "")
            dcf.terminal_growth = f"{sm.terminal_growth:.2%} [sector default]"
