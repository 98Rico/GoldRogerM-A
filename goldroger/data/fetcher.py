"""
Real financial data fetcher using yfinance.

For public companies: pulls verified, structured data directly from Yahoo Finance.
For private companies: returns None — caller falls back to LLM estimation.

All monetary values are stored in USD millions for consistency.
Confidence levels: "verified" (yfinance), "estimated" (LLM), "inferred" (defaults).

Results are cached in-process for 1 hour to avoid redundant network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx
import yfinance as yf

from goldroger.utils.cache import market_data_cache, ticker_cache

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
    net_debt: Optional[float] = None
    enterprise_value: Optional[float] = None    # market-implied EV

    # Income statement TTM (USD millions)
    revenue_ttm: Optional[float] = None
    ebitda_ttm: Optional[float] = None
    ebit_ttm: Optional[float] = None
    net_income_ttm: Optional[float] = None

    # Margins (0–1 decimal)
    gross_margin: Optional[float] = None
    ebitda_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # Cash flow TTM (USD millions)
    fcf_ttm: Optional[float] = None
    capex_ttm: Optional[float] = None
    da_ttm: Optional[float] = None

    # Historical annual revenue oldest-first (USD millions, up to 5 years)
    revenue_history: list[float] = field(default_factory=list)
    revenue_growth_yoy: Optional[float] = None  # most recent TTM YoY (decimal)

    # Forward / consensus estimates
    forward_revenue_growth: Optional[float] = None  # analyst forward 1Y growth
    forward_revenue_1y: Optional[float] = None      # USD millions
    earnings_growth: Optional[float] = None          # fwd EPS growth (decimal)
    forward_eps: Optional[float] = None

    # Balance sheet (for P/B valuation)
    book_value_per_share: Optional[float] = None    # total equity / shares
    total_equity: Optional[float] = None            # USD millions

    # Tax
    effective_tax_rate: Optional[float] = None

    # CAPM inputs
    beta: Optional[float] = None

    # Market-implied multiples
    ev_ebitda_market: Optional[float] = None
    ev_revenue_market: Optional[float] = None
    pe_ratio: Optional[float] = None               # trailing P/E
    forward_pe: Optional[float] = None             # forward P/E

    # Analyst consensus
    analyst_target_price: Optional[float] = None
    analyst_recommendation: Optional[str] = None

    # Cost of debt proxy
    interest_expense: Optional[float] = None        # USD millions TTM

    confidence: str = "verified"
    data_source: str = "yfinance"


def fetch_market_data(ticker: str) -> Optional[MarketData]:
    """
    Pull real structured financial data for a public company via yfinance.
    Returns None if the ticker is invalid or critical data is missing.
    Results cached for 1 hour.
    """
    key = f"md:{ticker.upper()}"
    cached = market_data_cache.get(key)
    if cached is not None:
        return cached

    result = _fetch_raw(ticker)
    if result is not None:
        market_data_cache.set(key, result)
    return result


def resolve_ticker(company_name: str) -> Optional[str]:
    """
    Resolve a company name to its primary exchange ticker via Yahoo Finance search.
    Returns the best EQUITY match or None. Cached for 24 hours.
    """
    key = f"ticker:{company_name.lower()}"
    cached = ticker_cache.get(key)
    if cached is not None:
        return cached

    result = _resolve_raw(company_name)
    if result:
        ticker_cache.set(key, result)
    return result


# ── Private implementation ────────────────────────────────────────────────────

def _fetch_raw(ticker: str) -> Optional[MarketData]:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None

        # ── Annual income statement ───────────────────────────────────────
        revenue_history: list[float] = []
        da_ttm: Optional[float] = None
        capex_ttm: Optional[float] = None
        interest_expense: Optional[float] = None
        effective_tax_rate: Optional[float] = None

        try:
            fin = stock.financials
            if fin is not None and not fin.empty:
                if "Total Revenue" in fin.index:
                    vals = fin.loc["Total Revenue"].dropna().tolist()
                    revenue_history = [v / 1e6 for v in reversed(vals[:5])]
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

        # ── Cash flow statement ───────────────────────────────────────────
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

        # ── Balance sheet ─────────────────────────────────────────────────
        total_equity: Optional[float] = None
        book_value_per_share: Optional[float] = None
        try:
            bs = stock.balance_sheet
            if bs is not None and not bs.empty:
                for eq_key in ("Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"):
                    if eq_key in bs.index:
                        total_equity = abs(bs.loc[eq_key].iloc[0]) / 1e6
                        break
        except Exception:
            pass

        bv = info.get("bookValue")
        if bv:
            book_value_per_share = float(bv)

        # ── Forward / consensus estimates ─────────────────────────────────
        forward_revenue_growth: Optional[float] = None
        forward_revenue_1y: Optional[float] = None
        try:
            rev_est = stock.revenue_estimate
            if rev_est is not None and not rev_est.empty:
                cols = rev_est.columns.tolist()
                fwd_col = None
                for candidate in ("+1y", "0y"):
                    if candidate in cols:
                        fwd_col = candidate
                        break
                if fwd_col:
                    avg = rev_est.loc["avg", fwd_col] if "avg" in rev_est.index else None
                    if avg and not _is_nan(avg):
                        forward_revenue_1y = float(avg) / 1e6
                        revenue_ttm_raw = info.get("totalRevenue")
                        if revenue_ttm_raw and revenue_ttm_raw > 0:
                            forward_revenue_growth = (forward_revenue_1y / (revenue_ttm_raw / 1e6)) - 1
        except Exception:
            pass

        # Fallback: use earningsGrowth as a proxy
        if forward_revenue_growth is None:
            eg = info.get("earningsGrowth") or info.get("revenueGrowth")
            if eg:
                forward_revenue_growth = float(eg)

        # ── Core info fields ──────────────────────────────────────────────
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

        ebitda_margin = _safe_pct(info.get("ebitdaMargins"))
        gross_margin = _safe_pct(info.get("grossMargins"))
        net_margin = _safe_pct(info.get("profitMargins"))

        if ebitda_margin is None and ebitda_ttm and revenue_ttm and revenue_ttm > 0:
            ebitda_margin = ebitda_ttm / revenue_ttm

        revenue_growth = info.get("revenueGrowth")
        beta = info.get("beta")
        ev_ebitda = info.get("enterpriseToEbitda")
        ev_revenue = info.get("enterpriseToRevenue")
        trailing_pe = info.get("trailingPE")
        fwd_pe = info.get("forwardPE")
        fwd_eps = info.get("forwardEps")
        target = info.get("targetMeanPrice")
        rec = info.get("recommendationKey")
        earnings_growth = info.get("earningsGrowth")

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
            forward_revenue_growth=forward_revenue_growth,
            forward_revenue_1y=forward_revenue_1y,
            earnings_growth=float(earnings_growth) if earnings_growth else None,
            forward_eps=float(fwd_eps) if fwd_eps else None,
            book_value_per_share=book_value_per_share,
            total_equity=total_equity,
            effective_tax_rate=effective_tax_rate,
            beta=float(beta) if beta else None,
            ev_ebitda_market=float(ev_ebitda) if ev_ebitda and ev_ebitda > 0 else None,
            ev_revenue_market=float(ev_revenue) if ev_revenue and ev_revenue > 0 else None,
            pe_ratio=float(trailing_pe) if trailing_pe and trailing_pe > 0 else None,
            forward_pe=float(fwd_pe) if fwd_pe and fwd_pe > 0 else None,
            analyst_target_price=float(target) if target else None,
            analyst_recommendation=rec,
            interest_expense=interest_expense,
        )
    except Exception as exc:
        print(f"[fetcher] Error fetching {ticker}: {exc}")
        return None


def _resolve_raw(company_name: str) -> Optional[str]:
    try:
        resp = _HTTP.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": company_name, "quotesCount": 5, "newsCount": 0},
        )
        quotes = resp.json().get("quotes", [])
        for q in quotes:
            if q.get("quoteType") in ("EQUITY", "ETF") and q.get("symbol"):
                sym = q["symbol"]
                if "." not in sym:
                    return sym
        for q in quotes:
            if q.get("symbol"):
                return q["symbol"]
        return None
    except Exception:
        return None


def _millions(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return f / 1e6 if abs(f) > 1000 else f
    except Exception:
        return None


def _safe_pct(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if f > 1.0:
            f /= 100.0
        return max(-1.0, min(f, 1.0))
    except Exception:
        return None


def _is_nan(value) -> bool:
    try:
        import math
        return math.isnan(float(value))
    except Exception:
        return True
