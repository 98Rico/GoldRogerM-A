"""
Real financial data fetcher using yfinance.

For public companies: pulls verified, structured data directly from Yahoo Finance.
For private companies: returns None — caller falls back to LLM estimation.

All monetary values are stored in USD millions for consistency.
Confidence levels: "verified" (yfinance), "estimated" (LLM), "inferred" (computed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
import yfinance as yf

_HTTP = httpx.Client(
    timeout=15,
    headers={"User-Agent": "Mozilla/5.0"},
    follow_redirects=True,
)


@dataclass
class MarketData:
    ticker: str
    company_name: str
    sector: str

    # Price & market structure
    current_price: Optional[float] = None
    market_cap: Optional[float] = None          # USD millions
    shares_outstanding: Optional[float] = None  # millions

    # Capital structure (USD millions)
    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    net_debt: Optional[float] = None            # total_debt - cash
    enterprise_value: Optional[float] = None    # market-implied EV

    # Income statement TTM (USD millions)
    revenue_ttm: Optional[float] = None
    ebitda_ttm: Optional[float] = None
    ebit_ttm: Optional[float] = None
    net_income_ttm: Optional[float] = None

    # Margins (0–1 decimal, e.g. 0.25 = 25%)
    gross_margin: Optional[float] = None
    ebitda_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # Cash flow TTM (USD millions)
    fcf_ttm: Optional[float] = None
    capex_ttm: Optional[float] = None           # absolute, positive
    da_ttm: Optional[float] = None              # D&A for tax shield

    # Historical annual revenue, oldest-first (USD millions, up to 5 years)
    revenue_history: list[float] = field(default_factory=list)
    revenue_growth_yoy: Optional[float] = None  # most recent YoY (decimal)

    # Tax
    effective_tax_rate: Optional[float] = None  # decimal

    # CAPM inputs
    beta: Optional[float] = None

    # Market-implied multiples (from yfinance, reflect current pricing)
    ev_ebitda_market: Optional[float] = None
    ev_revenue_market: Optional[float] = None
    pe_ratio: Optional[float] = None

    # Analyst consensus
    analyst_target_price: Optional[float] = None
    analyst_recommendation: Optional[str] = None

    # Cost of debt proxy
    interest_expense: Optional[float] = None    # USD millions TTM

    confidence: str = "verified"
    data_source: str = "yfinance"


def fetch_market_data(ticker: str) -> Optional[MarketData]:
    """
    Pull real structured financial data for a public company via yfinance.
    Returns None if the ticker is invalid or critical data is missing.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None

        # ── Revenue history (annual, oldest-first) ────────────────────────
        revenue_history: list[float] = []
        da_ttm: Optional[float] = None
        capex_ttm: Optional[float] = None
        interest_expense: Optional[float] = None
        effective_tax_rate: Optional[float] = None

        try:
            fin = stock.financials  # columns = most-recent → oldest
            if fin is not None and not fin.empty:
                if "Total Revenue" in fin.index:
                    rev_vals = fin.loc["Total Revenue"].dropna().tolist()
                    revenue_history = [v / 1e6 for v in reversed(rev_vals[:5])]
                if "EBIT" in fin.index:
                    pass  # captured below via info
                if "Tax Provision" in fin.index and "Pretax Income" in fin.index:
                    tax = fin.loc["Tax Provision"].iloc[0]
                    pretax = fin.loc["Pretax Income"].iloc[0]
                    if pretax and pretax > 0:
                        effective_tax_rate = max(0.0, min(tax / pretax, 0.50))
                if "Interest Expense" in fin.index:
                    ie = fin.loc["Interest Expense"].iloc[0]
                    if ie:
                        interest_expense = abs(ie) / 1e6
        except Exception:
            pass

        try:
            cf = stock.cashflow
            if cf is not None and not cf.empty:
                for da_key in ("Depreciation And Amortization", "Depreciation"):
                    if da_key in cf.index:
                        da_ttm = abs(cf.loc[da_key].iloc[0]) / 1e6
                        break
                if "Capital Expenditure" in cf.index:
                    capex_ttm = abs(cf.loc["Capital Expenditure"].iloc[0]) / 1e6
        except Exception:
            pass

        # ── Core financials from info ──────────────────────────────────────
        revenue_ttm = _millions(info.get("totalRevenue"))
        ebitda_ttm = _millions(info.get("ebitda"))
        market_cap = _millions(info.get("marketCap"))
        enterprise_value = _millions(info.get("enterpriseValue"))
        total_debt = _millions(info.get("totalDebt"))
        cash = _millions(info.get("totalCash"))
        fcf_ttm = _millions(info.get("freeCashflow"))
        shares = _millions(info.get("sharesOutstanding"))

        net_debt: Optional[float] = None
        if total_debt is not None and cash is not None:
            net_debt = total_debt - cash

        # ── Margins (ensure 0-1 range) ─────────────────────────────────────
        ebitda_margin = _safe_pct(info.get("ebitdaMargins"))
        gross_margin = _safe_pct(info.get("grossMargins"))
        net_margin = _safe_pct(info.get("profitMargins"))

        # Derive EBITDA margin from TTM figures when info field is missing
        if ebitda_margin is None and ebitda_ttm and revenue_ttm and revenue_ttm > 0:
            ebitda_margin = ebitda_ttm / revenue_ttm

        # ── Growth ────────────────────────────────────────────────────────
        revenue_growth = info.get("revenueGrowth")  # already a decimal

        # ── CAPM & multiples ──────────────────────────────────────────────
        beta = info.get("beta")
        ev_ebitda = info.get("enterpriseToEbitda")
        ev_revenue = info.get("enterpriseToRevenue")
        pe = info.get("forwardPE") or info.get("trailingPE")
        target = info.get("targetMeanPrice")
        rec = info.get("recommendationKey")

        # ── Sanity: reject if no revenue ──────────────────────────────────
        if not revenue_ttm and not revenue_history:
            return None

        return MarketData(
            ticker=ticker.upper(),
            company_name=info.get("longName") or info.get("shortName") or ticker,
            sector=info.get("sector") or info.get("industry") or "Unknown",
            current_price=float(price),
            market_cap=market_cap,
            shares_outstanding=shares,
            total_debt=total_debt,
            cash_and_equivalents=cash,
            net_debt=net_debt,
            enterprise_value=enterprise_value,
            revenue_ttm=revenue_ttm,
            ebitda_ttm=ebitda_ttm,
            gross_margin=gross_margin,
            ebitda_margin=ebitda_margin,
            net_margin=net_margin,
            fcf_ttm=fcf_ttm,
            capex_ttm=capex_ttm,
            da_ttm=da_ttm,
            revenue_history=revenue_history,
            revenue_growth_yoy=float(revenue_growth) if revenue_growth else None,
            effective_tax_rate=effective_tax_rate,
            beta=float(beta) if beta else None,
            ev_ebitda_market=float(ev_ebitda) if ev_ebitda and ev_ebitda > 0 else None,
            ev_revenue_market=float(ev_revenue) if ev_revenue and ev_revenue > 0 else None,
            pe_ratio=float(pe) if pe and pe > 0 else None,
            analyst_target_price=float(target) if target else None,
            analyst_recommendation=rec,
            interest_expense=interest_expense,
        )
    except Exception as exc:
        print(f"[fetcher] Error fetching {ticker}: {exc}")
        return None


def resolve_ticker(company_name: str) -> Optional[str]:
    """
    Resolve a company name to its primary exchange ticker via Yahoo Finance search.
    Returns the best EQUITY match or None.
    """
    try:
        resp = _HTTP.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": company_name, "quotesCount": 5, "newsCount": 0},
        )
        quotes = resp.json().get("quotes", [])
        for q in quotes:
            if q.get("quoteType") in ("EQUITY", "ETF") and q.get("symbol"):
                sym = q["symbol"]
                # Prefer US listings — skip ADRs / foreign-listed tickers (contain dots)
                if "." not in sym:
                    return sym
        # Fallback: return first result even if foreign
        for q in quotes:
            if q.get("symbol"):
                return q["symbol"]
        return None
    except Exception:
        return None


# ── Internal helpers ───────────────────────────────────────────────────────────

def _millions(value) -> Optional[float]:
    """Convert raw Yahoo Finance value (in base units) to USD millions."""
    if value is None:
        return None
    try:
        f = float(value)
        return f / 1e6 if abs(f) > 1000 else f  # already in millions if small
    except Exception:
        return None


def _safe_pct(value) -> Optional[float]:
    """Ensure a margin/ratio is in 0-1 decimal form."""
    if value is None:
        return None
    try:
        f = float(value)
        if f > 1.0:
            f = f / 100.0
        return max(-1.0, min(f, 1.0))
    except Exception:
        return None
